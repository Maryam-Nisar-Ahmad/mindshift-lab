"""Write SRT and WebVTT sidecar files for YouTube upload."""

from __future__ import annotations


def _srt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def _vtt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def write_srt(path, lines: list[dict], offset_sec: float = 0.0) -> None:
    parts: list[str] = []
    for idx, line in enumerate(lines, start=1):
        start = float(line.get("start", 0) or 0) + offset_sec
        end = float(line.get("end", start) or start) + offset_sec
        text = (line.get("text") or "").strip()
        parts.append(str(idx))
        parts.append(f"{_srt_ts(max(0, start))} --> {_srt_ts(max(start + 0.04, end))}")
        parts.append(text)
        parts.append("")
    path.write_text("\n".join(parts), encoding="utf-8")


def write_vtt(path, lines: list[dict], offset_sec: float = 0.0) -> None:
    parts = ["WEBVTT", ""]
    for line in lines:
        start = float(line.get("start", 0) or 0) + offset_sec
        end = float(line.get("end", start) or start) + offset_sec
        text = (line.get("text") or "").strip()
        parts.append(f"{_vtt_ts(max(0, start))} --> {_vtt_ts(max(start + 0.04, end))}")
        parts.append(text)
        parts.append("")
    path.write_text("\n".join(parts), encoding="utf-8")
