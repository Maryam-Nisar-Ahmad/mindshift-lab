"""
Expanding-window proportional alignment.

Architecture
------------
Each segment is aligned INDEPENDENTLY of every other.  There is no moving
cursor.  Every segment searches in a symmetric window centred on its own
proportional-time estimate.  Three nested windows are tried in order:

  Stage 1 – ±2 s  (very tight)   min_match = 25 %
  Stage 2 – ±6 s  (tight)        min_match = 20 %
  Stage 3 – ±20 s (wide)         min_match = 15 %

Trying the tightest window FIRST means we always prefer the audio position
closest to the proportional estimate.  This eliminates the false-far-match
bug (e.g. Scene 6 matching at 50 s when expected at 21 s) and the
image-before-audio bug (where the next segment accidentally matched to the
tail of the previous segment's audio because the cursor sat right there).

A cursor-based lower bound is deliberately ABSENT.  The only lower bound is
"the audio starts at 0 s", enforced by max(0, prop_time - slack).  Any
temporal ordering violations are corrected by the post-processing monotonicity
pass.  Remaining unmatched segments are filled by anchor interpolation.

Why no cursor?
  The cursor-advance logic caused cascading errors:
    • Under-advance (old)  → next segment searched inside current segment's
                            unmatched audio tail → image appeared 1-2 s early.
    • Over-advance (new)   → next segment's window started past its true
                            position → image appeared 10+ s late, making the
                            entire video out of sync after scene 5.
  Without a cursor, neither failure mode exists.  Each segment's fate depends
  only on its own proportional time and the audio around it.
"""

from __future__ import annotations

import bisect
import re
from functools import lru_cache

from rapidfuzz import fuzz

from .word_timing import refine_word_timestamps


# ── Window sizes (seconds on each side of the proportional estimate) ──────────
_SLACK_1 = 2.0    # Stage 1: very tight — catches constant-pace narration
_SLACK_2 = 6.0    # Stage 2: tight    — ≈ 20 % pace variation
_SLACK_3 = 20.0   # Stage 3: wide     — handles pauses / improvisation

_RATIO_1 = 0.25   # require 25 % word match in ±2 s window
_RATIO_2 = 0.20   # require 20 % in ±6 s window
_RATIO_3 = 0.15   # require 15 % in ±20 s window

_STAGES = [
    (_SLACK_1, _RATIO_1, "nw_tight"),
    (_SLACK_2, _RATIO_2, "nw_primary"),
    (_SLACK_3, _RATIO_3, "nw_secondary"),
]


# ── Token helpers ─────────────────────────────────────────────────────────────

def _norm_token(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"[^\w\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=131072)
def _pair_score(a: str, b: str) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return -6.0
    if a == b:
        return 18.0
    r = fuzz.ratio(a, b)
    if r >= 96:
        return 15.0
    if r >= 88:
        return 12.0
    if r >= 80:
        return 9.0
    if r >= 72:
        return 5.0
    if r >= 62:
        return 1.0
    return -8.0


# ── Needleman–Wunsch ──────────────────────────────────────────────────────────

def _nw_align(script_tokens: list[str], asr_tokens: list[str]) -> list[int | None]:
    """Global NW alignment of script_tokens onto asr_tokens.
    Returns a list[int | None] of length len(script_tokens):
    the ASR index each script token aligns to (or None if gapped).
    """
    S, W = len(script_tokens), len(asr_tokens)
    if S == 0:
        return []
    if W == 0:
        return [None] * S

    sn = [_norm_token(t) for t in script_tokens]
    an = [_norm_token(t) for t in asr_tokens]
    gap = -5.0
    neg_inf = -1e18

    dp = [[neg_inf] * (W + 1) for _ in range(S + 1)]
    back = [[-1] * (W + 1) for _ in range(S + 1)]
    dp[0][0] = 0.0
    for i in range(1, S + 1):
        dp[i][0] = dp[i - 1][0] + gap
        back[i][0] = 1
    for j in range(1, W + 1):
        dp[0][j] = dp[0][j - 1] + gap
        back[0][j] = 2

    for i in range(1, S + 1):
        for j in range(1, W + 1):
            sc = _pair_score(sn[i - 1], an[j - 1])
            best = dp[i - 1][j - 1] + sc
            bt = 0
            v = dp[i - 1][j] + gap
            if v > best:
                best = v; bt = 1
            v2 = dp[i][j - 1] + gap
            if v2 > best:
                best = v2; bt = 2
            dp[i][j] = best
            back[i][j] = bt

    mapping: list[int | None] = [None] * S
    i, j = S, W
    while i > 0 or j > 0:
        if i > 0 and j > 0 and back[i][j] == 0:
            mapping[i - 1] = j - 1
            i -= 1; j -= 1
        elif i > 0 and (j == 0 or back[i][j] == 1):
            mapping[i - 1] = None
            i -= 1
        else:
            j -= 1
    return mapping


def _local_nw(
    seg_words: list[str],
    words: list[dict],
    w_from: int,
    w_to: int,
    min_ratio: float,
) -> tuple[float, bool, float]:
    """
    Run NW of seg_words against words[w_from:w_to].
    Returns (start_time, matched, ratio_pct).
    """
    w_to = min(w_to, len(words))
    if not seg_words or w_from >= w_to:
        return 0.0, False, 0.0

    window = words[w_from:w_to]
    win_tokens = [w.get("word", "") for w in window]
    mapping = _nw_align(seg_words, win_tokens)
    matched = [j for j in mapping if j is not None]

    if not matched:
        return float(words[w_from].get("start", 0)), False, 0.0

    ratio = len(matched) / max(len(seg_words), 1)
    if ratio < min_ratio:
        return float(words[w_from].get("start", 0)), False, round(ratio * 100, 2)

    first = w_from + min(matched)
    return (
        round(float(words[first].get("start", 0)), 3),
        True,
        round(ratio * 100, 2),
    )


# ── Anchor interpolation ──────────────────────────────────────────────────────

def _interpolate_unmatched(results: list[dict], total_duration: float) -> None:
    """
    Linearly interpolate the start times of unmatched/proportional segments
    between the nearest matched neighbours.
    """
    n = len(results)
    for i in range(n):
        if results[i].get("matched"):
            continue
        # Find enclosing matched anchors
        prev_t = 0.0
        prev_i = -1
        for j in range(i - 1, -1, -1):
            if results[j].get("matched"):
                prev_t = results[j]["start_time"]
                prev_i = j
                break
        next_t = total_duration
        next_i = n
        for j in range(i + 1, n):
            if results[j].get("matched"):
                next_t = results[j]["start_time"]
                next_i = j
                break
        gap = max(next_i - prev_i, 1)
        pos = i - prev_i
        results[i]["start_time"] = round(prev_t + (next_t - prev_t) * pos / gap, 3)
        results[i]["method"] = "interpolated"


# ── Main entry point ──────────────────────────────────────────────────────────

def compute_segment_starts(
    mj_data: list,
    audio_words_raw: list[dict],
    total_duration: float,
) -> list[dict]:
    """
    Returns one dict per segment:
    { segment_id, start_time, matched, match_ratio, method }
    """
    words = refine_word_timestamps([dict(w) for w in audio_words_raw]) if audio_words_raw else []

    if not words:
        return [
            {
                "segment_id": seg.get("segment_id", 0),
                "start_time": round(
                    (i / max(len(mj_data), 1)) * total_duration if total_duration else 0.0, 3
                ),
                "matched": False,
                "match_ratio": 0.0,
                "method": "empty_asr",
            }
            for i, seg in enumerate(mj_data)
        ]

    # Build a sorted list of word start times for O(log n) time → word-index lookups.
    word_starts: list[float] = [float(w.get("start", 0) or 0) for w in words]

    # Cumulative script-word offsets for proportional time estimates.
    seg_word_offsets: list[int] = []
    cumulative = 0
    for seg in mj_data:
        seg_word_offsets.append(cumulative)
        cumulative += len((seg.get("segment_text") or "").strip().split())
    total_script_words = max(cumulative, 1)

    results: list[dict] = []

    for row_idx, seg in enumerate(mj_data):
        segment_id = seg.get("segment_id", 0)
        seg_words = (seg.get("segment_text") or "").strip().split()

        # Proportional time: where in the audio this segment should roughly start.
        prop_time = (seg_word_offsets[row_idx] / total_script_words) * total_duration

        if not seg_words:
            results.append({
                "segment_id": segment_id,
                "start_time": round(prop_time, 3),
                "matched": False,
                "match_ratio": 0.0,
                "method": "empty_segment_text",
            })
            continue

        # ── Try expanding windows, tightest first ────────────────────────────
        # Each window is a symmetric ±slack interval around prop_time.
        # NO cursor lower bound — each segment is searched independently.
        # This eliminates cascading cursor errors that caused the whole-video
        # desync (images 10+ s late after scene 5).
        found = False
        for slack, min_ratio, method in _STAGES:
            t_from = max(0.0, prop_time - slack)
            t_to = min(total_duration, prop_time + slack)
            if t_from >= t_to:
                continue

            w_from = bisect.bisect_left(word_starts, t_from)
            # Include seg_words extra slots so the last word can match.
            w_to = min(len(words), bisect.bisect_right(word_starts, t_to) + len(seg_words))
            if w_from >= w_to:
                continue

            st, ok, ratio = _local_nw(seg_words, words, w_from, w_to, min_ratio)
            if ok:
                results.append({
                    "segment_id": segment_id,
                    "start_time": st,
                    "matched": True,
                    "match_ratio": round(ratio, 2),
                    "method": method,
                })
                found = True
                break

        if not found:
            # All windows failed: use proportional estimate directly.
            results.append({
                "segment_id": segment_id,
                "start_time": round(prop_time, 3),
                "matched": False,
                "match_ratio": 0.0,
                "method": "proportional_fallback",
            })

    # ── Post-pass 1: enforce non-decreasing start times ──────────────────────
    for i in range(1, len(results)):
        if results[i]["start_time"] <= results[i - 1]["start_time"]:
            results[i]["start_time"] = round(results[i - 1]["start_time"] + 0.05, 3)

    # ── Post-pass 2: anchor interpolation for unmatched segments ─────────────
    _interpolate_unmatched(results, total_duration)

    # ── Post-pass 3: final monotonicity after interpolation ──────────────────
    for i in range(1, len(results)):
        if results[i]["start_time"] <= results[i - 1]["start_time"]:
            results[i]["start_time"] = round(results[i - 1]["start_time"] + 0.05, 3)

    return results
