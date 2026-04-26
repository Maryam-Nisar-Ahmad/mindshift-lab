"""DeepSeek: YouTube video title from cleaned script (system prompt from repo markdown)."""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)


def _frontend_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def load_title_system_prompt() -> str:
    p = _frontend_root() / "deepseek-title-system-prompt.md"
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8").strip()


def parse_final_title(raw: str) -> tuple[str, str]:
    """Returns (final_title_one_line, full_raw)."""
    full = (raw or "").strip()
    if not full:
        return "", full
    m = re.search(
        r"FINAL TITLE:\s*(.*?)(?=^\s*FORMULA USED:)",
        full,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    if not m:
        m = re.search(r"FINAL TITLE:\s*(.+)", full, re.IGNORECASE | re.DOTALL)
    block = (m.group(1).strip() if m else "").strip()
    if not block:
        return "", full
    block = block.split("FORMULA USED:")[0].strip()
    one = re.match(r"^\[([^\]]+)\]", block, re.DOTALL)
    if one:
        title = re.sub(r"\s+", " ", one.group(1).strip())
        return title[:120], full
    first = block.split("\n")[0].strip().strip("[]\"' ")
    first = re.sub(r"\s+", " ", first)
    return first[:120], full


async def generate_video_title(script_text: str) -> tuple[str, str]:
    """Call DeepSeek with title system markdown + script; return (parsed_title, raw_response)."""
    system = load_title_system_prompt()
    if not system:
        raise RuntimeError("deepseek-title-system-prompt.md not found in project root")

    client = AsyncOpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    user = (
        "Below is the full cleaned narration script for one video. Apply the system instructions and output in the required format.\n\n"
        "--- SCRIPT START ---\n"
        f"{script_text.strip()[:50000]}\n"
        "--- SCRIPT END ---"
    )
    resp = await client.chat.completions.create(
        model=model,
        temperature=0.7,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    raw = (resp.choices[0].message.content or "").strip()
    parsed, _full = parse_final_title(raw)
    return (parsed or _full.split("\n")[0].strip()[:120]), raw
