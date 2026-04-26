"""
Optional WhisperX forced alignment (research.md §1): refines OpenAI Whisper word timings.

Runs on CPU if no CUDA device is available (slower but no GPU required).

Install (CPU-only): pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
                   pip install whisperx
GPU optional: CUDA wheel + whisperx — see requirements.txt notes.
Safe no-op when WhisperX/torch unavailable.
"""

from __future__ import annotations

import gc
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

NUMBER_RE = re.compile(r"\d")


def _norm_lang(lang: str | None) -> str:
    if not lang:
        return "en"
    x = str(lang).lower().strip()
    mapping = {
        "english": "en",
        "en": "en",
        "eng": "en",
    }
    return mapping.get(x, x[:2] if len(x) >= 2 else "en")


def _segments_for_whisperx(data: dict) -> list[dict]:
    """WhisperX expects segment dicts with start, end, text."""
    out: list[dict] = []
    for seg in data.get("segments") or []:
        out.append(
            {
                "start": float(seg.get("start", 0) or 0),
                "end": float(seg.get("end", 0) or 0),
                "text": (seg.get("text") or "").strip(),
            }
        )
    return out


def _extract_aligned_words(aligned: dict) -> list[dict]:
    words: list[dict] = []
    for segment in aligned.get("segments") or []:
        for w in segment.get("words") or []:
            words.append(
                {
                    "word": str(w.get("word", "")).strip(),
                    "start": round(float(w.get("start", 0) or 0), 3),
                    "end": round(float(w.get("end", 0) or 0), 3),
                }
            )
    return words


def _fallback_words_from_segments(data: dict) -> list[dict]:
    """Rebuild words from segments / raw Whisper layout."""
    words: list[dict] = []
    for segment in data.get("segments") or []:
        seg_start = float(segment.get("start", 0) or 0)
        seg_end = float(segment.get("end", seg_start) or seg_start)
        if "words" in segment:
            for w in segment["words"]:
                words.append(
                    {
                        "word": str(w.get("word", w.get("text", ""))).strip(),
                        "start": round(float(w.get("start", seg_start)), 3),
                        "end": round(float(w.get("end", seg_end)), 3),
                    }
                )
            continue
        tokens = str(segment.get("text", "")).split()
        if not tokens:
            continue
        duration = max(seg_end - seg_start, 0.001)
        step = duration / len(tokens)
        for j, token in enumerate(tokens):
            words.append(
                {
                    "word": token,
                    "start": round(seg_start + j * step, 3),
                    "end": round(seg_start + (j + 1) * step, 3),
                }
            )
    return words


def _torch_device_for_alignment() -> str:
    """
    WHISPERX_DEVICE env: cuda | cpu | auto (default auto).
    Auto uses CUDA only when PyTorch sees a GPU; otherwise CPU.
    """
    try:
        import torch
    except ImportError:
        return "cpu"
    raw = os.getenv("WHISPERX_DEVICE", "auto").strip().lower()
    if raw == "cuda":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if raw == "cpu":
        return "cpu"
    # auto (default): no GPU → CPU-only pipelines work out of the box
    return "cuda" if torch.cuda.is_available() else "cpu"


def _torch_device_for_demucs() -> str:
    """DEMUCS_DEVICE: cuda | cpu | auto — default prefers CPU when no CUDA."""
    try:
        import torch
    except ImportError:
        return "cpu"
    raw = os.getenv("DEMUCS_DEVICE", "auto").strip().lower()
    if raw == "cuda":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if raw == "cpu":
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def fix_number_timestamps(words: list[dict], raw_segments: list[dict]) -> list[dict]:
    """Mitigate WhisperX digit-word drift (GitHub #1298): tie digits to Whisper segment span."""
    if not words or not raw_segments:
        return words
    fixed: list[dict] = []
    for word in words:
        w = dict(word)
        text = w.get("word", "") or ""
        if NUMBER_RE.search(text):
            w_start = float(w.get("start", 0) or 0)
            for seg in raw_segments:
                s_start = float(seg.get("start", 0) or 0)
                s_end = float(seg.get("end", 0) or s_start)
                if s_start <= w_start <= s_end and s_end > s_start:
                    w["start"] = round(s_start, 3)
                    w["end"] = round(max(s_end, s_start + 0.05), 3)
                    break
        fixed.append(w)
    return fixed


def _demucs_vocals(audio_path: Path, project_dir: Path) -> Path | None:
    """Isolate vocals with Demucs (optional; heavy). Returns path to vocals wav or None."""
    out_root = project_dir / "_demucs_align"
    out_root.mkdir(exist_ok=True)
    stem = audio_path.stem
    try:
        demucs_cmd = [
            sys.executable,
            "-m",
            "demucs",
            "--two-stems=vocals",
            "-n",
            "htdemucs",
            "-d",
            _torch_device_for_demucs(),
            "-o",
            str(out_root),
            str(audio_path),
        ]
        subprocess.run(
            demucs_cmd,
            check=True,
            capture_output=True,
            timeout=3600,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("Demucs failed: %s", e)
        return None
    vocals = out_root / "htdemucs" / stem / "vocals.wav"
    if vocals.exists():
        return vocals
    return None


def apply_whisperx_alignment(
    data: dict[str, Any],
    audio_path: str | Path,
    *,
    use_demucs: bool = False,
    device: str | None = None,
) -> dict[str, Any]:
    """
    Run WhisperX align-only on existing Whisper segments. Falls back to input `data` on any failure.
    Uses CPU automatically when no GPU is present (WHISPERX_DEVICE=auto default).
    """
    try:
        import torch
        import whisperx
    except ImportError:
        logger.info("WhisperX not installed; skipping alignment.")
        return data

    audio_path = Path(audio_path)
    project_dir = audio_path.parent
    lang = _norm_lang(data.get("language"))
    if device is None:
        device = _torch_device_for_alignment()
    elif device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    if device == "cpu":
        cpu_threads = os.getenv("OMP_NUM_THREADS") or os.getenv("TORCH_NUM_THREADS") or ""
        if not cpu_threads:
            physical = os.cpu_count() or 4
            torch.set_num_threads(min(physical, 8))
            torch.set_num_interop_threads(min(physical, 4))
        logger.info("WhisperX alignment using CPU device=%s (no GPU)", device)
    else:
        logger.info("WhisperX alignment using device=%s", device)

    align_path = str(audio_path)
    if use_demucs:
        vpath = _demucs_vocals(audio_path, project_dir)
        if vpath is not None:
            align_path = str(vpath)

    segments_in = _segments_for_whisperx(data)
    if not segments_in and data.get("words"):
        wz = data["words"]
        if wz:
            segments_in = [
                {
                    "start": float(wz[0].get("start", 0) or 0),
                    "end": float(wz[-1].get("end", 0) or 0),
                    "text": " ".join(str(x.get("word", "")) for x in wz),
                }
            ]
    if not segments_in:
        return data

    model_a = None
    try:
        model_a, meta_align = whisperx.load_align_model(language_code=lang, device=device)
        audio = whisperx.load_audio(align_path)
        aligned = whisperx.align(
            segments_in,
            model_a,
            meta_align,
            audio,
            device,
            return_char_alignments=False,
        )
        words = _extract_aligned_words(aligned)
        if not words:
            logger.warning("WhisperX returned no words; keeping original timestamps.")
            return data
        words = fix_number_timestamps(words, data.get("segments") or [])
        out = dict(data)
        out["words"] = words
        out["text"] = " ".join(w["word"] for w in words)
        out["_alignment"] = "whisperx"
        return out
    except Exception as exc:
        logger.warning("WhisperX alignment failed (%r); using Whisper timestamps.", exc)
        return data
    finally:
        if model_a is not None:
            del model_a
        gc.collect()
        try:
            import torch

            if device == "cuda":
                torch.cuda.empty_cache()
        except Exception:
            pass


def alignment_requested(meta: dict[str, Any] | None) -> bool:
    if meta and meta.get("whisperx_align") is True:
        return True
    return os.getenv("WHISPERX_ALIGN_DEFAULT", "").strip().lower() in ("1", "true", "yes", "on")


def demucs_requested(meta: dict[str, Any] | None) -> bool:
    if meta and meta.get("demucs_vocals") is True:
        return True
    return os.getenv("DEMUCS_BEFORE_ALIGN", "").strip().lower() in ("1", "true", "yes", "on")
