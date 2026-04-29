"""
Time-first segment alignment.

Core insight
------------
Previous versions searched ±200 WORDS around the proportional estimate.  For a
14-minute / 2 156-word audio that window spans ≈78 seconds — wide enough for the
NW to accidentally match Scene 6 at audio time 50 s instead of the correct 21 s,
causing a 44-second duration gap and all downstream scenes being wrong.

Professional tools (CapCut, Descript, Kapwing) use TIME-proximity as the primary
constraint and text as a refinement:

  1. PRIMARY  – NW inside ± TIME_SLACK_PRIMARY (±6 s) of the proportional estimate.
               A 12-second window cannot produce false matches far from the expected
               position.  min_match_ratio is lowered to 0.15 because the tight window
               already prevents false positives.

  2. SECONDARY – NW inside ± TIME_SLACK_SECONDARY (±20 s) for segments where the
                narrator paused, repeated, or deviated from the exact script pace.

  3. FALLBACK  – use the proportional time directly (accurate to ≈ ±1–3 s for a
               constant-pace narration).  Far better than the old "uniform over the
               whole video" approach.

  4. ANCHOR INTERPOLATION – any remaining unmatched segments are placed on a line
                            between the two nearest matched neighbours.

All positions are monotonically non-decreasing; last_matched_end_time (in seconds)
is used as the lower bound so that the time cursor automatically advances even
between successful matches.
"""

from __future__ import annotations

import bisect
import re
from functools import lru_cache
from rapidfuzz import fuzz

from .word_timing import refine_word_timestamps


# ── Tuning constants ──────────────────────────────────────────────────────────
# Tight primary window: prevents false matches far from the expected position.
TIME_SLACK_PRIMARY = 6.0      # seconds on each side
TIME_SLACK_SECONDARY = 20.0   # seconds on each side (wider fallback)
MIN_MATCH_RATIO = 0.15        # fraction of segment words that must match in NW
                               # (lower than before; tight window makes it safe)


# ── Token helpers ─────────────────────────────────────────────────────────────

def _norm_token(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


@lru_cache(maxsize=131072)
def _pair_score_normed(a: str, b: str) -> float:
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


# ── Needleman–Wunsch (used for small windows only) ───────────────────────────

def _nw_align_script_to_asr(
    script_tokens: list[str],
    asr_tokens: list[str],
    gap_script: float = -5.0,
    gap_asr: float = -5.0,
) -> list[int | None]:
    S, W = len(script_tokens), len(asr_tokens)
    if S == 0:
        return []
    if W == 0:
        return [None] * S

    sn = [_norm_token(t) for t in script_tokens]
    an = [_norm_token(t) for t in asr_tokens]

    neg_inf = -1e18
    dp: list[list[float]] = [[neg_inf] * (W + 1) for _ in range(S + 1)]
    back: list[list[int]] = [[-1] * (W + 1) for _ in range(S + 1)]
    dp[0][0] = 0.0

    for i in range(1, S + 1):
        dp[i][0] = dp[i - 1][0] + gap_script
        back[i][0] = 1
    for j in range(1, W + 1):
        dp[0][j] = dp[0][j - 1] + gap_asr
        back[0][j] = 2

    for i in range(1, S + 1):
        for j in range(1, W + 1):
            sc = _pair_score_normed(sn[i - 1], an[j - 1])
            best = dp[i - 1][j - 1] + sc
            bt = 0
            v = dp[i - 1][j] + gap_script
            if v > best:
                best = v
                bt = 1
            v2 = dp[i][j - 1] + gap_asr
            if v2 > best:
                best = v2
                bt = 2
            dp[i][j] = best
            back[i][j] = bt

    match_asr_idx: list[int | None] = [None] * S
    i, j = S, W
    while i > 0 or j > 0:
        if i > 0 and j > 0 and back[i][j] == 0:
            match_asr_idx[i - 1] = j - 1
            i -= 1
            j -= 1
        elif i > 0 and (j == 0 or back[i][j] == 1):
            match_asr_idx[i - 1] = None
            i -= 1
        else:
            j -= 1
    return match_asr_idx


# ── Per-segment local NW ──────────────────────────────────────────────────────

def _segment_local_nw(
    seg_words: list[str],
    words: list[dict],
    search_from: int,
    search_to: int,
    *,
    min_match_ratio: float = MIN_MATCH_RATIO,
) -> tuple[float, bool, float, int]:
    """
    Run NW between the full segment text and words[search_from:search_to].
    Returns (start_time, matched, match_ratio_pct, end_pos_exclusive).
    """
    search_to = min(search_to, len(words))
    if not seg_words or not words or search_from >= search_to:
        return 0.0, False, 0.0, search_from

    window = words[search_from:search_to]
    win_tokens = [w.get("word", "") for w in window]
    match_idx = _nw_align_script_to_asr(seg_words, win_tokens)
    matched_local = [j for j in match_idx if j is not None]

    if not matched_local:
        return float(words[search_from]["start"]), False, 0.0, search_from

    ratio = len(matched_local) / max(len(seg_words), 1)
    if ratio < min_match_ratio:
        return (
            float(words[search_from]["start"]),
            False,
            round(ratio * 100, 2),
            search_from,
        )

    actual_first = search_from + min(matched_local)
    actual_last = search_from + max(matched_local)
    return (
        round(float(words[actual_first]["start"]), 3),
        True,
        round(ratio * 100, 2),
        min(actual_last + 1, len(words)),
    )


# ── Time window helper ────────────────────────────────────────────────────────

def _try_time_window(
    seg_words: list[str],
    words: list[dict],
    word_starts: list[float],
    last_end_time: float,
    prop_time: float,
    t_slack: float,
    total_duration: float,
    method_name: str,
    *,
    use_cursor_lower_bound: bool = True,
) -> tuple[float, bool, float, float, str] | None:
    """
    Attempt NW alignment within [max(last_end_time*, prop_time-t_slack),
    prop_time+t_slack] converted to word indices.

    *When use_cursor_lower_bound=False the cursor is ignored, which is
    appropriate for the wider secondary pass so that an accumulated cursor
    error cannot produce an empty search window for end-of-video scenes.

    Returns (start_time, matched, ratio_pct, matched_end_time, method) or None.
    """
    raw_from = prop_time - t_slack
    if use_cursor_lower_bound:
        t_from = max(last_end_time, raw_from)
    else:
        t_from = raw_from
    t_to = min(total_duration, prop_time + t_slack)

    if t_from >= t_to:
        return None

    w_from = bisect.bisect_left(word_starts, t_from)
    # Include seg_words extra positions so the last word has space to match.
    w_to = min(len(words), bisect.bisect_right(word_starts, t_to) + len(seg_words))

    if w_from >= w_to:
        return None

    st, ok, ratio, end_pos = _segment_local_nw(seg_words, words, w_from, w_to)
    if not ok:
        return None

    # end_time = end of the last matched word (advance the cursor past it).
    last_idx = min(end_pos - 1, len(words) - 1)
    end_time = float(words[last_idx]["end"]) if last_idx >= 0 else st
    return (st, True, ratio, end_time, method_name)


# ── Anchor interpolation ──────────────────────────────────────────────────────

def _interpolate_unmatched(results: list[dict], total_duration: float) -> None:
    """
    Replace proportional-fallback start times with linear interpolation between
    the nearest preceding and following matched anchors.
    """
    n = len(results)
    for i in range(n):
        if results[i].get("matched"):
            continue

        prev_time = 0.0
        prev_idx = -1
        for j in range(i - 1, -1, -1):
            if results[j].get("matched"):
                prev_time = results[j]["start_time"]
                prev_idx = j
                break

        next_time = total_duration
        next_idx = n
        for j in range(i + 1, n):
            if results[j].get("matched"):
                next_time = results[j]["start_time"]
                next_idx = j
                break

        gap = max(next_idx - prev_idx, 1)
        local_pos = i - prev_idx
        results[i]["start_time"] = round(
            prev_time + (next_time - prev_time) * (local_pos / gap), 3
        )
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

    # Sorted list of word start times — used with bisect for fast time→index lookups.
    word_starts: list[float] = [float(w.get("start", 0) or 0) for w in words]

    # Cumulative script-word offsets for proportional time estimation.
    seg_word_offsets: list[int] = []
    cumulative = 0
    for seg in mj_data:
        seg_word_offsets.append(cumulative)
        cumulative += len((seg.get("segment_text") or "").strip().split())
    total_script_words = max(cumulative, 1)

    results: list[dict] = []
    # last_matched_end_time: end time (seconds) of the last matched segment's words.
    # Used as the monotonic lower bound for the next segment's search window.
    last_matched_end_time = 0.0

    for row_idx, seg in enumerate(mj_data):
        segment_id = seg.get("segment_id", 0)
        seg_word_list = (seg.get("segment_text") or "").strip().split()

        # Proportional time — the estimated audio position for this segment.
        prop_time = (seg_word_offsets[row_idx] / total_script_words) * total_duration

        if not seg_word_list:
            t = max(last_matched_end_time + 0.05, prop_time)
            results.append(
                {
                    "segment_id": segment_id,
                    "start_time": round(t, 3),
                    "matched": False,
                    "match_ratio": 0.0,
                    "method": "empty_segment_text",
                }
            )
            continue

        # ── Stage 1: NW inside the tight ±6-second window ────────────────────
        m = _try_time_window(
            seg_word_list, words, word_starts,
            last_matched_end_time, prop_time,
            TIME_SLACK_PRIMARY, total_duration,
            "local_nw_primary",
        )

        # ── Stage 2: NW inside the wide ±20-second window ────────────────────
        # Do NOT clamp by last_matched_end_time here — a bad Stage-1 cursor
        # advance must not shrink this window to nothing for later scenes.
        if m is None:
            m = _try_time_window(
                seg_word_list, words, word_starts,
                last_matched_end_time, prop_time,
                TIME_SLACK_SECONDARY, total_duration,
                "local_nw_secondary",
                use_cursor_lower_bound=False,
            )

        if m is not None:
            st, _, ratio, end_time, method = m
            # If the secondary window found a match that is technically before
            # the current cursor (because we disabled the cursor lower-bound),
            # pin the start to just after the last match so the timeline stays
            # monotonic.  The monotonicity post-pass will also catch any
            # remaining violations.
            if st < last_matched_end_time:
                st = round(last_matched_end_time + 0.05, 3)
            # Only advance the time cursor when the match is in a sane range.
            if abs(st - prop_time) <= TIME_SLACK_SECONDARY + 2:
                last_matched_end_time = max(last_matched_end_time, end_time)
            results.append(
                {
                    "segment_id": segment_id,
                    "start_time": st,
                    "matched": True,
                    "match_ratio": round(ratio, 2),
                    "method": method,
                }
            )
            continue

        # ── Stage 3: Proportional fallback ───────────────────────────────────
        # Clamp by last_matched_end_time so we never go backwards, but do NOT
        # advance the time cursor — a fallback position might be inaccurate.
        fallback_t = round(max(last_matched_end_time + 0.05, prop_time), 3)
        results.append(
            {
                "segment_id": segment_id,
                "start_time": fallback_t,
                "matched": False,
                "match_ratio": 0.0,
                "method": "proportional_fallback",
            }
        )

    # ── Post-pass 1: monotonic non-decreasing starts ─────────────────────────
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
