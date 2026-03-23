import json
from pathlib import Path
from rapidfuzz import fuzz


def map_segments_to_timestamps(project_id, project_dir) -> list:
    project_dir = Path(project_dir)
    mj_path = project_dir / "mj_prompts.json"
    audio_path = project_dir / "audio_corrected.json"
    if not audio_path.exists():
        audio_path = project_dir / "audio_transcript.json"

    with open(mj_path, "r", encoding="utf-8") as f:
        mj_data = json.load(f)
    with open(audio_path, "r", encoding="utf-8") as f:
        audio_data = json.load(f)

    words = audio_data["words"]
    total_duration = audio_data.get("duration", words[-1]["end"]) if words else 0.0

    last_matched_pos = 0
    result = []

    for seg in mj_data:
        segment_id = seg.get("segment_id", 0)
        segment_text = seg.get("segment_text", "")
        seg_words = segment_text.split()
        probe_str = ""

        best_ratio = 0.0
        best_pos = 0
        matched = False
        matched_probe_size = 0

        if seg_words and words:
            for probe_size, threshold in [(6, 75), (4, 70), (3, 65), (2, 60)]:
                if len(seg_words) < probe_size or len(words) < probe_size:
                    continue

                probe_str = " ".join(seg_words[:probe_size])
                current_best_ratio = 0.0
                current_best_pos = last_matched_pos

                for i in range(last_matched_pos, len(words) - probe_size + 1):
                    window_words = [words[i + j]["word"] for j in range(probe_size)]
                    window_str = " ".join(window_words)
                    ratio = fuzz.ratio(probe_str, window_str)
                    if ratio > current_best_ratio:
                        current_best_ratio = ratio
                        current_best_pos = i
                    if ratio >= 90:
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
            start_time = words[best_pos]["start"]
            last_matched_pos = best_pos + matched_probe_size
            if last_matched_pos > len(words):
                last_matched_pos = len(words)
        else:
            total_segments = len(mj_data)
            current_index = len(result)
            estimated_start = (current_index / total_segments) * total_duration if total_segments else 0.0
            start_time = estimated_start

        result.append({
            "segment_id": segment_id,
            "image": str(segment_id) + ".png",
            "start": round(float(start_time or 0), 3),
            "end": None,
            "text": segment_text,
            "matched": matched,
            "match_ratio": round(best_ratio, 2) if probe_str else 0.0,
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


def build_subtitle_lines(audio_corrected_json, max_words_per_line=7) -> list:
    words = audio_corrected_json.get("words", [])
    if not words:
        return []

    lines = []
    line_words = []
    line_start = None

    def flush():
        nonlocal line_words, line_start
        if not line_words:
            return
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
        line_start = None

    for w in words:
        word_text = w["word"]
        if line_start is None:
            line_start = w["start"]
        line_words.append(w)
        ends_sentence = any(word_text.rstrip().endswith(c) for c in ".!?,")
        if ends_sentence or len(line_words) >= max_words_per_line:
            flush()

    if line_words:
        flush()

    return lines
