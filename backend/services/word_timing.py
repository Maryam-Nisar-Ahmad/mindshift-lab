"""Post-process ASR word timings: clamp overlaps, minimum highlight duration, shrink large gaps."""

from __future__ import annotations

MIN_WORD_HIGHLIGHT_SEC = 0.08
MAX_INTRA_GAP_SEC = 0.10
# Gap absorption is disabled: artificially pulling word ends forward / next-word starts backward
# shifts timestamps away from what the ASR actually measured, causing subtitle lines and segment
# alignment anchors to appear up to ~180 ms before the true spoken time.
GAP_ABSORB_THRESHOLD_SEC = 0.0


def words_timestamps_need_refine(words: list[dict]) -> bool:
    """True when overlaps suggest JSON predates refine_word_timestamps (avoid double-refining)."""
    if len(words) < 2:
        return False
    for i in range(len(words) - 1):
        a_end = float(words[i].get("end", 0) or 0)
        b_start = float(words[i + 1].get("start", 0) or 0)
        if a_end > b_start + 0.015:
            return True
    return False


def load_alignment_word_list(raw: list | None) -> list[dict]:
    """
    Words for segment alignment + subtitle building.
    audio_corrected.json is usually already refined at upload — refining again skews timing.
    """
    words = [dict(w) for w in (raw or [])]
    if not words:
        return []
    if words_timestamps_need_refine(words):
        return refine_word_timestamps(words)
    return words


def _refine_pass_one_only(words: list[dict]) -> list[dict]:
    """Overlap clamp + minimum word duration — does not merge inter-word pauses (karaoke-friendly)."""
    if not words:
        return []
    out: list[dict] = []
    n = len(words)
    for i, w in enumerate(words):
        ws = float(w.get("start", 0) or 0)
        we = float(w.get("end", ws) or ws)
        if i + 1 < n:
            nxt_s = float(words[i + 1].get("start", we) or we)
            we = min(we, nxt_s - 0.001)
        dur = we - ws
        if dur < MIN_WORD_HIGHLIGHT_SEC:
            we = ws + MIN_WORD_HIGHLIGHT_SEC
            if i + 1 < n:
                nxt_s = float(words[i + 1].get("start", we) or we)
                we = min(we, nxt_s - 0.001)
        out.append({"word": w.get("word", ""), "start": round(ws, 3), "end": round(we, 3)})
    for i in range(len(out) - 1):
        if float(out[i]["end"]) > float(out[i + 1]["start"]):
            out[i]["end"] = round(float(out[i + 1]["start"]) - 0.001, 3)
    return out


def refine_word_timestamps_karaoke_only(words: list[dict]) -> list[dict]:
    """For subtitles: preserve natural pauses (no midpoint gap merge)."""
    return _refine_pass_one_only(words)


def gentle_pull_apart_merged_gaps(words: list[dict]) -> list[dict]:
    """
    Upload pipeline merges gaps ≤100ms for alignment; that steals ~50ms from each side of a pause.
    Nudge boundaries apart slightly so karaoke highlights track perceived speech better.
    """
    if len(words) < 2:
        return words
    out: list[dict] = [{"word": w.get("word", ""), "start": float(w.get("start", 0) or 0), "end": float(w.get("end", 0) or 0)} for w in words]
    for i in range(len(out) - 1):
        gap = float(out[i + 1]["start"]) - float(out[i]["end"])
        if gap < 0.035:
            shed = min(0.03, max(0.006, (0.055 - gap) / 2))
            out[i]["end"] = round(max(float(out[i]["start"]) + MIN_WORD_HIGHLIGHT_SEC, float(out[i]["end"]) - shed), 3)
            out[i + 1]["start"] = round(min(float(out[i + 1]["end"]) - MIN_WORD_HIGHLIGHT_SEC, float(out[i + 1]["start"]) + shed), 3)
    for i in range(len(out) - 1):
        if float(out[i]["end"]) > float(out[i + 1]["start"]):
            mid = (float(out[i]["end"]) + float(out[i + 1]["start"])) / 2
            out[i]["end"] = round(mid - 0.001, 3)
            out[i + 1]["start"] = round(mid + 0.001, 3)
    return out


def prepare_karaoke_word_list(raw: list | None) -> list[dict]:
    """
    Words for subtitle/caption generation only — prioritizes acoustic timing over contiguous karaoke paint.
    """
    words = [dict(w) for w in (raw or [])]
    if not words:
        return []
    if words_timestamps_need_refine(words):
        words = refine_word_timestamps_karaoke_only(words)
    else:
        words = gentle_pull_apart_merged_gaps(words)
    return words


def refine_word_timestamps(words: list[dict]) -> list[dict]:
    """
    - Clamp word end to not exceed next word start.
    - Enforce minimum duration per word (karaoke visibility).
    - Close tiny gaps (≤ MAX_INTRA_GAP_SEC) by meeting at the midpoint so karaoke highlights flow.
    - Larger gaps (natural pauses) are left untouched to preserve ASR timing accuracy.
    """
    if not words:
        return []
    out = _refine_pass_one_only(words)

    # Second pass: pull word ends toward next starts to close awkward gaps (alignment / NW helper path)
    for i in range(len(out) - 1):
        gap = float(out[i + 1]["start"]) - float(out[i]["end"])
        if gap <= 0:
            continue
        if gap <= MAX_INTRA_GAP_SEC:
            mid = float(out[i]["end"]) + gap / 2
            out[i]["end"] = round(mid, 3)
            out[i + 1]["start"] = round(mid, 3)
        elif gap <= GAP_ABSORB_THRESHOLD_SEC:
            absorb = gap * 0.6
            out[i]["end"] = round(float(out[i]["end"]) + absorb, 3)
            out[i + 1]["start"] = round(float(out[i + 1]["start"]) - (gap - absorb), 3)

    for i in range(len(out) - 1):
        if float(out[i]["end"]) > float(out[i + 1]["start"]):
            out[i]["end"] = round(float(out[i + 1]["start"]) - 0.001, 3)

    return out
