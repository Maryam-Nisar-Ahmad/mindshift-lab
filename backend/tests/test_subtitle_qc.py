"""Unit tests for subtitle QC helpers."""

import sys
from pathlib import Path

_backend = Path(__file__).resolve().parents[1]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from services.subtitle_qc import line_cps, validate_subtitle_lines


def test_line_cps_basic():
    assert abs(line_cps("hello", 0.0, 1.0) - 5.0) < 1e-6


def test_validate_ok_line():
    ok, errs = validate_subtitle_lines(
        [{"start": 0.0, "end": 1.0, "text": "short"}],
        max_cps=25.0,
    )
    assert ok
    assert errs == []


def test_validate_overlap():
    ok, errs = validate_subtitle_lines(
        [
            {"start": 0.0, "end": 1.0, "text": "a"},
            {"start": 0.5, "end": 1.5, "text": "b"},
        ],
    )
    assert not ok
    assert any("overlaps" in e for e in errs)
