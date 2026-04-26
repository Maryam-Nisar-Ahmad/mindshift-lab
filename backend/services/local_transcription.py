"""
Local speech-to-text with faster-whisper (no OpenAI / no cloud transcription fees).

Downloads model weights once from Hugging Face (free). Runs on CPU by default.
"""

from __future__ import annotations

import logging
import os
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_model = None
_model_lock = Lock()
_model_signature: tuple[str, str, str] | None = None


def _desired_model_config() -> tuple[str, str, str]:
    """Returns (model_size, device, compute_type)."""
    model_size = os.getenv("FASTER_WHISPER_MODEL", "base").strip() or "base"
    device = os.getenv("WHISPER_LOCAL_DEVICE", "cpu").strip().lower()
    if device not in ("cpu", "cuda"):
        device = "cpu"
    if device == "cuda":
        compute_type = os.getenv("WHISPER_LOCAL_COMPUTE_TYPE", "float16").strip() or "float16"
    else:
        compute_type = os.getenv("WHISPER_LOCAL_COMPUTE_TYPE", "int8").strip() or "int8"
    return model_size, device, compute_type


def _get_model():
    """Lazy-load WhisperModel (thread-safe)."""
    global _model, _model_signature
    cfg = _desired_model_config()
    with _model_lock:
        if _model is not None and _model_signature == cfg:
            return _model
        from faster_whisper import WhisperModel

        logger.info(
            "Loading faster-whisper model=%s device=%s compute_type=%s",
            cfg[0],
            cfg[1],
            cfg[2],
        )
        _model = WhisperModel(cfg[0], device=cfg[1], compute_type=cfg[2])
        _model_signature = cfg
        return _model


def transcribe_file_local(audio_path: str, offset_sec: float = 0.0) -> dict[str, Any]:
    """
    Transcribe one audio file; return same shape as OpenAI verbose_json merge piece:
    text, language, segments[], words[]
    """
    path = audio_path
    model = _get_model()
    beam_size = int(os.getenv("WHISPER_LOCAL_BEAM_SIZE", "5") or "5")
    beam_size = max(1, min(beam_size, 10))

    segments_gen, info = model.transcribe(
        path,
        beam_size=beam_size,
        word_timestamps=True,
        vad_filter=os.getenv("WHISPER_LOCAL_VAD", "1").strip() not in ("0", "false", "no"),
    )

    language = getattr(info, "language", None) or "unknown"
    seg_id = 0
    segments_out: list[dict] = []
    words_out: list[dict] = []

    for seg in segments_gen:
        st = round(float(seg.start) + offset_sec, 3)
        en = round(float(seg.end) + offset_sec, 3)
        text = (seg.text or "").strip()
        segments_out.append(
            {
                "id": seg_id,
                "start": st,
                "end": en,
                "text": text,
            }
        )
        seg_id += 1

        if getattr(seg, "words", None):
            for w in seg.words or []:
                raw = (w.word or "").strip()
                if not raw:
                    continue
                words_out.append(
                    {
                        "word": raw,
                        "start": round(float(w.start) + offset_sec, 3),
                        "end": round(float(w.end) + offset_sec, 3),
                    }
                )
        elif text:
            # No word timestamps — spread tokens across segment span
            toks = text.split()
            if toks:
                dur = max(en - st, 0.001)
                step = dur / len(toks)
                for j, tok in enumerate(toks):
                    ws = round(st + j * step, 3)
                    we = round(st + (j + 1) * step, 3)
                    words_out.append({"word": tok, "start": ws, "end": we})

    full_text = " ".join(w["word"] for w in words_out) if words_out else ""
    if not full_text and segments_out:
        full_text = " ".join(s["text"] for s in segments_out)

    return {
        "text": full_text,
        "language": language,
        "segments": segments_out,
        "words": words_out,
    }
