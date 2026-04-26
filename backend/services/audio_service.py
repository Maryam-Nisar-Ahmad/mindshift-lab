from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

import json
import logging
import os
import re
import math
from pathlib import Path
from openai import OpenAI

logger = logging.getLogger(__name__)

MAX_CHUNK_MB = 24
DEEPSEEK_BATCH = 300


def _fmt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _srt_fmt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def _get_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def _split_audio(audio_path: Path, base_dir: Path) -> list:
    size_mb = _get_size_mb(audio_path)
    if size_mb <= MAX_CHUNK_MB:
        return [(str(audio_path), 0.0)]

    try:
        from pydub import AudioSegment
    except ImportError:
        raise RuntimeError("File > 24 MB. Install pydub to auto-split: pip install pydub")

    audio = AudioSegment.from_file(str(audio_path))
    duration_ms = len(audio)
    num_chunks = math.ceil(size_mb / MAX_CHUNK_MB)
    chunk_ms = duration_ms // num_chunks

    chunks = []
    for i in range(num_chunks):
        start = i * chunk_ms
        end = min((i + 1) * chunk_ms, duration_ms)
        chunk_path = base_dir / f"_chunk_{i:03d}.mp3"
        audio[start:end].export(str(chunk_path), format="mp3")
        chunks.append((str(chunk_path), start / 1000.0))

    return chunks


def _transcribe_chunk_openai(client: OpenAI, audio_path: str, offset: float = 0.0) -> dict:
    """Cloud transcription (paid OpenAI API). Opt-in via TRANSCRIPTION_BACKEND=openai."""
    with open(audio_path, "rb") as f:
        resp = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    words = [
        {
            "word": w.word.strip(),
            "start": round(w.start + offset, 3),
            "end": round(w.end + offset, 3),
        }
        for w in (resp.words or [])
    ]

    segments = [
        {
            "id": seg.id,
            "start": round(seg.start + offset, 3),
            "end": round(seg.end + offset, 3),
            "text": seg.text.strip(),
        }
        for seg in (resp.segments or [])
    ]

    return {
        "text": resp.text,
        "language": getattr(resp, "language", "unknown"),
        "segments": segments,
        "words": words,
    }


def _transcribe_chunk_local(audio_path: str, offset: float = 0.0) -> dict:
    """Free local Whisper (faster-whisper). Default pipeline."""
    from .local_transcription import transcribe_file_local

    return transcribe_file_local(audio_path, offset_sec=offset)


def _merge_chunks(results: list) -> dict:
    merged = {
        "text": " ".join(r["text"] for r in results),
        "language": results[0]["language"],
        "segments": [],
        "words": [],
        "duration": 0.0,
    }
    sid = 0
    for r in results:
        for seg in r["segments"]:
            seg["id"] = sid
            sid += 1
            merged["segments"].append(seg)
        merged["words"].extend(r["words"])
    if merged["words"]:
        merged["duration"] = merged["words"][-1]["end"]
    return merged


def _save_srt(data: dict, path: Path) -> None:
    lines = []
    for i, w in enumerate(data["words"], start=1):
        lines += [
            str(i),
            f"{_srt_fmt(w['start'])} --> {_srt_fmt(w['end'])}",
            w["word"],
            "",
        ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _correct_with_deepseek(data: dict, original_script: str) -> dict:
    ds_client = OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
    )
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    words = data["words"]
    total = len(words)
    corrected = []

    for batch_start in range(0, total, DEEPSEEK_BATCH):
        batch_end = min(batch_start + DEEPSEEK_BATCH, total)
        batch = words[batch_start:batch_end]

        word_list = "\n".join(
            f"{i+1}. {w['word']}"
            for i, w in enumerate(batch)
        )

        prompt = f"""You are a transcript correction assistant.

TASK:
- You have a list of words transcribed by Whisper (may contain errors).
- You have the original script the speaker was reading from.
- Correct ONLY words that are wrong — fix spelling, mis-heard words, wrong numbers.
- Keep words that are correct exactly as they are.
- If Whisper captured an ad-libbed word NOT in the script, keep it as-is.
- Return EXACTLY the same number of lines as input — one corrected word per line.
- Format: just the number and corrected word, nothing else. Example:
  1. the
  2. quick
  3. brown

ORIGINAL SCRIPT (for reference):
\"\"\"
{original_script}
\"\"\"

WHISPER WORDS TO CORRECT:
{word_list}

Return only the numbered corrected words. No explanations. No extra text."""

        resp = ds_client.chat.completions.create(
            model=model,
            max_tokens=DEEPSEEK_BATCH * 8,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = resp.choices[0].message.content.strip()

        fixed_words = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^\d+[.)]\s*(.+)$", line)
            if m:
                fixed_words.append(m.group(1).strip())

        if len(fixed_words) != len(batch):
            fixed_words = [w["word"] for w in batch]

        for original_word, corrected_word in zip(batch, fixed_words):
            corrected.append({
                "word": corrected_word,
                "start": original_word["start"],
                "end": original_word["end"],
            })

    corrected_data = dict(data)
    corrected_data["words"] = corrected
    corrected_data["text"] = " ".join(w["word"] for w in corrected)

    return corrected_data


async def process_audio(project_id, project_dir: Path, job_status: dict, job_id: str | None = None) -> None:
    from .job_store import job_put

    def persist_job() -> None:
        if job_id:
            job_put(job_id, dict(job_status))

    audio_path = project_dir / "audio.mp3"
    script_path = project_dir / "script.txt"

    job_status["status"] = "transcribing"
    job_status["percent"] = 10
    persist_job()

    chunks = _split_audio(audio_path, project_dir)

    backend = os.getenv("TRANSCRIPTION_BACKEND", "local").strip().lower()
    if backend in ("openai", "cloud", "api"):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "TRANSCRIPTION_BACKEND=openai requires OPENAI_API_KEY. "
                "Remove TRANSCRIPTION_BACKEND or set it to 'local' for free on-device transcription."
            )
        client = OpenAI(api_key=api_key)
        results = [_transcribe_chunk_openai(client, path, offset) for path, offset in chunks]
    else:
        try:
            results = [_transcribe_chunk_local(path, offset) for path, offset in chunks]
        except ImportError as e:
            raise RuntimeError(
                "Local transcription requires faster-whisper. Install: pip install faster-whisper"
            ) from e

    for path, _ in chunks:
        if "_chunk_" in path and os.path.exists(path):
            os.remove(path)

    data = _merge_chunks(results)

    meta: dict = {}
    meta_path = project_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}

    from .forced_alignment import (
        alignment_requested,
        apply_whisperx_alignment,
        demucs_requested,
    )

    if alignment_requested(meta):
        job_status["status"] = "aligning"
        job_status["percent"] = 28
        persist_job()
        try:
            data = apply_whisperx_alignment(
                data,
                audio_path,
                use_demucs=demucs_requested(meta),
            )
        except Exception:
            logger.exception("Alignment step crashed; continuing with Whisper timestamps.")
        job_status["percent"] = 48
        persist_job()

    with open(project_dir / "audio_transcript.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    _save_srt(data, project_dir / "audio_subtitles.srt")

    job_status["percent"] = 55
    job_status["status"] = "correcting"
    persist_job()

    pre_deepseek = data
    if script_path.exists():
        script_text = script_path.read_text(encoding="utf-8").strip()
        corrected = _correct_with_deepseek(data, script_text)
    else:
        corrected = data

    from .word_timing import refine_word_timestamps

    if corrected.get("words"):
        corrected["words"] = refine_word_timestamps(corrected["words"])
        corrected["text"] = " ".join(w["word"] for w in corrected["words"])
        if corrected["words"]:
            corrected["duration"] = corrected["words"][-1]["end"]

    with open(project_dir / "audio_corrected.json", "w", encoding="utf-8") as f:
        json.dump(corrected, f, indent=2, ensure_ascii=False)

    _save_srt(corrected, project_dir / "audio_corrected.srt")

    words_corrected = sum(
        1 for o, c in zip(pre_deepseek["words"], corrected["words"])
        if o["word"].lower() != c["word"].lower()
    )

    job_status["status"] = "done"
    job_status["percent"] = 100
    final_dur = float(corrected.get("duration") or pre_deepseek.get("duration") or 0)
    job_status["duration"] = final_dur
    job_status["duration_sec"] = final_dur
    job_status["word_count"] = len(corrected.get("words") or [])
    job_status["words_corrected"] = words_corrected
    persist_job()
