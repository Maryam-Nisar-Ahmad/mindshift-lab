"""Validate subtitle lines before export (cues, CPS, durations)."""

from __future__ import annotations


def line_cps(text: str, start: float, end: float) -> float:
    if end <= start:
        return 999.0
    chars = len((text or "").replace("\n", ""))
    dur = end - start
    return chars / dur if dur > 0 else 999.0


def validate_subtitle_lines(
    lines: list[dict],
    *,
    max_cps: float = 21.0,
    min_duration_sec: float = 0.28,
    max_duration_sec: float = 7.0,
) -> tuple[bool, list[str]]:
    errs: list[str] = []
    prev_end = -1.0
    for i, line in enumerate(lines):
        start = float(line.get("start", 0) or 0)
        end = float(line.get("end", 0) or 0)
        text = line.get("text") or ""
        if end <= start:
            errs.append(f"cue {i}: negative or zero duration")
            continue
        if start < prev_end - 0.001:
            errs.append(f"cue {i}: overlaps previous cue")
        prev_end = end
        dur = end - start
        if dur < min_duration_sec:
            errs.append(f"cue {i}: duration {dur:.2f}s < {min_duration_sec}s")
        if dur > max_duration_sec:
            errs.append(f"cue {i}: duration {dur:.2f}s > {max_duration_sec}s")
        cps = line_cps(text, start, end)
        if cps > max_cps:
            errs.append(f"cue {i}: CPS {cps:.1f} > {max_cps}")
    ok = len(errs) == 0
    return ok, errs
