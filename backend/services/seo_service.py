"""Automated YouTube SEO package — DeepSeek generates title, description, timestamps, tags, and alt titles.

System prompt: prompts/5_seo.txt (same file used for the manual workflow, extended with JSON contract).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)


def _frontend_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _load_seo_system_prompt() -> str:
    p = _frontend_root() / "prompts" / "5_seo.txt"
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8").strip()


_JSON_CONTRACT = """

---
CRITICAL — API OUTPUT (overrides prose templates above):
Return ONLY valid JSON. No markdown fences, no commentary before or after.
Use this exact shape:
{
  "title": "The single best YouTube title — apply all rules above",
  "description": "Full YouTube description 200-350 words as specified in DELIVERABLE 2",
  "chapter_timestamps": "00:00 — Chapter Name\\n01:30 — Chapter Name\\n... (5-8 chapters as in DELIVERABLE 3)",
  "tags": "tag1, tag2, tag3, ... (25-30 comma-separated tags as in DELIVERABLE 4)",
  "alternative_titles": [
    {"title": "Alt title 1", "formula": "formula name", "viewer_state": "exact viewer this targets", "advantage": "one sentence why this might outperform the primary"},
    {"title": "Alt title 2", "formula": "formula name", "viewer_state": "exact viewer this targets", "advantage": "one sentence why this might outperform the primary"},
    {"title": "Alt title 3", "formula": "formula name", "viewer_state": "exact viewer this targets", "advantage": "one sentence why this might outperform the primary"}
  ]
}

All five fields are required. The title field must be the single best title following all rules above. The description must open with 2-3 compelling hook sentences (NOT 'In this video...'). The chapter_timestamps must use \\n between chapters. The tags must be comma-separated. The alternative_titles array must contain exactly 3 items using different formulas from the ranked list.
"""


class SeoPackage(TypedDict):
    title: str
    description: str
    chapter_timestamps: str
    tags: str
    alternative_titles: list[dict]


def _parse_seo_json(raw: str) -> SeoPackage:
    """Extract JSON from LLM response, handling markdown fences if present."""
    text = raw.strip()
    # Strip markdown fences if model added them despite instructions
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        text = m.group(1).strip()
    # Try to find JSON object directly
    obj_m = re.search(r"\{[\s\S]*\}", text)
    if obj_m:
        text = obj_m.group(0)
    data = json.loads(text)
    return SeoPackage(
        title=str(data.get("title", "")).strip(),
        description=str(data.get("description", "")).strip(),
        chapter_timestamps=str(data.get("chapter_timestamps", "")).strip(),
        tags=str(data.get("tags", "")).strip(),
        alternative_titles=data.get("alternative_titles", []),
    )


async def generate_seo_package(title: str, script_text: str) -> SeoPackage:
    """Call DeepSeek with the SEO system prompt and return a structured SeoPackage."""
    base_system = _load_seo_system_prompt()
    if not base_system:
        raise RuntimeError("prompts/5_seo.txt not found")

    system = base_system + _JSON_CONTRACT

    client = AsyncOpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
    )
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    user = (
        f"VIDEO TITLE (working title — you may improve it):\n{title.strip()}\n\n"
        f"FULL SCRIPT:\n{script_text.strip()[:50000]}\n\n"
        "Generate the complete SEO package. Return JSON only."
    )

    resp = await client.chat.completions.create(
        model=model,
        temperature=0.6,
        max_tokens=4096,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    raw = (resp.choices[0].message.content or "").strip()
    return _parse_seo_json(raw)
