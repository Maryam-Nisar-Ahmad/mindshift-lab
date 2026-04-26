"""Unit tests for ASR word timing refinement."""

import sys
from pathlib import Path

_backend = Path(__file__).resolve().parents[1]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from services.word_timing import MIN_WORD_HIGHLIGHT_SEC, refine_word_timestamps


def test_refine_minimum_duration():
    words = [{"word": "hi", "start": 0.0, "end": 0.01}]
    out = refine_word_timestamps(words)
    assert len(out) == 1
    assert float(out[0]["end"]) - float(out[0]["start"]) >= MIN_WORD_HIGHLIGHT_SEC - 1e-6


def test_refine_two_words_no_overlap():
    words = [
        {"word": "a", "start": 0.0, "end": 0.5},
        {"word": "b", "start": 0.2, "end": 1.0},
    ]
    out = refine_word_timestamps(words)
    assert float(out[0]["end"]) <= float(out[1]["start"]) + 1e-3
