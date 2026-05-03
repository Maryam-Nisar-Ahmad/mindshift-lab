from __future__ import annotations

import bisect
import json
from pathlib import Path
import re
import subprocess

from rapidfuzz import fuzz

from .segment_images import resolve_segment_image_basename
from .segment_alignment import _nw_align
from .word_timing import load_alignment_word_list, prepare_karaoke_word_list


def _audio_timeline_json_path(project_dir: Path) -> Path:
    """Prefer DeepSeek-corrected transcript; fall back to raw Whisper JSON."""
    for name in ("audio_corrected.json", "audio_transcript.json"):
        p = project_dir / name
        if p.is_file():
            return p
    raise FileNotFoundError(
        "No audio transcript found (need audio_corrected.json or audio_transcript.json in the project folder). "
        "Upload audio.mp3 on Step 5 again and wait until processing completes."
    )


def read_audio_timeline_duration_sec(project_dir: Path) -> float | None:
    """Duration from the same JSON used for assembly (seconds), or None if missing."""
    project_dir = Path(project_dir)
    try:
        audio_path = _audio_timeline_json_path(project_dir)
    except FileNotFoundError:
        return None
    with open(audio_path, "r", encoding="utf-8") as f:
        audio_data = json.load(f)
    words = audio_data.get("words") or []
    d = float(audio_data.get("duration", 0) or 0)
    if d <= 0 and words:
        d = float(words[-1].get("end", 0) or 0)
    return float(d) if d > 0 else None


_MIN_ACCEPTABLE_DURATION = 0.5   # seconds; shorter than this is suspicious
_CLUSTER_THRESHOLD = 1           # redistribute any suspicious short run

# Only proportionally “fill” a caption line when intra-word silence is huge (missing token).
# Normal ~0.15–0.25s gaps must keep Whisper/alignment timings or karaoke feels early/late.
_SUBTITLE_SNAP_ONLY_IF_MAX_GAP_SEC = 0.55
SUBTITLE_ONSET_EARLY_THRESHOLD_MS = 100.0
SUBTITLE_ONSET_TARGET_MARGIN_MS = 50.0
# Localized subtitle fixes (no global offset)
LARGE_SILENCE_INTER_SEGMENT_GAP_SEC = 3.0
FAST_SPEECH_WPS_THRESHOLD = 3.2
FAST_SPEECH_WINDOW_WORDS = 5
FAST_SPEECH_EARLY_THRESHOLD_MS = 60.0


def _sorted_word_starts(words: list) -> list[float]:
    out: list[float] = []
    for w in words or []:
        try:
            out.append(float(w.get("start", 0) or 0))
        except (TypeError, ValueError):
            continue
    out.sort()
    return out


def _snap_start_to_words(
    t: float,
    word_starts: list[float],
    lo: float,
    hi: float,
) -> float:
    """Pick a word onset in ``[lo, hi]`` nearest to ``t`` (keeps cuts near real speech)."""
    if not word_starts or hi <= lo:
        return round(t, 3)
    i = bisect.bisect_left(word_starts, lo)
    best = round(t, 3)
    best_d = 1e9
    while i < len(word_starts):
        ws = word_starts[i]
        if ws > hi:
            break
        if ws < lo:
            i += 1
            continue
        d = abs(ws - t)
        if d < best_d:
            best_d = d
            best = round(ws, 3)
        i += 1
    return max(round(lo, 3), min(best, round(hi, 3)))


def _gap_between_matched(segments: list, left_idx: int, n: int, total_duration: float) -> float:
    """Minimum span between ``start[left_idx]`` and ``start[left_idx + 1]``."""
    feasible = total_duration / max(n, 1)
    a = bool(segments[left_idx].get("matched"))
    b = bool(segments[left_idx + 1].get("matched"))
    if a and b:
        # NW-aligned neighbours: small gap only — final ripple pass enforces readable duration.
        return min(0.45, max(0.16, feasible * 0.28))
    return min(0.5, max(0.05, feasible * 0.95))


def _fix_short_duration_clusters(
    segments: list,
    total_duration: float,
    word_starts: list[float] | None = None,
) -> list:
    """
    Find runs of ≥ _CLUSTER_THRESHOLD consecutive scenes whose duration is
    < _MIN_ACCEPTABLE_DURATION and redistribute their start times by linear
    interpolation between the enclosing long-duration anchors.

    After redistribution the segment `end` values are recomputed.
    """
    n = len(segments)
    if n == 0:
        return segments

    # Rebuild durations from current start values (ends are start[i+1] or total_duration).
    def duration(i: int) -> float:
        s = float(segments[i].get("start", 0) or 0)
        e = float(segments[i + 1]["start"]) if i + 1 < n else total_duration
        return e - s

    i = 0
    while i < n:
        if duration(i) < _MIN_ACCEPTABLE_DURATION:
            # Find the full extent of the short run.
            j = i
            while j < n and duration(j) < _MIN_ACCEPTABLE_DURATION:
                j += 1
            run_len = j - i
            if run_len >= _CLUSTER_THRESHOLD:
                # Anchor before the run.
                prev_start = float(segments[i - 1]["start"]) if i > 0 else 0.0
                # Anchor after the run (first long scene past j-1).
                next_start = float(segments[j]["start"]) if j < n else total_duration
                span = next_start - prev_start
                # Need enough space between anchors for each short scene to reach ~min duration.
                # Old (run_len+1)*0.5 skipped valid single-scene fixes when span was 0.5–1.0s.
                min_span_needed = max(
                    run_len * _MIN_ACCEPTABLE_DURATION * 0.92,
                    _MIN_ACCEPTABLE_DURATION * 0.42,
                )
                if span >= min_span_needed:
                    # Distribute evenly across prev+1 … j-1 (leave prev and next untouched).
                    slots = run_len + 1  # gaps between prev_anchor and next_anchor
                    for k, idx in enumerate(range(i, j), start=1):
                        new_t = round(prev_start + span * k / slots, 3)
                        lo = prev_start + 0.02
                        hi = next_start - 0.02
                        if word_starts and hi > lo:
                            new_t = _snap_start_to_words(new_t, word_starts, lo, hi)
                        if abs(float(segments[idx].get("start", 0) or 0) - new_t) >= 0.001:
                            segments[idx]["start"] = new_t
                            segments[idx]["matched"] = False
                            segments[idx]["method"] = "cluster_interpolated"
        i += 1

    # Recompute end values after redistribution.
    for i in range(n):
        if i < n - 1:
            segments[i]["end"] = round(float(segments[i + 1]["start"]), 3)
        else:
            segments[i]["end"] = round(float(total_duration or 0), 3)
        if segments[i]["end"] <= segments[i]["start"]:
            segments[i]["end"] = round(float(segments[i]["start"] or 0) + 1.0, 3)

    return segments


def _enforce_min_scene_duration(segments: list, total_duration: float, min_duration: float = 0.5) -> list:
    """
    Global timing stabilizer:
    enforce a minimum gap between consecutive scene starts when feasible.

    Gaps are **tighter between two NW-matched scenes** so we do not destroy
    phoneme-aligned anchors; wider gaps apply when either scene relied on
    proportional / interpolated timing.
    """
    n = len(segments)
    if n <= 1 or total_duration <= 0:
        return segments

    tail_gap = _gap_between_matched(segments, n - 2, n, total_duration) if n >= 2 else 0.05
    max_last_start = max(0.0, total_duration - max(0.04, tail_gap * 0.5))

    starts = [max(0.0, float(s.get("start", 0) or 0)) for s in segments]

    # Alternating forward/backward projections quickly converge for this constraint set.
    for _ in range(5):
        starts[0] = max(0.0, starts[0])
        for i in range(1, n):
            gap = _gap_between_matched(segments, i - 1, n, total_duration)
            lb = starts[i - 1] + gap
            if starts[i] < lb:
                starts[i] = lb
        if starts[-1] > max_last_start:
            starts[-1] = max_last_start
        for i in range(n - 2, -1, -1):
            gap = _gap_between_matched(segments, i, n, total_duration)
            ub = starts[i + 1] - gap
            if starts[i] > ub:
                starts[i] = ub
        if starts[0] < 0.0:
            shift = -starts[0]
            starts = [x + shift for x in starts]

    changed = False
    for i, s in enumerate(segments):
        new_start = round(starts[i], 3)
        old_start = round(float(s.get("start", 0) or 0), 3)
        if abs(new_start - old_start) >= 0.001:
            s["start"] = new_start
            s["matched"] = False
            s["method"] = "duration_stabilized"
            changed = True

    if changed:
        for i in range(n):
            if i < n - 1:
                segments[i]["end"] = round(float(segments[i + 1]["start"]), 3)
            else:
                segments[i]["end"] = round(float(total_duration or 0), 3)
            if segments[i]["end"] <= segments[i]["start"]:
                pad = min_duration if not segments[i].get("matched") else 0.08
                segments[i]["end"] = round(float(segments[i]["start"] or 0) + pad, 3)

    return segments


def _ripple_minimum_scene_durations(
    segments: list,
    total_duration: float,
    word_starts: list[float] | None = None,
) -> list:
    """
    Guarantee each scene (except possibly the last when the timeline is packed)
    has duration >= target by pushing later starts forward; ripples until stable.
    Eliminates sub-0.5s flashes left by NW + tight matched gaps.
    """
    n = len(segments)
    if n <= 1 or total_duration <= 0:
        return segments

    packed = total_duration / max(n, 1)
    min_dur = _MIN_ACCEPTABLE_DURATION
    if packed < min_dur:
        min_dur = max(0.28, min(packed * 0.92, min_dur))

    for _ in range(n + 10):
        changed = False
        for i in range(n - 1):
            s0 = float(segments[i].get("start", 0) or 0)
            s1 = float(segments[i + 1].get("start", 0) or 0)
            need = round(s0 + min_dur, 3)
            if s1 >= need - 1e-4:
                continue
            new_s1 = float(need)
            if word_starts:
                lo = s0 + 0.03
                hi = total_duration - 0.02
                if i + 2 < n:
                    hi = min(hi, float(segments[i + 2].get("start", total_duration)) - 0.03)
                if hi > lo:
                    snapped = _snap_start_to_words(new_s1, word_starts, lo, hi)
                    new_s1 = max(float(snapped), need)
            new_s1 = round(min(max(new_s1, need), total_duration - 0.02), 3)
            if new_s1 <= s1 + 1e-6:
                continue
            segments[i + 1]["start"] = new_s1
            segments[i + 1]["matched"] = False
            segments[i + 1]["method"] = "min_duration_ripple"
            changed = True
        if not changed:
            break

    # Last segment: extend visible hold toward end of audio when possible
    if n >= 2:
        ls = float(segments[-1].get("start", 0) or 0)
        last_dur = total_duration - ls
        prev_s = float(segments[-2].get("start", 0) or 0)
        if last_dur < min_dur - 1e-4:
            ideal = round(total_duration - min_dur, 3)
            low = round(prev_s + min_dur, 3)
            if ideal >= low - 1e-4 and ideal < ls - 1e-4:
                segments[-1]["start"] = ideal
                segments[-1]["matched"] = False
                segments[-1]["method"] = "min_duration_ripple"

    for i in range(n):
        if i < n - 1:
            segments[i]["end"] = round(float(segments[i + 1]["start"]), 3)
        else:
            segments[i]["end"] = round(float(total_duration or 0), 3)
        if segments[i]["end"] <= segments[i]["start"]:
            segments[i]["end"] = round(
                float(segments[i]["start"] or 0) + max(min_dur * 0.5, 0.16),
                3,
            )

    return segments


def _nudge_segment_starts_to_word_onsets(
    segments: list,
    word_starts: list[float],
    total_duration: float,
    max_nudge: float = 0.32,
) -> list:
    """
    Pull each scene cut onto the nearest real ASR word onset when within ``max_nudge`` seconds.
    Improves perceived lip-sync after ripple/cluster passes that land between words.
    """
    n = len(segments)
    if n == 0 or not word_starts or total_duration <= 0:
        return segments
    min_edge = 0.18
    for i in range(n):
        t = float(segments[i].get("start", 0) or 0)
        lo = float(segments[i - 1]["start"]) + min_edge if i > 0 else 0.0
        hi = float(segments[i + 1]["start"]) - min_edge if i + 1 < n else total_duration - 0.02
        if hi <= lo:
            continue
        lo_n = max(lo, t - max_nudge)
        hi_n = min(hi, t + max_nudge)
        if hi_n <= lo_n:
            continue
        snapped = _snap_start_to_words(t, word_starts, lo_n, hi_n)
        if abs(snapped - t) >= 0.008:
            segments[i]["start"] = snapped
            segments[i]["matched"] = False
            segments[i]["method"] = "word_onset_nudge"

    for i in range(1, n):
        if float(segments[i]["start"]) <= float(segments[i - 1]["start"]):
            segments[i]["start"] = round(float(segments[i - 1]["start"]) + 0.05, 3)
            segments[i]["matched"] = False

    for i in range(n):
        if i < n - 1:
            segments[i]["end"] = round(float(segments[i + 1]["start"]), 3)
        else:
            segments[i]["end"] = round(float(total_duration or 0), 3)
        if segments[i]["end"] <= segments[i]["start"]:
            segments[i]["end"] = round(float(segments[i]["start"] or 0) + 0.35, 3)

    return segments


def _script_tokens(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?", text or "")]


def _norm_karaoke_token(text: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", (text or "").strip().lower())


def _phrase_anchor_score(seg_tokens: list[str], audio_tokens: list[str], start_idx: int) -> tuple[float, int]:
    """
    Score how well ``seg_tokens`` match ``audio_tokens[start_idx:]`` as a short phrase.
    Returns (score_0_to_100_plus_bias, compared_tokens_count).
    """
    if start_idx >= len(audio_tokens):
        return 0.0, 0
    m = min(len(seg_tokens), len(audio_tokens) - start_idx, 7)
    if m <= 0:
        return 0.0, 0
    s = 0.0
    wsum = 0.0
    compared = 0
    for k in range(m):
        st = seg_tokens[k]
        at = audio_tokens[start_idx + k]
        if not st or not at:
            continue
        rk = float(max(fuzz.ratio(st, at), fuzz.partial_ratio(st, at)))
        # First two words are strongest onset identity for scene cuts.
        wk = 1.45 if k < 2 else 1.0
        if len(st) <= 2:
            wk *= 0.6
        s += rk * wk
        wsum += wk
        compared += 1
    if wsum <= 1e-9:
        return 0.0, 0
    # Slight bias toward candidates matching more consecutive words.
    return (s / wsum) + min(7.5, max(0.0, compared - 1) * 1.8), compared


def _line_anchor_score(scene_tokens: list[str], line_tokens: list[str]) -> float:
    """Fuzzy score between scene opening tokens and one subtitle line."""
    if not scene_tokens or not line_tokens:
        return 0.0
    probe = scene_tokens[:4]
    window = line_tokens[:7]
    total = 0.0
    used = 0
    for i, tok in enumerate(probe):
        if not tok:
            continue
        best = 0.0
        for lw in window:
            if not lw:
                continue
            sc = float(max(fuzz.ratio(tok, lw), fuzz.partial_ratio(tok, lw)))
            if sc > best:
                best = sc
        if best <= 0:
            continue
        w = 1.35 if i == 0 else 1.0
        total += best * w
        used += 1
    if used == 0:
        return 0.0
    return total / (1.35 + max(0, used - 1))


def _anchor_segments_to_subtitle_lines(
    segments: list,
    mj_data: list,
    subtitle_lines: list[dict],
    total_duration: float,
) -> list:
    """
    Map each scene start onto the subtitle timeline (same timing source user hears/sees).
    """
    n = min(len(segments), len(mj_data))
    if n == 0 or not subtitle_lines:
        return segments
    line_tokens = [
        [_norm_karaoke_token(t) for t in _script_tokens(line.get("text") or "")]
        for line in subtitle_lines
    ]
    line_cursor = 0
    ln = len(subtitle_lines)
    for i in range(n):
        seg = segments[i]
        stoks = [_norm_karaoke_token(t) for t in _script_tokens(mj_data[i].get("segment_text") or "")]
        stoks = [t for t in stoks if t and len(t) > 1]
        if not stoks:
            continue
        cur_t = float(seg.get("start", 0) or 0)
        prev_lo = float(segments[i - 1].get("start", 0) or 0) + 0.06 if i > 0 else 0.0
        next_hi = (
            float(segments[i + 1].get("start", total_duration) or total_duration) - 0.10
            if i + 1 < n
            else max(total_duration - 0.02, prev_lo + 0.08)
        )
        if next_hi <= prev_lo + 0.01:
            continue
        best_li = None
        best_sc = 0.0
        best_dist = 1e9
        max_li = min(ln, line_cursor + 30)
        for li in range(line_cursor, max_li):
            line = subtitle_lines[li]
            l_words = line.get("words") or []
            l_start = float(l_words[0].get("start", line.get("start", 0)) or 0) if l_words else float(line.get("start", 0) or 0)
            if l_start < prev_lo - 0.02:
                continue
            if l_start > next_hi + 1.2:
                break
            sc = _line_anchor_score(stoks, line_tokens[li])
            if sc < 80.0:
                continue
            d = abs(l_start - cur_t)
            eff = sc - min(8.0, d * 2.0)
            if eff > best_sc or (abs(eff - best_sc) < 1e-6 and d < best_dist):
                best_sc = eff
                best_dist = d
                best_li = li
        if best_li is None:
            continue
        bline = subtitle_lines[best_li]
        bwords = bline.get("words") or []
        new_t = float(bwords[0].get("start", bline.get("start", cur_t)) or cur_t) if bwords else float(bline.get("start", cur_t) or cur_t)
        new_t = max(prev_lo, min(new_t, next_hi))
        if abs(new_t - cur_t) >= 0.006:
            seg["start"] = round(new_t, 3)
            seg["matched"] = bool(seg.get("matched")) or best_sc >= 87.0
            seg["method"] = "subtitle_line_anchor"
        line_cursor = min(ln - 1, max(line_cursor, best_li))

    for i in range(1, n):
        if float(segments[i]["start"]) <= float(segments[i - 1]["start"]):
            segments[i]["start"] = round(float(segments[i - 1]["start"]) + 0.05, 3)
            segments[i]["matched"] = False
            segments[i]["method"] = "subtitle_line_anchor_repair"
    for i in range(n):
        if i < n - 1:
            segments[i]["end"] = round(float(segments[i + 1]["start"]), 3)
        else:
            segments[i]["end"] = round(float(total_duration or 0), 3)
        if float(segments[i]["end"]) <= float(segments[i]["start"]):
            segments[i]["end"] = round(float(segments[i]["start"]) + 0.35, 3)
    return segments


def _repair_long_scene_outliers_with_subtitles(
    segments: list,
    mj_data: list,
    subtitle_lines: list[dict],
    total_duration: float,
    max_scene_duration: float = 18.0,
) -> list:
    """
    If a scene lasts too long, pull the NEXT scene start from subtitle lines
    using that next scene's text. This fixes isolated missed cuts.
    """
    n = min(len(segments), len(mj_data))
    if n < 2 or not subtitle_lines:
        return segments
    line_tokens = [
        [_norm_karaoke_token(t) for t in _script_tokens(line.get("text") or "")]
        for line in subtitle_lines
    ]
    ln = len(subtitle_lines)

    for i in range(n - 1):
        s0 = float(segments[i].get("start", 0) or 0)
        s1 = float(segments[i + 1].get("start", total_duration) or total_duration)
        if s1 - s0 <= max_scene_duration:
            continue

        next_tokens = [
            _norm_karaoke_token(t)
            for t in _script_tokens(mj_data[i + 1].get("segment_text") or "")
        ]
        next_tokens = [t for t in next_tokens if t and len(t) > 1]
        if not next_tokens:
            continue

        lo = s0 + min(2.2, max_scene_duration * 0.35)
        hi = min(total_duration - 0.08, s0 + max_scene_duration + 5.0)
        if i + 2 < n:
            hi = min(hi, float(segments[i + 2].get("start", total_duration) or total_duration) - 0.12)
        if hi <= lo + 0.15:
            continue

        cur_next_start = s1
        best_li = None
        best_sc = 0.0
        best_dist = 1e9
        for li in range(ln):
            line = subtitle_lines[li]
            words = line.get("words") or []
            l_start = float(words[0].get("start", line.get("start", 0)) or 0) if words else float(line.get("start", 0) or 0)
            if l_start < lo:
                continue
            if l_start > hi:
                break
            sc = _line_anchor_score(next_tokens, line_tokens[li])
            if sc < 74.0:
                continue
            d = abs(l_start - cur_next_start)
            eff = sc - min(9.0, d * 1.6)
            if eff > best_sc or (abs(eff - best_sc) < 1e-6 and d < best_dist):
                best_sc = eff
                best_dist = d
                best_li = li

        if best_li is None or best_sc < 78.0:
            continue

        line = subtitle_lines[best_li]
        words = line.get("words") or []
        new_s1 = float(words[0].get("start", line.get("start", cur_next_start)) or cur_next_start) if words else float(line.get("start", cur_next_start) or cur_next_start)
        new_s1 = max(lo, min(new_s1, hi))
        if abs(new_s1 - s1) >= 0.008:
            segments[i + 1]["start"] = round(new_s1, 3)
            segments[i + 1]["matched"] = False
            segments[i + 1]["method"] = "long_scene_subtitle_repair"

    for i in range(1, n):
        if float(segments[i]["start"]) <= float(segments[i - 1]["start"]):
            segments[i]["start"] = round(float(segments[i - 1]["start"]) + 0.05, 3)
            segments[i]["matched"] = False
            segments[i]["method"] = "long_scene_subtitle_repair"
    for i in range(n):
        if i < n - 1:
            segments[i]["end"] = round(float(segments[i + 1]["start"]), 3)
        else:
            segments[i]["end"] = round(float(total_duration or 0), 3)
        if float(segments[i]["end"]) <= float(segments[i]["start"]):
            segments[i]["end"] = round(float(segments[i]["start"]) + 0.35, 3)
    return segments


def _anchor_segments_to_karaoke_clock(
    segments: list,
    mj_data: list,
    karaoke_words: list[dict],
    total_duration: float,
    max_seek_ahead_words: int = 260,
) -> list:
    """
    Anchor each scene to the first spoken token from the same word clock used by subtitles.
    This keeps image cuts and caption cues on one timeline source.
    """
    n = min(len(segments), len(mj_data))
    if n == 0 or not karaoke_words:
        return segments
    audio_tokens = [_norm_karaoke_token(w.get("word", "")) for w in karaoke_words]
    cursor = 0
    word_n = len(karaoke_words)
    for i in range(n):
        seg = segments[i]
        toks = [_norm_karaoke_token(t) for t in _script_tokens(mj_data[i].get("segment_text") or "")]
        toks = [t for t in toks if t]
        if not toks:
            continue
        phrase = toks[:8]
        target = phrase[0]
        cur_t = float(seg.get("start", 0) or 0)
        prev_lo = float(segments[i - 1].get("start", 0) or 0) + 0.06 if i > 0 else 0.0
        next_hi = (
            float(segments[i + 1].get("start", total_duration) or total_duration) - 0.12
            if i + 1 < n
            else max(total_duration - 0.02, prev_lo + 0.08)
        )
        if next_hi <= prev_lo + 0.01:
            continue

        best_idx = None
        best_score = 0.0
        best_dist = 1e9
        best_compared = 0
        end_idx = min(word_n, cursor + max_seek_ahead_words)
        for wi in range(cursor, end_idx):
            w = karaoke_words[wi]
            ws = float(w.get("start", 0) or 0)
            if ws < prev_lo - 0.02:
                continue
            if ws > next_hi + 1.0:
                break
            aw = audio_tokens[wi]
            if len(aw) < 1:
                continue
            onset_sc = float(max(fuzz.ratio(target, aw), fuzz.partial_ratio(target, aw)))
            if onset_sc < 74.0:
                continue
            sc, compared = _phrase_anchor_score(phrase, audio_tokens, wi)
            if compared <= 0:
                continue
            # Favor phrase match quality; lightly penalize large jumps from current estimate.
            sc_eff = sc - min(10.0, abs(ws - cur_t) * 2.4)
            d = abs(ws - cur_t)
            if sc_eff > best_score or (abs(sc_eff - best_score) < 1e-6 and d < best_dist):
                best_score = sc_eff
                best_dist = d
                best_idx = wi
                best_compared = compared

        if best_idx is None:
            continue
        min_accept = 88.0 if best_compared <= 2 else 82.0
        if best_score < min_accept:
            continue
        new_t = float(karaoke_words[best_idx].get("start", cur_t) or cur_t)
        if new_t < prev_lo:
            new_t = prev_lo
        if new_t > next_hi:
            new_t = next_hi
        if abs(new_t - cur_t) >= 0.006:
            seg["start"] = round(new_t, 3)
            seg["matched"] = bool(seg.get("matched")) or best_score >= 89.0
            seg["method"] = "karaoke_anchor"
        cursor = min(word_n - 1, max(cursor, best_idx + max(1, best_compared // 2)))

    for i in range(1, n):
        if float(segments[i]["start"]) <= float(segments[i - 1]["start"]):
            segments[i]["start"] = round(float(segments[i - 1]["start"]) + 0.05, 3)
            segments[i]["matched"] = False
            segments[i]["method"] = "karaoke_anchor_repair"
    for i in range(n):
        if i < n - 1:
            segments[i]["end"] = round(float(segments[i + 1]["start"]), 3)
        else:
            segments[i]["end"] = round(float(total_duration or 0), 3)
        if float(segments[i]["end"]) <= float(segments[i]["start"]):
            segments[i]["end"] = round(float(segments[i]["start"]) + 0.35, 3)
    return segments


def map_segments_to_timestamps(project_id, project_dir) -> list:
    project_dir = Path(project_dir)
    mj_path = project_dir / "mj_prompts.json"
    audio_path = _audio_timeline_json_path(project_dir)

    with open(mj_path, "r", encoding="utf-8") as f:
        mj_data = json.load(f)
    with open(audio_path, "r", encoding="utf-8") as f:
        audio_data = json.load(f)

    words = audio_data.get("words") or []
    total_duration = float(audio_data.get("duration", words[-1]["end"]) if words else 0.0)
    result = []
    images_dir = project_dir / "images"

    deterministic_rows, deterministic_report = _deterministic_segment_rows_from_whisper(
        mj_data=mj_data,
        audio_data=audio_data,
        total_duration=total_duration,
    )

    # Hard requirement for Step 6: images must follow deterministic Whisper timing.
    if not deterministic_rows:
        raise RuntimeError(
            "Deterministic Whisper line-to-word mapping failed. "
            "Step 6 does not allow heuristic fallback."
        )
    aligned = deterministic_rows

    for seg, row in zip(mj_data, aligned):
        segment_id = seg.get("segment_id", 0)
        segment_text = seg.get("segment_text", "")
        start_time = float(row.get("start_time", 0) or 0)
        end_time = row.get("end_time")
        matched = bool(row.get("matched"))

        img_name = resolve_segment_image_basename(images_dir, segment_id)
        result.append({
            "segment_id": segment_id,
            "image": img_name or (str(segment_id) + ".png"),
            "start": round(start_time, 3),
            "end": round(float(end_time), 3) if end_time is not None else None,
            "text": segment_text,
            "matched": matched,
            "match_ratio": round(float(row.get("match_ratio") or 0), 2),
        })

    for i in range(len(result)):
        if result[i].get("end") is None:
            result[i]["end"] = round(float(total_duration or 0), 3)
        if result[i]["end"] <= result[i]["start"]:
            result[i]["end"] = round(float(result[i]["start"] or 0) + 0.05, 3)

    out_path = project_dir / "segments.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # Print a quick quality report to the server log.
    _log_assembly_quality(result, total_duration)
    if deterministic_report:
        _log_image_sync_verification(project_id, deterministic_report)

    return result


def _deterministic_segment_rows_from_whisper(
    mj_data: list,
    audio_data: dict,
    total_duration: float,
) -> tuple[list[dict], list[dict]]:
    """
    Deterministic Step 6 mapping from one global token alignment:
    - Flatten all MJ lines into one token stream.
    - Globally align that stream to Whisper words once.
    - Convert aligned word indices back into per-line first/last indices.
    """
    words = load_alignment_word_list(audio_data.get("words") or [])
    if not words:
        return [], []
    audio_tokens = [_norm_karaoke_token(w.get("word", "")) for w in words]
    if not any(audio_tokens):
        return [], []
    line_spans = _build_global_line_word_index_map(mj_data, audio_tokens)
    if not line_spans or len(line_spans) != len(mj_data):
        return [], []

    rows: list[dict] = []
    report: list[dict] = []
    wn = len(words)

    for i, seg in enumerate(mj_data):
        first_idx = max(0, min(int(line_spans[i]["first_word_idx"]), wn - 1))
        last_idx = max(first_idx, min(int(line_spans[i]["last_word_idx"]), wn - 1))
        ratio = float(line_spans[i].get("coverage_ratio", 0) or 0)
        start_time = round(float(words[first_idx].get("start", 0) or 0), 3)
        end_time = round(float(words[last_idx].get("end", start_time) or start_time), 3)
        if end_time <= start_time:
            end_time = round(start_time + 0.05, 3)
        matched = ratio >= 0.999
        rows.append(
            {
                "segment_id": seg.get("segment_id", i + 1),
                "start_time": start_time,
                "end_time": end_time,
                "matched": matched,
                "match_ratio": round(float(ratio * 100.0), 2),
                "method": "deterministic_whisper_line",
            }
        )
        report.append(
            {
                "segment_id": seg.get("segment_id", i + 1),
                "line_index": i,
                "expected_start": start_time,
                "expected_end": end_time,
                "actual_start": start_time,
                "actual_end": end_time,
                "pass": True,
                "score": round(float(ratio * 100.0), 2),
            }
        )

    return rows, report


def _build_global_line_word_index_map(mj_data: list, audio_tokens: list[str]) -> list[dict]:
    line_tokens: list[list[str]] = []
    seg_token_offsets: list[int] = []
    flat_script_tokens: list[str] = []
    for seg in mj_data:
        seg_token_offsets.append(len(flat_script_tokens))
        toks = [_norm_karaoke_token(t) for t in _script_tokens(seg.get("segment_text") or "")]
        toks = [t for t in toks if t]
        line_tokens.append(toks)
        flat_script_tokens.extend(toks)
    if not flat_script_tokens:
        return []

    mapping = _nw_align(flat_script_tokens, audio_tokens)
    n_lines = len(mj_data)
    spans: list[dict] = []
    last_assigned = -1
    for i in range(n_lines):
        lo = seg_token_offsets[i]
        hi = seg_token_offsets[i + 1] if i + 1 < n_lines else len(flat_script_tokens)
        matched_word_idx = [m for m in mapping[lo:hi] if m is not None]
        if matched_word_idx:
            first = int(min(matched_word_idx))
            last = int(max(matched_word_idx))
            covered = len(matched_word_idx)
        else:
            # Deterministic gap fill between nearest mapped neighbors.
            left = None
            for k in range(lo - 1, -1, -1):
                if mapping[k] is not None:
                    left = int(mapping[k])
                    break
            right = None
            for k in range(hi, len(mapping)):
                if mapping[k] is not None:
                    right = int(mapping[k])
                    break
            if left is None and right is None:
                first = max(0, last_assigned + 1)
                last = first
            elif left is None:
                first = max(0, right - 1)
                last = first
            elif right is None:
                first = max(left + 1, last_assigned + 1)
                last = first
            else:
                first = max(left + 1, last_assigned + 1)
                last = min(max(first, right - 1), right)
            covered = 0
        if first <= last_assigned:
            first = last_assigned + 1
        if last < first:
            last = first
        last_assigned = last
        token_count = max(hi - lo, 1)
        spans.append(
            {
                "first_word_idx": first,
                "last_word_idx": last,
                "coverage_ratio": covered / token_count,
            }
        )
    if spans:
        max_idx = max(0, len(audio_tokens) - 1)
        for s in spans:
            s["first_word_idx"] = max(0, min(int(s["first_word_idx"]), max_idx))
            s["last_word_idx"] = max(int(s["first_word_idx"]), min(int(s["last_word_idx"]), max_idx))
    return spans




def _log_image_sync_verification(project_id: str, report_rows: list[dict]) -> None:
    """
    Log expected vs actual timing for every scene after deterministic placement.
    """
    failures = 0
    for row in report_rows:
        ok = bool(row.get("pass"))
        if not ok:
            failures += 1
        print(
            "[image-sync-verify]"
            f" project={project_id!r}"
            f" scene={row.get('segment_id')}"
            f" line={row.get('line_index')}"
            f" expected=({float(row.get('expected_start', 0)):.3f},{float(row.get('expected_end', 0)):.3f})"
            f" actual=({float(row.get('actual_start', 0)):.3f},{float(row.get('actual_end', 0)):.3f})"
            f" score={float(row.get('score', 0)):.2f}"
            f" pass={ok}",
            flush=True,
        )
    print(
        f"[image-sync-verify] project={project_id!r} total={len(report_rows)} failures={failures}",
        flush=True,
    )


def _log_assembly_quality(segments: list, total_duration: float) -> None:
    """Print a one-line quality summary to the server log after every assembly."""
    n = len(segments)
    if n == 0:
        print("[assembly-qc] no segments", flush=True)
        return
    matched = sum(1 for s in segments if s.get("matched"))
    durations = [s.get("end", 0) - s.get("start", 0) for s in segments]
    max_dur = max(durations) if durations else 0
    min_dur = min(durations) if durations else 0
    avg_dur = sum(durations) / len(durations) if durations else 0
    bad = [s["segment_id"] for s in segments if (s.get("end", 0) - s.get("start", 0)) > 30]
    pct = round(matched / n * 100, 1)
    print(
        f"[assembly-qc] segments={n} matched={matched}/{n} ({pct}%)"
        f" dur min={min_dur:.2f}s avg={avg_dur:.2f}s max={max_dur:.2f}s"
        + (f" SUSPICIOUS>{30}s: {bad}" if bad else ""),
        flush=True,
    )


def validate_assembly(segments: list, total_duration: float) -> dict:
    """
    Run quality checks on assembled segments.  Returns a dict with:
    - pass: bool  (True when the assembly looks correct enough to export)
    - match_pct: float  — fraction whose timing came from NW (not fallback/interpolation)
    - timing_pct: float — fraction with acceptable duration (> 0.5 s)
    - issues: list[str]  (human-readable warnings)
    - problem_scenes: list[int]  (segment_ids with suspicious timing)
    """
    issues: list[str] = []
    problem_scenes: list[int] = []
    n = len(segments)

    if n == 0:
        return {
            "pass": False, "match_pct": 0.0, "timing_pct": 0.0,
            "issues": ["No segments"], "problem_scenes": [],
        }

    matched = sum(1 for s in segments if s.get("matched"))
    match_pct = round(matched / n * 100, 1)

    durations = []
    for s in segments:
        start = float(s.get("start", 0) or 0)
        end = float(s.get("end", 0) or 0)
        dur = end - start
        durations.append(dur)
        sid = s.get("segment_id")
        if dur > 30:
            issues.append(f"Scene {sid} duration {dur:.1f}s is suspiciously long (>{30}s).")
            problem_scenes.append(sid)
        elif dur <= 0:
            issues.append(f"Scene {sid} has zero or negative duration.")
            problem_scenes.append(sid)
        elif dur < 0.5:
            issues.append(f"Scene {sid} duration {dur:.2f}s is very short (<0.5s).")
            problem_scenes.append(sid)

    # Check monotonicity
    for i in range(1, len(segments)):
        if segments[i].get("start", 0) < segments[i - 1].get("start", 0):
            issues.append(
                f"Scene {segments[i].get('segment_id')} starts before scene "
                f"{segments[i - 1].get('segment_id')} — order is wrong."
            )
            problem_scenes.append(segments[i].get("segment_id"))

    # Check last segment ends near total_duration
    if segments and total_duration > 0:
        last_end = float(segments[-1].get("end", 0) or 0)
        drift = abs(last_end - total_duration)
        if drift > 2.0:
            issues.append(
                f"Last scene ends at {last_end:.2f}s but audio is {total_duration:.2f}s "
                f"({drift:.2f}s drift)."
            )

    good_timing = sum(1 for d in durations if d >= 0.5)
    timing_pct = round(good_timing / n * 100, 1)

    # NW vs proportional starts — wording depends on whether scene lengths already pass.
    if match_pct < 80:
        issues.insert(
            0,
            f"Only {match_pct}% of scenes locked tight NW windows — "
            f"the rest use proportional spacing (review if speech feels early/late).",
        )
    elif match_pct < 95 and timing_pct < 90:
        issues.insert(
            0,
            f"{match_pct}% tight NW match — others use proportional/interpolated starts; "
            f"re-sync can help if cuts feel off.",
        )
    elif match_pct < 95 and timing_pct >= 90:
        issues.insert(
            0,
            f"Note: {match_pct}% of starts used strict NW windows; the rest are "
            f"proportional — scene length checks still look fine.",
        )

    ok = timing_pct >= 95 and len(problem_scenes) == 0
    return {
        "pass": ok,
        # match_pct: % whose start came from NW text-alignment (not fallback)
        "match_pct": match_pct,
        # timing_pct: % with acceptable duration — the real quality signal
        "timing_pct": timing_pct,
        "matched": matched,
        "total": n,
        "issues": issues,
        "problem_scenes": list(dict.fromkeys(problem_scenes)),  # deduplicated
        "avg_duration_sec": round(sum(durations) / max(len(durations), 1), 2),
        "max_duration_sec": round(max(durations) if durations else 0, 2),
    }


def _first_alpha_token(text: str) -> str:
    """First alphabetic word in a Whisper token (e.g. 'Compare' / 'Here's')."""
    raw = (text or "").strip()
    if not raw:
        return ""
    m = re.match(r"^[^a-zA-Z]*([a-zA-Z]+)", raw)
    return m.group(1).lower() if m else ""


# If the current line already has several words, start a new caption before these (spoken clause boundaries).
_PHRASE_LINE_STARTERS = frozenset({
    "and",
    "but",
    "or",
    "nor",
    "so",
    "because",
    "which",
    "when",
    "while",
    "though",
    "although",
    "then",
    "also",
    "plus",
    "yet",
    "still",
    "compare",
    "same",
    "here",
    "however",
    "instead",
    "meanwhile",
    "besides",
    "actually",
    "now",
    "well",
    "okay",
    "look",
    "see",
    "both",
    "either",
    "neither",
    "another",
    "finally",
    "eventually",
    "suddenly",
    "basically",
    "obviously",
})


def _merge_lonely_numeric_lines(lines: list, max_words: int = 3) -> list:
    """Whisper sometimes leaves a 1–3 digit token alone (e.g. '40' before 'That's'); merge into next line."""
    if len(lines) < 2:
        return lines
    out: list = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        words = cur.get("words") or []
        if (
            i + 1 < len(lines)
            and len(words) == 1
            and re.fullmatch(r"\d{1,3}", (words[0].get("word") or "").strip())
        ):
            nxt = lines[i + 1]
            nwords = nxt.get("words") or []
            merged_words = [dict(x) for x in words] + [dict(x) for x in nwords]
            if len(merged_words) <= max_words:
                _snap_subtitle_line_timings(merged_words)
                out.append(
                    {
                        "start": merged_words[0]["start"],
                        "end": merged_words[-1]["end"],
                        "text": " ".join(x["word"] for x in merged_words),
                        "words": merged_words,
                    }
                )
                i += 2
                continue
        out.append(cur)
        i += 1
    return out


def _snap_subtitle_line_timings(line_words: list) -> None:
    """
    If intra-line gaps are already small (aligned audio), preserve word timings.
    Otherwise proportionally distribute time so karaoke fills the cue (matches ASS export).
    """
    if len(line_words) < 2:
        return
    gaps = []
    for i in range(len(line_words) - 1):
        g = float(line_words[i + 1].get("start", 0)) - float(line_words[i].get("end", 0))
        gaps.append(max(g, 0))
    if gaps and max(gaps) <= _SUBTITLE_SNAP_ONLY_IF_MAX_GAP_SEC:
        return
    start = float(line_words[0]["start"])
    end = float(line_words[-1]["end"])
    span = max(end - start, 0.001)
    weights = [max(float(w["end"]) - float(w["start"]), 0.0001) for w in line_words]
    sw = sum(weights)
    t = start
    for i, w in enumerate(line_words):
        dur = span * (weights[i] / sw)
        w["start"] = round(t, 3)
        t += dur
        w["end"] = round(t, 3)
    line_words[-1]["end"] = round(end, 3)


def _line_cps(text: str, start: float, end: float) -> float:
    if end <= start:
        return 0.0
    return len((text or "").replace("\n", "")) / (end - start)


def _split_overspeed_lines(lines: list, max_cps: float = 20.0) -> list:
    """Split a line that exceeds max CPS into two timed lines (half words each)."""
    out: list = []
    for line in lines:
        st = float(line.get("start", 0))
        en = float(line.get("end", st))
        text = line.get("text", "") or ""
        words = line.get("words") or []
        if not words or len(words) < 2 or _line_cps(text, st, en) <= max_cps:
            out.append(line)
            continue
        mid = len(words) // 2
        w1 = [dict(x) for x in words[:mid]]
        w2 = [dict(x) for x in words[mid:]]
        # Keep ASR word boundaries; overspeed split is only for readability.
        out.append(
            {
                "start": w1[0]["start"],
                "end": w1[-1]["end"],
                "text": " ".join(x["word"] for x in w1),
                "words": [{"word": x["word"], "start": x["start"], "end": x["end"]} for x in w1],
            }
        )
        out.append(
            {
                "start": w2[0]["start"],
                "end": w2[-1]["end"],
                "text": " ".join(x["word"] for x in w2),
                "words": [{"word": x["word"], "start": x["start"], "end": x["end"]} for x in w2],
            }
        )
    return out


def _remap_words_linear(
    words: list,
    old_lo: float,
    old_hi: float,
    new_lo: float,
    new_hi: float,
) -> list:
    """
    When caption cue bounds change, map word timings linearly so karaoke rhythm
    tracks speech instead of squashing all highlights into the shortened window.
    """
    if not words:
        return []
    old_span = max(float(old_hi) - float(old_lo), 1e-6)
    new_span = max(float(new_hi) - float(new_lo), 1e-6)
    out: list = []
    for w in words:
        ws = float(w.get("start", old_lo) or old_lo)
        we = float(w.get("end", ws) or ws)
        ws = max(float(old_lo), min(ws, float(old_hi)))
        we = max(ws, min(we, float(old_hi)))
        fa = (ws - float(old_lo)) / old_span
        fb = (we - float(old_lo)) / old_span
        nws = float(new_lo) + fa * new_span
        nwe = float(new_lo) + fb * new_span
        if nwe <= nws:
            nwe = min(float(new_hi), nws + 0.03)
        out.append(
            {
                "word": w.get("word", ""),
                "start": round(nws, 3),
                "end": round(min(nwe, float(new_hi)), 3),
            }
        )
    for i in range(len(out) - 1):
        if float(out[i]["end"]) > float(out[i + 1]["start"]):
            mid = (float(out[i]["end"]) + float(out[i + 1]["start"])) / 2
            out[i]["end"] = round(mid, 3)
            out[i + 1]["start"] = round(mid, 3)
    return out


def _stabilize_subtitle_lines(lines: list, min_duration: float = 0.28) -> list:
    """
    Final subtitle pass (word-locked):
    - Keep per-word timings as the authoritative audio clock.
    - Build cue bounds from first/last word with tiny lead/tail padding.
    - Prevent cue overlap without warping word-level karaoke timings.
    """
    if not lines:
        return lines

    # Cue start must match spoken first-word onset exactly (no early display).
    lead_in = 0.0
    tail_out = 0.06
    overlap_gap = 0.02
    out = [dict(line) for line in lines]
    n = len(out)

    for i in range(n):
        line = out[i]
        words = line.get("words") or []
        if words:
            # Keep word timing untouched (except existing upstream clamping).
            ws0 = float(words[0].get("start", line.get("start", 0)) or 0)
            we1 = float(words[-1].get("end", line.get("end", ws0)) or ws0)
            start = max(0.0, ws0 - lead_in)
            end = max(start + 0.04, we1 + tail_out)
            line["start"] = round(start, 3)
            line["end"] = round(end, 3)
        else:
            start = float(line.get("start", 0) or 0)
            end = float(line.get("end", start) or start)
            if end <= start:
                end = start + min_duration
            line["start"] = round(start, 3)
            line["end"] = round(end, 3)

    # Final overlap cleanup: never shift current cue start away from word onset.
    # If overlap occurs, only trim previous cue end.
    for i in range(1, n):
        prev = out[i - 1]
        cur = out[i]
        cur_start = float(cur["start"])
        if float(cur["end"]) <= float(cur["start"]):
            cur["end"] = round(float(cur["start"]) + min(0.08, min_duration), 3)
        if float(prev["end"]) > float(cur["start"]) - overlap_gap:
            prev["end"] = round(max(float(prev["start"]) + 0.06, float(cur["start"]) - overlap_gap), 3)

    return out


def build_subtitle_lines(
    audio_corrected_json,
    max_words_per_line: int = 3,
    max_chars_per_line: int = 42,
    pause_flush_sec: float = 0.25,
    min_words_before_pause_break: int = 2,
    min_words_before_starter_break: int = 2,
    min_words_before_comma_break: int = 2,
    max_cps: float = 20.0,
) -> list:
    """
    Build caption lines from Whisper words (2–3 word bursts, clause/pause breaks, CPS cap).
    """
    words = prepare_karaoke_word_list(audio_corrected_json.get("words"))
    if not words:
        return []

    lines: list = []
    line_words: list = []

    def flush():
        nonlocal line_words
        if not line_words:
            return
        _snap_subtitle_line_timings(line_words)
        start = line_words[0]["start"]
        end = line_words[-1]["end"]
        text = " ".join(w["word"] for w in line_words)
        lines.append({
            "start": start,
            "end": end,
            "text": text,
            "words": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in line_words],
        })
        line_words = []

    for w in words:
        word_text = w["word"]

        # Noticeable pause → new phrase (works even when there is no punctuation in the transcript).
        if line_words and len(line_words) >= min_words_before_pause_break:
            gap = float(w.get("start", 0)) - float(line_words[-1].get("end", 0))
            if gap >= pause_flush_sec:
                flush()

        # Spoken pivot words (Compare him to… / Same job… / But she…).
        if line_words and len(line_words) >= min_words_before_starter_break:
            if _first_alpha_token(word_text) in _PHRASE_LINE_STARTERS:
                flush()

        # Hard cap: never more than `max_words_per_line` words on one caption (default 3).
        if line_words and len(line_words) >= max_words_per_line:
            flush()

        cand = (" ".join(x["word"] for x in line_words) + " " + word_text).strip() if line_words else word_text
        if line_words and len(cand) > max_chars_per_line:
            flush()

        line_words.append(dict(w))

        stripped = word_text.rstrip()
        if stripped.endswith((".", "!", "?")):
            flush()
        elif stripped.endswith((",", ";", ":")) and len(line_words) >= min_words_before_comma_break:
            flush()

    if line_words:
        flush()

    lines = _merge_lonely_numeric_lines(lines, max_words_per_line)
    lines = _split_overspeed_lines(lines, max_cps=max_cps)
    lines = _stabilize_subtitle_lines(lines)
    return lines


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return float(xs[lo] * (1.0 - frac) + xs[hi] * frac)


def _ffmpeg_decode_mono_16k(audio_path: Path, max_seconds: float) -> tuple[int, list[int]]:
    """
    Decode audio to mono 16k signed PCM samples via ffmpeg.
    Returns (sample_rate, samples).
    """
    sr = 16000
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-ss",
        "0",
        "-t",
        f"{max(1.0, float(max_seconds)):.3f}",
        "-i",
        str(audio_path),
        "-ac",
        "1",
        "-ar",
        str(sr),
        "-f",
        "s16le",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=True)
    raw = proc.stdout
    if not raw:
        return sr, []
    import array
    a = array.array("h")
    a.frombytes(raw)
    return sr, a.tolist()


def _build_rms_envelope(samples: list[int], sr: int) -> list[float]:
    n = len(samples)
    if n <= 0:
        return []
    win = max(1, int(sr * 0.02))
    sq = [0.0] * (n + 1)
    for i, x in enumerate(samples, start=1):
        fx = float(x) / 32768.0
        sq[i] = sq[i - 1] + fx * fx
    rms = [0.0] * n
    half = win // 2
    for i in range(n):
        a = max(0, i - half)
        b = min(n, a + win)
        span = max(1, b - a)
        rms[i] = ((sq[b] - sq[a]) / span) ** 0.5
    return rms


def _detect_word_onset_sec(rms: list[float], sr: int, word_start: float) -> tuple[float, bool]:
    """
    Lightweight onset estimate near Whisper word.start using RMS threshold crossing.
    """
    n = len(rms)
    if n <= 0:
        return float(word_start), False

    def t_to_i(t: float) -> int:
        return max(0, min(n - 1, int(t * sr)))

    pre0 = max(0.0, word_start - 0.35)
    pre1 = max(0.0, word_start - 0.08)
    s0 = max(0.0, word_start - 0.08)
    s1 = min(n / sr, word_start + 0.8)
    i_pre0, i_pre1 = t_to_i(pre0), t_to_i(pre1)
    i0, i1 = t_to_i(s0), t_to_i(s1)
    if i1 <= i0 + 2:
        return float(word_start), False

    pre = rms[i_pre0:i_pre1] if i_pre1 > i_pre0 else rms[max(0, i0 - 400):i0]
    local = rms[i0:i1]
    if not local:
        return float(word_start), False

    # Percentile helper (small vectors here, sort is fine).
    def pctl(vals: list[float], p: float) -> float:
        return _quantile(vals, p / 100.0) if vals else 0.0

    baseline = pctl(pre, 70)
    peak = pctl(local, 98)
    if (peak - baseline) < 0.0025:
        return float(word_start), False
    threshold = max(baseline * 2.0, baseline + (peak - baseline) * 0.2)
    j = None
    for k, v in enumerate(local):
        if v > threshold:
            j = i0 + k
            break
    if j is None:
        return float(word_start), False
    return float(j / sr), True


def _subtitle_delta_stats_ms(deltas_sec: list[float]) -> dict:
    if not deltas_sec:
        return {"p50": 0.0, "p75": 0.0, "p90": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "p50": round(_quantile(deltas_sec, 0.50) * 1000.0, 1),
        "p75": round(_quantile(deltas_sec, 0.75) * 1000.0, 1),
        "p90": round(_quantile(deltas_sec, 0.90) * 1000.0, 1),
        "p95": round(_quantile(deltas_sec, 0.95) * 1000.0, 1),
        "max": round(max(deltas_sec) * 1000.0, 1),
    }


def _map_align_word_indices_to_kw_indices(
    align_words: list[dict], kw_words: list[dict]
) -> dict[int, int]:
    """Map load_alignment word index → prepare_karaoke word index (same stream order)."""
    m: dict[int, int] = {}
    j = 0
    for i, aw in enumerate(align_words):
        ast = float(aw.get("start", 0) or 0)
        while j < len(kw_words) and float(kw_words[j].get("start", 0) or 0) < ast - 0.02:
            j += 1
        if j < len(kw_words) and abs(float(kw_words[j].get("start", 0) or 0) - ast) < 0.08:
            m[i] = j
            j += 1
    return m


def _build_large_silence_first_word_kw_indices(
    project_dir: Path, audio_data: dict
) -> tuple[set[int], int, list[dict]]:
    """
    Long silence between image segments: Whisper's first-word start after the gap may drift.
    Returns karaoke word indices for those first words, boundary count, debug rows.
    """
    mj_path = project_dir / "mj_prompts.json"
    if not mj_path.is_file():
        return set(), 0, []
    with open(mj_path, "r", encoding="utf-8") as f:
        mj_data = json.load(f)
    if not isinstance(mj_data, list) or not mj_data:
        return set(), 0, []
    align_words = load_alignment_word_list(audio_data.get("words") or [])
    kw_words = prepare_karaoke_word_list(audio_data.get("words") or [])
    if not align_words or not kw_words:
        return set(), 0, []
    audio_tokens = [_norm_karaoke_token(w.get("word", "")) for w in align_words]
    spans = _build_global_line_word_index_map(mj_data, audio_tokens)
    if not spans:
        return set(), 0, []
    idx_map = _map_align_word_indices_to_kw_indices(align_words, kw_words)
    target_kw: set[int] = set()
    detail: list[dict] = []
    boundary_count = 0
    for seg_i in range(1, len(spans)):
        fi = int(spans[seg_i]["first_word_idx"])
        li_prev = int(spans[seg_i - 1]["last_word_idx"])
        prev_end = float(align_words[li_prev].get("end", 0) or 0)
        cur_start = float(align_words[fi].get("start", 0) or 0)
        gap = cur_start - prev_end
        if gap > LARGE_SILENCE_INTER_SEGMENT_GAP_SEC:
            boundary_count += 1
            kw_i = idx_map.get(fi)
            if kw_i is not None:
                target_kw.add(kw_i)
            detail.append(
                {
                    "segment_id_after": mj_data[seg_i].get("segment_id", seg_i + 1),
                    "gap_sec": round(gap, 3),
                    "first_align_word_idx": fi,
                    "kw_idx": kw_i,
                }
            )
    return target_kw, boundary_count, detail


def _build_fast_speech_kw_indices(kw_words: list[dict]) -> tuple[set[int], int]:
    """Windows of FAST_SPEECH_WINDOW_WORDS consecutive words with local wps > FAST_SPEECH_WPS_THRESHOLD."""
    n = len(kw_words)
    win = FAST_SPEECH_WINDOW_WORDS
    if n < win:
        return set(), 0
    window_starts: list[int] = []
    fast: set[int] = set()
    for i in range(0, n - win + 1):
        w0 = kw_words[i]
        w4 = kw_words[i + win - 1]
        t0 = float(w0.get("start", 0) or 0)
        t4 = float(w4.get("end", w4.get("start", 0)) or 0)
        dur = t4 - t0
        if dur <= 0:
            continue
        wps = win / dur
        if wps > FAST_SPEECH_WPS_THRESHOLD:
            window_starts.append(i)
            for j in range(i, i + win):
                fast.add(j)
    return fast, len(window_starts)


def apply_adaptive_subtitle_word_onset_correction(
    lines: list[dict],
    audio_path: Path,
    *,
    threshold_ms: float = SUBTITLE_ONSET_EARLY_THRESHOLD_MS,
    target_margin_ms: float = SUBTITLE_ONSET_TARGET_MARGIN_MS,
    max_lines_for_measure: int | None = None,
    project_dir: Path | None = None,
    audio_data: dict | None = None,
) -> tuple[list[dict], dict]:
    """
    Adaptive per-word subtitle correction (localized only):
    - Default: robust onset + Whisper early by > threshold_ms → shift by (delta - target_margin).
    - Fix 1: large inter-segment silence → first word after gap uses waveform onset when robust.
    - Fix 2: fast speech → use FAST_SPEECH_EARLY_THRESHOLD_MS (60ms) instead of 100ms for those words.
    """
    try:
        sampled = lines if max_lines_for_measure is None else lines[: max(1, int(max_lines_for_measure))]
        sampled_words: list[tuple[int, int]] = []
        for li, line in enumerate(sampled):
            for wi, _ in enumerate(line.get("words") or []):
                sampled_words.append((li, wi))
        if not sampled_words:
            return lines, {"reason": "no_words", "before_ms": _subtitle_delta_stats_ms([]), "after_ms": _subtitle_delta_stats_ms([])}

        fix1_kw: set[int] = set()
        fix1_boundary_count = 0
        fix1_detail: list[dict] = []
        fix2_kw: set[int] = set()
        fix2_window_count = 0
        if project_dir is not None and audio_data is not None:
            fix1_kw, fix1_boundary_count, fix1_detail = _build_large_silence_first_word_kw_indices(
                project_dir, audio_data
            )
            kw_for_fast = prepare_karaoke_word_list(audio_data.get("words") or [])
            fix2_kw, fix2_window_count = _build_fast_speech_kw_indices(kw_for_fast)

        probe_end = 0.0
        for li, wi in sampled_words:
            w = (sampled[li].get("words") or [])[wi]
            probe_end = max(probe_end, float(w.get("start", 0) or 0))
        probe_end += 1.0
        if not audio_path.exists():
            return lines, {"reason": "audio_missing", "before_ms": _subtitle_delta_stats_ms([]), "after_ms": _subtitle_delta_stats_ms([])}
        sr, samples = _ffmpeg_decode_mono_16k(audio_path, probe_end)
        if not samples:
            return lines, {"reason": "audio_decode_failed", "before_ms": _subtitle_delta_stats_ms([]), "after_ms": _subtitle_delta_stats_ms([])}
        rms = _build_rms_envelope(samples, sr)
        if not rms:
            return lines, {"reason": "audio_decode_failed", "before_ms": _subtitle_delta_stats_ms([]), "after_ms": _subtitle_delta_stats_ms([])}

        out = [dict(line) for line in lines]
        for i, line in enumerate(out):
            out[i] = {**line, "words": [dict(w) for w in (line.get("words") or [])]}

        threshold_sec = max(0.0, float(threshold_ms) / 1000.0)
        fast_threshold_sec = max(0.0, float(FAST_SPEECH_EARLY_THRESHOLD_MS) / 1000.0)
        target_sec = max(0.0, float(target_margin_ms) / 1000.0)

        before_deltas: list[float] = []
        after_deltas: list[float] = []
        before_fix1: list[float] = []
        after_fix1: list[float] = []
        before_fix2_only: list[float] = []
        after_fix2_only: list[float] = []
        before_other: list[float] = []
        after_other: list[float] = []

        corrected_words = 0
        robust_words = 0
        fix1_onset_applied = 0
        fix2_threshold_corrections = 0

        gidx = -1
        for li, wi in sampled_words:
            words = out[li].get("words") or []
            if wi >= len(words):
                continue
            gidx += 1
            w = words[wi]
            ws = float(w.get("start", 0) or 0)
            we = float(w.get("end", ws) or ws)
            aud, robust = _detect_word_onset_sec(rms, sr, ws)
            delta = (aud - ws) if robust else 0.0
            before_deltas.append(delta if robust else 0.0)

            in_fix1 = gidx in fix1_kw
            in_fix2_only = (gidx in fix2_kw) and not in_fix1
            if in_fix1:
                before_fix1.append(delta if robust else 0.0)
            elif in_fix2_only:
                before_fix2_only.append(delta if robust else 0.0)
            else:
                before_other.append(delta if robust else 0.0)

            nws = ws
            nwe = we
            applied_fix1 = False
            if robust:
                robust_words += 1
                if in_fix1:
                    # Replace Whisper start with audible onset when detector is robust.
                    nws = aud
                    nwe = we + (aud - ws)
                    applied_fix1 = True
                    fix1_onset_applied += 1
                if not applied_fix1:
                    thr = fast_threshold_sec if in_fix2_only else threshold_sec
                    if delta > thr:
                        shift = max(0.0, delta - target_sec)
                        nws = ws + shift
                        nwe = we + shift
                        corrected_words += 1
                        if in_fix2_only:
                            fix2_threshold_corrections += 1

            if wi + 1 < len(words):
                next_start = float(words[wi + 1].get("start", nwe) or nwe)
                nwe = min(nwe, next_start - 0.001)
            if nwe <= nws:
                nwe = nws + 0.03
            w["start"] = round(nws, 3)
            w["end"] = round(nwe, 3)

            res_after = (aud - nws) if robust else 0.0
            after_deltas.append(res_after)
            if in_fix1:
                after_fix1.append(res_after)
            elif in_fix2_only:
                after_fix2_only.append(res_after)
            else:
                after_other.append(res_after)

        out = _stabilize_subtitle_lines(out)
        report = {
            "threshold_ms": float(threshold_ms),
            "fast_speech_threshold_ms": float(FAST_SPEECH_EARLY_THRESHOLD_MS),
            "target_margin_ms": float(target_margin_ms),
            "large_silence_gap_sec": float(LARGE_SILENCE_INTER_SEGMENT_GAP_SEC),
            "fast_speech_wps_threshold": float(FAST_SPEECH_WPS_THRESHOLD),
            "fast_speech_window_words": int(FAST_SPEECH_WINDOW_WORDS),
            "sampled_lines": len(sampled),
            "sampled_words": len(sampled_words),
            "total_words_processed": len(sampled_words),
            "robust_words": robust_words,
            "corrected_words": corrected_words,
            "fix1_segment_boundaries_gt_cap": fix1_boundary_count,
            "fix1_first_words_targeted": len(fix1_kw),
            "fix1_waveform_onset_applied": fix1_onset_applied,
            "fix1_detail": fix1_detail[:24],
            "fix2_window_count": fix2_window_count,
            "fix2_words_in_fast_windows": len(fix2_kw),
            "fix2_threshold_corrections": fix2_threshold_corrections,
            "unchanged_words": max(0, len(sampled_words) - corrected_words - fix1_onset_applied),
            "before_ms": _subtitle_delta_stats_ms(before_deltas),
            "after_ms": _subtitle_delta_stats_ms(after_deltas),
            "before_ms_fix1_words": _subtitle_delta_stats_ms(before_fix1),
            "after_ms_fix1_words": _subtitle_delta_stats_ms(after_fix1),
            "before_ms_fix2_only_words": _subtitle_delta_stats_ms(before_fix2_only),
            "after_ms_fix2_only_words": _subtitle_delta_stats_ms(after_fix2_only),
            "before_ms_other_words": _subtitle_delta_stats_ms(before_other),
            "after_ms_other_words": _subtitle_delta_stats_ms(after_other),
        }
        return out, report
    except Exception as e:
        return lines, {"reason": f"adaptive_failed:{type(e).__name__}", "before_ms": _subtitle_delta_stats_ms([]), "after_ms": _subtitle_delta_stats_ms([])}
