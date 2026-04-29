"""
Align MJ prompt segments to ASR words via global sequence alignment (NW-style),
with silence-aware start snapping and fallback to fuzzy prefix matching.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from rapidfuzz import fuzz

from .word_timing import refine_word_timestamps


# Above this many DP cells, NW is skipped (fallback to fuzzy/uniform per segment).
# Full NW calls fuzz.ratio once per cell — at 2M cells that can stall for minutes.
_MAX_NW_CELLS_DEFAULT = 280_000


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
    """Scores pre-normalized tokens; LRU helps when vocabulary repeats across the grid."""
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
    Global alignment. Returns list of length len(script_tokens):
    each entry is the ASR index matched to that script token, or None (gap in ASR).
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
        back[i][0] = 1  # came from (i-1,0)
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

    # Backtrace from (S, W)
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


def _exact_word_start(first_asr_idx: int, words: list[dict]) -> float:
    """Return the exact ASR timestamp of the matched word with no nudging."""
    return round(float(words[first_asr_idx]["start"]), 3)


def _fuzzy_probe_fallback(
    seg_words: list[str],
    words: list[dict],
    search_from: int,
    *,
    thresholds: tuple[tuple[int, int], ...] = ((6, 78), (4, 72), (3, 68)),
) -> tuple[float, bool, float, int]:
    """Original sliding-window probe; returns (start_time, matched, ratio, new_last_pos)."""
    if not seg_words or not words:
        return 0.0, False, 0.0, search_from

    best_ratio = 0.0
    best_pos = search_from
    matched = False
    matched_probe_size = 0
    probe_str = ""

    for probe_size, threshold in thresholds:
        if len(seg_words) < probe_size or len(words) < probe_size:
            continue
        probe_str = " ".join(seg_words[:probe_size])
        probe_norm = _norm_token(probe_str)
        current_best_ratio = 0.0
        current_best_pos = search_from

        for idx in range(search_from, len(words) - probe_size + 1):
            window_words = [words[idx + k]["word"] for k in range(probe_size)]
            window_str = " ".join(window_words)
            ratio = fuzz.ratio(probe_norm, _norm_token(window_str))
            if ratio > current_best_ratio:
                current_best_ratio = ratio
                current_best_pos = idx
            if ratio >= 92:
                break

        if current_best_ratio > best_ratio:
            best_ratio = current_best_ratio
            best_pos = current_best_pos

        if current_best_ratio >= threshold:
            matched = True
            matched_probe_size = probe_size
            best_ratio = current_best_ratio
            best_pos = current_best_pos
            break

    if matched:
        start = float(words[best_pos]["start"])
        last_pos = best_pos + matched_probe_size
        return start, True, float(best_ratio), min(last_pos, len(words))

    return float(words[search_from]["start"]) if search_from < len(words) else 0.0, False, float(best_ratio), search_from


def compute_segment_starts(
    mj_data: list,
    audio_words_raw: list[dict],
    total_duration: float,
) -> list[dict]:
    """
    Returns list aligned with mj_data order:
    [{ "segment_id", "start_time", "matched", "match_ratio", "method" }, ...]
    """
    words = refine_word_timestamps([dict(w) for w in audio_words_raw]) if audio_words_raw else []

    flat_script: list[tuple[int, str]] = []
    for seg in mj_data:
        sid = seg.get("segment_id", 0)
        st = (seg.get("segment_text") or "").strip()
        for tok in st.split():
            flat_script.append((sid, tok))

    script_tokens = [t[1] for t in flat_script]
    asr_tokens = [str(w.get("word", "") or "") for w in words]

    if not words:
        out = []
        for i, seg in enumerate(mj_data):
            sid = seg.get("segment_id", 0)
            est = (i / max(len(mj_data), 1)) * total_duration if total_duration else 0.0
            out.append(
                {
                    "segment_id": sid,
                    "start_time": round(est, 3),
                    "matched": False,
                    "match_ratio": 0.0,
                    "method": "empty_asr",
                }
            )
        return out

    match_asr_idx: list[int | None] = []
    if script_tokens and asr_tokens:
        s_len, w_len = len(script_tokens), len(asr_tokens)
        cap = _max_nw_cells()
        if s_len * w_len <= cap:
            match_asr_idx = _nw_align_script_to_asr(script_tokens, asr_tokens)
        else:
            match_asr_idx = [None] * s_len

    # Flat index ranges per MJ row (order matches mj_data)
    results: list[dict] = []
    last_asr_cursor = 0

    flat_cursor = 0
    per_row_flat_ranges: list[tuple[int, int]] = []
    for seg in mj_data:
        sw = (seg.get("segment_text") or "").strip().split()
        n = len(sw)
        if n == 0:
            per_row_flat_ranges.append((flat_cursor, flat_cursor - 1))
        else:
            lo, hi = flat_cursor, flat_cursor + n - 1
            per_row_flat_ranges.append((lo, hi))
            flat_cursor += n

    for row_idx, seg in enumerate(mj_data):
        segment_id = seg.get("segment_id", 0)
        lo, hi = per_row_flat_ranges[row_idx]
        seg_word_list = (seg.get("segment_text") or "").strip().split()

        if lo > hi or not seg_word_list:
            # Empty segment text — distribute time
            est = (row_idx / max(len(mj_data), 1)) * total_duration if total_duration else 0.0
            if results:
                est = max(est, results[-1]["start_time"] + 0.5)
            results.append(
                {
                    "segment_id": segment_id,
                    "start_time": round(est, 3),
                    "matched": False,
                    "match_ratio": 0.0,
                    "method": "empty_segment_text",
                }
            )
            continue

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
        use_nw = bool(matched_asr) and matched_ratio >= 0.34 and mean_pair >= 3.5

        if use_nw:
            first_idx = min(matched_asr)
            start_snapped = _exact_word_start(first_idx, words)
            ratio_guess = min(100.0, max(55.0, 52.0 + mean_pair * 2.8))

            nw_overlap = first_idx < last_asr_cursor
            nw_weak = mean_pair < 5.0
            if nw_overlap or nw_weak:
                st, ok, br, new_pos = _fuzzy_probe_fallback(seg_word_list, words, last_asr_cursor)
                if ok and br >= 70:
                    fi = min(range(len(words)), key=lambda i: abs(float(words[i]["start"]) - st))
                    start_snapped = _exact_word_start(fi, words)
                    results.append(
                        {
                            "segment_id": segment_id,
                            "start_time": round(start_snapped, 3),
                            "matched": True,
                            "match_ratio": round(br, 2),
                            "method": "fuzzy_fallback",
                        }
                    )
                    last_asr_cursor = max(last_asr_cursor, new_pos)
                    continue

            results.append(
                {
                    "segment_id": segment_id,
                    "start_time": round(start_snapped, 3),
                    "matched": True,
                    "match_ratio": round(min(100.0, ratio_guess), 2),
                    "method": "nw",
                }
            )
            last_asr_cursor = max(last_asr_cursor, max(matched_asr) + 1)
            continue

        # Fuzzy fallback for whole segment
        st, ok, br, new_pos = _fuzzy_probe_fallback(seg_word_list, words, last_asr_cursor)
        if ok:
            fi = min(range(len(words)), key=lambda i: abs(float(words[i]["start"]) - st))
            start_snapped = _exact_word_start(fi, words)
            results.append(
                {
                    "segment_id": segment_id,
                    "start_time": round(start_snapped, 3),
                    "matched": True,
                    "match_ratio": round(br, 2),
                    "method": "fuzzy_only",
                }
            )
            last_asr_cursor = max(last_asr_cursor, new_pos)
        else:
            total_segments = len(mj_data)
            estimated_start = (row_idx / total_segments) * total_duration if total_segments else 0.0
            if results:
                estimated_start = max(estimated_start, results[-1]["start_time"] + 0.5)
            results.append(
                {
                    "segment_id": segment_id,
                    "start_time": round(estimated_start, 3),
                    "matched": False,
                    "match_ratio": round(br, 2),
                    "method": "uniform_fallback",
                }
            )

    # Enforce monotonic non-decreasing starts
    for i in range(1, len(results)):
        if results[i]["start_time"] <= results[i - 1]["start_time"]:
            results[i]["start_time"] = round(results[i - 1]["start_time"] + 0.05, 3)

    return results
