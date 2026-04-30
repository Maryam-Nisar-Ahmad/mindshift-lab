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

from .word_timing import load_alignment_word_list


# ── Window sizes (seconds on each side of the proportional estimate) ──────────
_SLACK_1 = 2.0    # Stage 1: very tight — catches constant-pace narration
_SLACK_2 = 6.0    # Stage 2: tight    — ≈ 20 % pace variation
_SLACK_3 = 20.0   # Stage 3: wide     — handles pauses / improvisation
_SLACK_4 = 55.0   # Stage 4: very wide — long narrations / uneven pacing (last resort)

_RATIO_1 = 0.25   # require 25 % word match in ±2 s window
_RATIO_2 = 0.20   # require 20 % in ±6 s window
_RATIO_3 = 0.13   # require 13 % in ±20 s window (slightly looser when pacing varies)
_RATIO_4 = 0.095   # wide window last resort — uneven narration / LLM script drift

_STAGES = [
    (_SLACK_1, _RATIO_1, "nw_tight"),
    (_SLACK_2, _RATIO_2, "nw_primary"),
    (_SLACK_3, _RATIO_3, "nw_secondary"),
    (_SLACK_4, _RATIO_4, "nw_wide_loose"),
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


def _collision_repair_pass(
    results: list[dict],
    mj_data: list,
    words: list[dict],
    word_starts: list[float],
    total_duration: float,
    blended_prop: list[float],
) -> None:
    """
    Independent NW passes can anchor two scenes on the same syllable when dialogue is dense.
    Re-search later scene with window forced *after* the previous start.
    """
    n = len(results)
    if n < 2:
        return
    gap_floor = 0.17
    for i in range(1, n):
        prev_t = float(results[i - 1]["start_time"])
        cur_t = float(results[i]["start_time"])
        if cur_t - prev_t >= gap_floor:
            continue
        seg = mj_data[i]
        seg_words = (seg.get("segment_text") or "").strip().split()
        if not seg_words:
            continue
        prop_time = blended_prop[i] if i < len(blended_prop) else cur_t
        min_audio = prev_t + 0.07
        t_from = max(min_audio, prop_time - _SLACK_2)
        t_to = min(total_duration, prop_time + _SLACK_3 + 5.0)
        if t_from >= t_to - 0.05:
            t_to = min(total_duration, min_audio + 25.0)
        w_from = bisect.bisect_left(word_starts, t_from)
        w_to = min(
            len(words),
            bisect.bisect_right(word_starts, t_to) + max(len(seg_words) * 2, 10),
        )
        if w_from >= w_to:
            continue
        st, ok, ratio = _local_nw(seg_words, words, w_from, w_to, 0.095, prop_time)
        if ok and st >= min_audio + 0.02:
            results[i]["start_time"] = round(st, 3)
            results[i]["matched"] = True
            results[i]["match_ratio"] = round(ratio, 2)
            results[i]["method"] = "collision_repair"


def _anchor_scenes_to_first_script_word(
    results: list[dict],
    mj_data: list,
    words: list[dict],
    word_starts: list[float],
    total_duration: float,
) -> None:
    """
    Snap scene starts toward the ASR timestamp of each segment's first content word.
    Cuts that landed mid-pause or on the wrong repetition track speech onset better.
    """
    n = len(results)
    if n == 0:
        return
    for i in range(n):
        raw_text = mj_data[i].get("segment_text") or ""
        toks = re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?", raw_text)
        if not toks:
            continue
        target = toks[0].lower()
        prev_lo = float(results[i - 1]["start_time"]) + 0.06 if i > 0 else 0.0
        prop = float(results[i]["start_time"])
        scan_hi = min(total_duration, prop + 18.0)
        wi0 = bisect.bisect_left(word_starts, max(0.0, prev_lo - 0.4))
        best_ts = None
        best_sc = 0.0
        for wi in range(wi0, min(len(words), wi0 + 160)):
            wt = float(words[wi].get("start", 0) or 0)
            if wt > scan_hi:
                break
            if wt + 0.02 < prev_lo:
                continue
            aw = _norm_token(words[wi].get("word", "") or "")
            if len(aw) < 2:
                continue
            sc = float(max(fuzz.ratio(target, aw), fuzz.partial_ratio(target, aw)))
            if sc > best_sc:
                best_sc = sc
                best_ts = wt
        if best_ts is None or best_sc < 78.0:
            continue
        if i + 1 < n and best_ts >= float(results[i + 1]["start_time"]) - 0.12:
            continue
        if abs(best_ts - prop) > 5.5:
            continue
        if best_sc < 88.0 and abs(best_ts - prop) > 0.9:
            continue
        if best_ts < prev_lo - 1e-6:
            continue
        results[i]["start_time"] = round(best_ts, 3)
        results[i]["matched"] = True
        results[i]["match_ratio"] = round(min(100.0, best_sc), 2)
        results[i]["method"] = "first_word_anchor"


def _segment_char_offsets(mj_data: list) -> tuple[list[int], int]:
    """Cumulative non-whitespace character counts for proportional pacing."""
    offsets: list[int] = []
    c = 0
    for seg in mj_data:
        offsets.append(c)
        compact = re.sub(r"\s+", "", seg.get("segment_text") or "")
        c += max(len(compact), 1)
    return offsets, max(c, 1)


def _count_script_words(text: str) -> int:
    """
    Count word-like tokens in script text for proportional timing.
    Uses alphanumeric runs so punctuation-heavy scripts match ASR token density better
    than naive ``split()`` word counts.
    """
    raw = text or ""
    toks = re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?", raw)
    if toks:
        return len(toks)
    # Fallback: whitespace chunks (e.g. numeric-only lines)
    parts = raw.strip().split()
    return len(parts) if parts else 0


def _local_nw(
    seg_words: list[str],
    words: list[dict],
    w_from: int,
    w_to: int,
    min_ratio: float,
    fallback_time: float,
) -> tuple[float, bool, float]:
    """
    Run NW of seg_words against words[w_from:w_to].
    Returns (start_time, matched, ratio_pct).

    On low-confidence alignment, returns ``fallback_time`` (proportional estimate),
    not the window edge — avoiding arbitrary anchors far from the true speech time.
    """
    w_to = min(w_to, len(words))
    if not seg_words or w_from >= w_to:
        return round(float(fallback_time or 0), 3), False, 0.0

    window = words[w_from:w_to]
    win_tokens = [w.get("word", "") for w in window]
    mapping = _nw_align(seg_words, win_tokens)
    matched = [j for j in mapping if j is not None]
    if not matched:
        return round(float(fallback_time or 0), 3), False, 0.0

    # Count only semantically strong token pairs; NW can map weak pairs just to avoid gaps.
    strong_pairs: list[tuple[int, int]] = []
    for i, j in enumerate(mapping):
        if j is None:
            continue
        try:
            sc = _pair_score(_norm_token(seg_words[i]), _norm_token(win_tokens[j]))
        except Exception:
            sc = -8.0
        # >=5 roughly means token similarity is at least decent ("close enough" wording).
        if sc >= 5.0:
            strong_pairs.append((i, j))

    weak_hits = 0
    for i, j in enumerate(mapping):
        if j is None:
            continue
        try:
            sc = _pair_score(_norm_token(seg_words[i]), _norm_token(win_tokens[j]))
        except Exception:
            sc = -8.0
        if 1.0 <= sc < 5.0:
            weak_hits += 1
    # Partial phonetic / typo hits help acceptance without counting as full NW tokens.
    ratio = (len(strong_pairs) + 0.38 * weak_hits) / max(len(seg_words), 1)
    if ratio < min_ratio:
        return round(float(fallback_time or 0), 3), False, round(ratio * 100, 2)

    # Anchor segment start to first strong mapped token (fallback to any mapped token).
    first_local = strong_pairs[0][1] if strong_pairs else min(matched)
    first = w_from + first_local
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
    words = load_alignment_word_list(audio_words_raw)

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
        cumulative += _count_script_words(seg.get("segment_text") or "")
    total_script_words = max(cumulative, 1)

    seg_char_offsets, total_script_chars = _segment_char_offsets(mj_data)
    blended_prop: list[float] = []
    for idx in range(len(mj_data)):
        wt_prop = (seg_word_offsets[idx] / total_script_words) * total_duration
        ct_prop = (seg_char_offsets[idx] / total_script_chars) * total_duration
        blended_prop.append(round(0.52 * wt_prop + 0.48 * ct_prop, 5))

    results: list[dict] = []

    for row_idx, seg in enumerate(mj_data):
        segment_id = seg.get("segment_id", 0)
        seg_words = (seg.get("segment_text") or "").strip().split()

        # Blend word-based and character-based pacing so long phrases shift proportionally vs ASR density.
        prop_time = blended_prop[row_idx]

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
            w_to = min(
                len(words),
                bisect.bisect_right(word_starts, t_to) + max(len(seg_words) * 2, 8),
            )
            if w_from >= w_to:
                continue

            st, ok, ratio = _local_nw(seg_words, words, w_from, w_to, min_ratio, prop_time)
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

    # ── Post-pass 4: de-collision (stacked scene starts on the same syllable) ─
    _collision_repair_pass(results, mj_data, words, word_starts, total_duration, blended_prop)

    for i in range(1, len(results)):
        if results[i]["start_time"] <= results[i - 1]["start_time"]:
            results[i]["start_time"] = round(results[i - 1]["start_time"] + 0.05, 3)

    # ── Post-pass 5: align each scene cut with first spoken word of that MJ prompt ─
    _anchor_scenes_to_first_script_word(results, mj_data, words, word_starts, total_duration)

    for i in range(1, len(results)):
        if results[i]["start_time"] <= results[i - 1]["start_time"]:
            results[i]["start_time"] = round(results[i - 1]["start_time"] + 0.05, 3)

    return results
