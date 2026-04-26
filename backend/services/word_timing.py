"""Post-process ASR word timings: clamp overlaps, minimum highlight duration, shrink large gaps."""

from __future__ import annotations

MIN_WORD_HIGHLIGHT_SEC = 0.08
MAX_INTRA_GAP_SEC = 0.10
GAP_ABSORB_THRESHOLD_SEC = 0.30


def refine_word_timestamps(words: list[dict]) -> list[dict]:
    """
    - Clamp word end to not exceed next word start.
    - Enforce minimum duration per word (karaoke visibility).
    - Absorb gaps > MAX_INTRA_GAP_SEC between consecutive words by extending previous word (proportional feel).
    """
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

    # Second pass: pull word ends toward next starts to close awkward gaps (keep karaoke continuous)
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

    # Final clamp monotonicity
    for i in range(len(out) - 1):
        if float(out[i]["end"]) > float(out[i + 1]["start"]):
            out[i]["end"] = round(float(out[i + 1]["start"]) - 0.001, 3)

    return out
