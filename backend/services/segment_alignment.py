"""
Align MJ prompt segments to ASR words.

Strategy (in priority order for every segment)
-----------------------------------------------
1. Global NW   – when the full script × audio grid fits the cell budget (short videos).
2. Per-segment local NW inside a proportional window  – PRIMARY method for long videos.
   Each segment gets its own search window *centred on where it should appear* in the
   audio based on its fractional position in the full script.  This is completely
   independent of whether earlier segments matched, so cascade failures are impossible.
3. Fuzzy first-word probe inside the same window  – secondary fallback.
4. Proportional-time estimate  – last resort; far more accurate than "uniform over the
   whole video" because it uses the segment's actual script-word offset.
5. Anchor interpolation post-pass  – remaining unmatched segments are placed on a
   straight line between the nearest preceding and following matched anchors.
"""

from __future__ import annotations

import bisect
import os
import re
from functools import lru_cache
from rapidfuzz import fuzz

from .word_timing import refine_word_timestamps


# ── Global NW budget ──────────────────────────────────────────────────────────
# Raised from 280k → 600k: handles medium-length videos (≤~35 segments) with the
# full global pass; larger videos fall through to per-segment local NW.
_MAX_NW_CELLS_DEFAULT = 600_000

# Words of audio to search on each side of the proportional estimate.
# 200 words ≈ 1.5–2 min of speech at typical pacing — generous enough to absorb
# any accumulated drift from earlier segments.
_WINDOW_SLACK = 200


def _max_nw_cells() -> int:
    try:
        return max(50_000, int(os.getenv("ASSEMBLY_MAX_NW_CELLS", str(_MAX_NW_CELLS_DEFAULT))))
    except (TypeError, ValueError):
        return _MAX_NW_CELLS_DEFAULT


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


def _pair_score(script_word: str, asr_word: str) -> float:
    return _pair_score_normed(_norm_token(script_word), _norm_token(asr_word))


def _nw_align_script_to_asr(
    script_tokens: list[str],
    asr_tokens: list[str],
    gap_script: float = -5.0,
    gap_asr: float = -5.0,
) -> list[int | None]:
    """
    Global NW alignment.  Returns list[script_len]: ASR index or None for each token.
    """
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


def _exact_word_start(idx: int, words: list[dict]) -> float:
    return round(float(words[idx]["start"]), 3)


def _segment_local_nw(
    seg_words: list[str],
    words: list[dict],
    search_from: int,
    search_to: int,
    *,
    min_match_ratio: float = 0.20,
) -> tuple[float, bool, float, int]:
    """
    Run NW between the full segment text and a bounded window [search_from, search_to).

    Using the full segment text (not just the first N words) makes this much more
    discriminative than a simple prefix probe, especially for short segments or those
    that start with common words.
    """
    search_to = min(search_to, len(words))
    if not seg_words or not words or search_from >= search_to:
        return 0.0, False, 0.0, search_from

    window = words[search_from:search_to]
    win_tokens = [w.get("word", "") for w in window]
    match_idx = _nw_align_script_to_asr(seg_words, win_tokens)
    matched_local = [j for j in match_idx if j is not None]

    if not matched_local:
        return _exact_word_start(search_from, words), False, 0.0, search_from

    match_ratio = len(matched_local) / max(len(seg_words), 1)
    if match_ratio < min_match_ratio:
        return (
            _exact_word_start(search_from, words),
            False,
            round(match_ratio * 100, 2),
            search_from,
        )

    actual_first = search_from + min(matched_local)
    actual_last = search_from + max(matched_local)
    return (
        _exact_word_start(actual_first, words),
        True,
        round(match_ratio * 100, 2),
        min(actual_last + 1, len(words)),
    )


def _fuzzy_probe_fallback(
    seg_words: list[str],
    words: list[dict],
    search_from: int,
    search_to: int | None = None,
    *,
    thresholds: tuple[tuple[int, int], ...] = ((6, 65), (4, 58), (3, 50)),
) -> tuple[float, bool, float, int]:
    """
    Sliding-window fuzzy probe on the first N words.
    Searches only within [search_from, search_to) when search_to is given.
    Thresholds lowered from (78/72/68) → (65/58/50); the monotonicity cursor
    and proportional window protect against false positives.
    """
    if not seg_words or not words:
        return 0.0, False, 0.0, search_from

    end = search_to if search_to is not None else len(words)
    best_ratio = 0.0
    best_pos = search_from
    matched = False
    matched_probe_size = 0

    for probe_size, threshold in thresholds:
        if len(seg_words) < probe_size or len(words) < probe_size:
            continue
        probe_norm = _norm_token(" ".join(seg_words[:probe_size]))
        cur_best_ratio = 0.0
        cur_best_pos = search_from

        for idx in range(search_from, min(end - probe_size + 1, len(words) - probe_size + 1)):
            window_str = " ".join(words[idx + k]["word"] for k in range(probe_size))
            ratio = fuzz.ratio(probe_norm, _norm_token(window_str))
            if ratio > cur_best_ratio:
                cur_best_ratio = ratio
                cur_best_pos = idx
            if ratio >= 92:
                break

        if cur_best_ratio > best_ratio:
            best_ratio = cur_best_ratio
            best_pos = cur_best_pos

        if cur_best_ratio >= threshold:
            matched = True
            matched_probe_size = probe_size
            break

    if matched:
        return (
            float(words[best_pos]["start"]),
            True,
            float(best_ratio),
            min(best_pos + matched_probe_size, len(words)),
        )

    return (
        float(words[search_from]["start"]) if search_from < len(words) else 0.0,
        False,
        float(best_ratio),
        search_from,
    )


def _interpolate_unmatched(results: list[dict], total_duration: float) -> None:
    """
    For any remaining unmatched segments, replace their start time with a value
    linearly interpolated between the nearest preceding and following matched anchors.
    This is far more accurate than distributing over the whole video.
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

        gap_count = max(next_idx - prev_idx, 1)
        local_pos = i - prev_idx
        results[i]["start_time"] = round(
            prev_time + (next_time - prev_time) * (local_pos / gap_count), 3
        )
        results[i]["method"] = "interpolated"


def compute_segment_starts(
    mj_data: list,
    audio_words_raw: list[dict],
    total_duration: float,
) -> list[dict]:
    """
    Core alignment function.  Returns one dict per segment:
    { segment_id, start_time, matched, match_ratio, method }
    """
    words = refine_word_timestamps([dict(w) for w in audio_words_raw]) if audio_words_raw else []

    # Pre-build a sorted list of word start times for O(log n) position look-ups.
    word_starts: list[float] = [float(w.get("start", 0) or 0) for w in words]

    # Cumulative script-word offsets: tells us how far into the script each segment is.
    seg_word_offsets: list[int] = []
    cumulative = 0
    for seg in mj_data:
        seg_word_offsets.append(cumulative)
        cumulative += len((seg.get("segment_text") or "").strip().split())
    total_script_words = max(cumulative, 1)

    # Flat script tokens (for global NW when the grid fits).
    flat_script: list[tuple[int, str]] = []
    for seg in mj_data:
        sid = seg.get("segment_id", 0)
        for tok in (seg.get("segment_text") or "").strip().split():
            flat_script.append((sid, tok))

    script_tokens = [t[1] for t in flat_script]
    asr_tokens = [str(w.get("word", "") or "") for w in words]

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

    # ── Global NW (only when the grid fits) ─────────────────────────────────
    match_asr_idx: list[int | None] = []
    global_nw_ok = False
    if script_tokens and asr_tokens:
        s_len, w_len = len(script_tokens), len(asr_tokens)
        if s_len * w_len <= _max_nw_cells():
            match_asr_idx = _nw_align_script_to_asr(script_tokens, asr_tokens)
            global_nw_ok = True
        else:
            match_asr_idx = [None] * s_len

    # Flat index ranges per segment (needed to read global NW results).
    flat_cursor = 0
    per_row_flat_ranges: list[tuple[int, int]] = []
    for seg in mj_data:
        sw = (seg.get("segment_text") or "").strip().split()
        n = len(sw)
        if n == 0:
            per_row_flat_ranges.append((flat_cursor, flat_cursor - 1))
        else:
            per_row_flat_ranges.append((flat_cursor, flat_cursor + n - 1))
            flat_cursor += n

    results: list[dict] = []
    last_matched_cursor = 0   # highest ASR index consumed by a matched segment

    for row_idx, seg in enumerate(mj_data):
        segment_id = seg.get("segment_id", 0)
        lo, hi = per_row_flat_ranges[row_idx]
        seg_word_list = (seg.get("segment_text") or "").strip().split()

        if lo > hi or not seg_word_list:
            elapsed = seg_word_offsets[row_idx] / total_script_words
            est = round(elapsed * total_duration, 3)
            if results:
                est = max(est, results[-1]["start_time"] + 0.5)
            results.append(
                {
                    "segment_id": segment_id,
                    "start_time": est,
                    "matched": False,
                    "match_ratio": 0.0,
                    "method": "empty_segment_text",
                }
            )
            continue

        # ── Compute per-segment proportional window ───────────────────────────
        # Estimate where in the audio this segment should appear based on its
        # fractional position in the full script text.  This is independent of
        # whether any prior segment matched, so cascade failures cannot happen.
        elapsed_frac = seg_word_offsets[row_idx] / total_script_words
        prop_time = elapsed_frac * total_duration
        prop_cursor = bisect.bisect_left(word_starts, prop_time)
        prop_cursor = max(0, min(prop_cursor, len(words) - 1))

        # The search window is centred on the proportional estimate but is
        # clamped from below by last_matched_cursor (monotonicity guarantee).
        search_from = max(last_matched_cursor, prop_cursor - _WINDOW_SLACK)
        search_to = min(len(words), prop_cursor + _WINDOW_SLACK + len(seg_word_list))

        # ── Stage 1: Global NW (short videos only) ───────────────────────────
        if global_nw_ok:
            matched_asr: list[int] = []
            scores: list[float] = []
            for fi in range(lo, hi + 1):
                aj = match_asr_idx[fi] if fi < len(match_asr_idx) else None
                if aj is not None:
                    matched_asr.append(aj)
                    if fi < len(script_tokens) and aj < len(asr_tokens):
                        scores.append(_pair_score(script_tokens[fi], asr_tokens[aj]))
            n_w = len(seg_word_list)
            matched_ratio = len(matched_asr) / max(n_w, 1)
            mean_pair = sum(scores) / len(scores) if scores else -99.0
            use_nw = (
                bool(matched_asr)
                and matched_ratio >= 0.34
                and mean_pair >= 5.0
                and min(matched_asr) >= last_matched_cursor
            )
            if use_nw:
                first_idx = min(matched_asr)
                ratio_guess = min(100.0, max(55.0, 52.0 + mean_pair * 2.8))
                results.append(
                    {
                        "segment_id": segment_id,
                        "start_time": _exact_word_start(first_idx, words),
                        "matched": True,
                        "match_ratio": round(ratio_guess, 2),
                        "method": "nw",
                    }
                )
                last_matched_cursor = max(last_matched_cursor, max(matched_asr) + 1)
                continue

        # ── Stage 2: Local NW inside proportional window (primary for long videos) ──
        if search_from < search_to:
            st_lnw, ok_lnw, br_lnw, pos_lnw = _segment_local_nw(
                seg_word_list, words, search_from, search_to
            )
            if ok_lnw:
                results.append(
                    {
                        "segment_id": segment_id,
                        "start_time": st_lnw,
                        "matched": True,
                        "match_ratio": round(br_lnw, 2),
                        "method": "local_nw",
                    }
                )
                last_matched_cursor = max(last_matched_cursor, pos_lnw)
                continue
        else:
            br_lnw = 0.0

        # ── Stage 3: Fuzzy probe inside the same window ──────────────────────
        st_fz, ok_fz, br_fz, pos_fz = _fuzzy_probe_fallback(
            seg_word_list, words, search_from, search_to
        )
        if ok_fz:
            results.append(
                {
                    "segment_id": segment_id,
                    "start_time": st_fz,
                    "matched": True,
                    "match_ratio": round(br_fz, 2),
                    "method": "fuzzy_only",
                }
            )
            last_matched_cursor = max(last_matched_cursor, pos_fz)
            continue

        # ── Stage 4: Proportional time estimate (last resort) ────────────────
        # Do NOT advance last_matched_cursor; keep the best matched position
        # so that later segments still search from the right neighbourhood.
        prop_start = round(prop_time, 3)
        if results:
            prop_start = max(prop_start, results[-1]["start_time"] + 0.05)
        results.append(
            {
                "segment_id": segment_id,
                "start_time": prop_start,
                "matched": False,
                "match_ratio": round(max(br_lnw, br_fz), 2),
                "method": "proportional_fallback",
            }
        )

    # ── Post-pass 1: enforce monotonic non-decreasing starts ─────────────────
    for i in range(1, len(results)):
        if results[i]["start_time"] <= results[i - 1]["start_time"]:
            results[i]["start_time"] = round(results[i - 1]["start_time"] + 0.05, 3)

    # ── Post-pass 2: anchor interpolation for unmatched segments ─────────────
    _interpolate_unmatched(results, total_duration)

    # ── Post-pass 3: final monotonicity (interpolation can violate it) ───────
    for i in range(1, len(results)):
        if results[i]["start_time"] <= results[i - 1]["start_time"]:
            results[i]["start_time"] = round(results[i - 1]["start_time"] + 0.05, 3)

    return results
