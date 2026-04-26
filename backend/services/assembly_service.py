import json
from pathlib import Path
import re

from .word_timing import MAX_INTRA_GAP_SEC, refine_word_timestamps
from .segment_images import resolve_segment_image_basename
from .segment_alignment import compute_segment_starts


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

    aligned = compute_segment_starts(mj_data, words, total_duration)
    result = []
    images_dir = project_dir / "images"

    for seg, row in zip(mj_data, aligned):
        segment_id = seg.get("segment_id", 0)
        segment_text = seg.get("segment_text", "")
        start_time = row["start_time"]
        matched = bool(row.get("matched"))

        img_name = resolve_segment_image_basename(images_dir, segment_id)
        result.append({
            "segment_id": segment_id,
            "image": img_name or (str(segment_id) + ".png"),
            "start": round(float(start_time or 0), 3),
            "end": None,
            "text": segment_text,
            "matched": matched,
            "match_ratio": round(float(row.get("match_ratio") or 0), 2),
        })

    for i in range(len(result)):
        if i < len(result) - 1:
            result[i]["end"] = round(result[i + 1]["start"], 3)
        else:
            result[i]["end"] = round(float(total_duration or 0), 3)

        if result[i]["end"] <= result[i]["start"]:
            result[i]["end"] = round(float(result[i]["start"] or 0) + 4.0, 3)

    out_path = project_dir / "segments.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    return result


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
    if gaps and max(gaps) <= MAX_INTRA_GAP_SEC + 0.02:
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
        _snap_subtitle_line_timings(w1)
        _snap_subtitle_line_timings(w2)
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
    words = audio_corrected_json.get("words", [])
    words = refine_word_timestamps([dict(w) for w in words]) if words else []
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
    return lines
