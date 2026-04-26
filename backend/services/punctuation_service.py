"""
Restore commas and sentence-ending punctuation for subtitle lines using DeepSeek
(same client as transcript correction). Skips if no API key or on any error.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

_MAX_LINES = 80


def _split_ws(text: str) -> list[str]:
    return [x for x in (text or "").strip().split() if x]


def punctuate_subtitle_lines(lines: list[dict]) -> list[dict]:
    """
    Add commas / terminal . ? ! without changing word order or word count (whitespace tokens).
    Updates text and each words[].word for karaoke consistency.
    """
    if not lines:
        return lines
    if not os.getenv("DEEPSEEK_API_KEY"):
        return lines

    batch = lines[:_MAX_LINES]
    numbered = []
    for i, line in enumerate(batch):
        t = (line.get("text") or "").strip()
        numbered.append(f"{i + 1}. {t}")

    prompt = """You add punctuation ONLY. Rules:
- Keep the same words in the same order; same number of whitespace-separated tokens per line as input.
- You may attach punctuation to words (e.g. comma after a word, period at end): "hello," counts as ONE token.
- Each input line starts with "N. " — preserve words after that prefix only.
- Output a JSON array of strings, one string per line (no numbers), length """ + str(len(batch)) + """ .
- Use standard English commas where natural and add . ? ! at phrase ends where appropriate.

INPUT LINES:
""" + "\n".join(numbered) + """

Return ONLY the JSON array, no markdown."""

    try:
        client = OpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com",
        )
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        resp = client.chat.completions.create(
            model=model,
            max_tokens=min(4096, 120 * len(batch)),
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        arr = json.loads(raw)
        if not isinstance(arr, list) or len(arr) != len(batch):
            return lines
    except Exception:
        return lines

    out_lines = []
    for orig_line, new_text in zip(lines[: len(batch)], arr):
        if not isinstance(new_text, str):
            out_lines.append(orig_line)
            continue
        old_full = (orig_line.get("text") or "").strip()
        old_words = orig_line.get("words") or []
        os_tokens = _split_ws(old_full)
        ns_tokens = _split_ws(new_text)
        if len(os_tokens) != len(ns_tokens):
            out_lines.append(orig_line)
            continue
        if old_words and len(old_words) != len(os_tokens):
            out_lines.append(orig_line)
            continue
        if not old_words:
            out_lines.append({**orig_line, "text": " ".join(ns_tokens)})
            continue
        merged_words = []
        for wdict, new_tok in zip(old_words, ns_tokens):
            wd = dict(wdict)
            wd["word"] = new_tok
            merged_words.append(wd)
        out_lines.append(
            {
                **orig_line,
                "text": " ".join(ns_tokens),
                "words": merged_words,
            }
        )

    if len(lines) > len(batch):
        out_lines.extend(lines[len(batch) :])
    return out_lines
