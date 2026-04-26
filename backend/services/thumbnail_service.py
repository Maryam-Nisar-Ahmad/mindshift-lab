"""Three YouTube thumbnail prompts for Grok — LLM-backed (OpenAI preferred, DeepSeek optional).

Prompt markdown: thumbnail-system-prompt.md (fallback: deepseek-thumbnail-system-prompt.md).

Set OPENAI_API_KEY and THUMBNAIL_PROMPT_PROVIDER=openai (default when key present), or THUMBNAIL_PROMPT_PROVIDER=deepseek.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)


def _frontend_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def load_thumbnail_system_prompt() -> str:
    root = _frontend_root()
    for name in ("thumbnail-system-prompt.md", "deepseek-thumbnail-system-prompt.md"):
        p = root / name
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


# Machine-parseable JSON for the app (object root works with OpenAI json_object mode).
_JSON_CONTRACT = """

---
CRITICAL — API OUTPUT (overrides prose templates above):
Return ONLY valid JSON (no markdown fences, no commentary before or after).
Use this exact shape (key name must be "variants"):
{"variants":[
  {"title": "short label for this variant", "grok_prompt": "one English paragraph ..."},
  {"title": "...", "grok_prompt": "..."},
  {"title": "...", "grok_prompt": "..."}
]}

Legacy alternative accepted by parser: a raw JSON array of 3 objects with the same title/grok_prompt keys.

Each grok_prompt MUST be ONE dense flowing paragraph that mechanically follows the **Mandatory clause sequence** in the document (output type, camera/crop, main subject with exhaustive literal detail, secondary element or explicit none, background, lighting, color/contrast, text or explicit none, safe zones, negatives, hook sentence). Target **140–280 words** per paragraph when a human face or complex scene is used; symbolic-only may be shorter but must still cover every clause. Do not output thin generic prompts.

Do NOT match in-video illustration, anime, cel shading, or scene pipeline style. Photoreal / premium symbolic only.
"""


def _thumbnail_temperature() -> float:
    for key in ("THUMBNAIL_PROMPT_TEMPERATURE", "DEEPSEEK_THUMBNAIL_TEMPERATURE"):
        raw = os.getenv(key)
        if raw:
            try:
                return max(0.0, min(2.0, float(raw)))
            except ValueError:
                break
    return 0.35


def _thumbnail_provider() -> str:
    explicit = (os.getenv("THUMBNAIL_PROMPT_PROVIDER") or "").strip().lower()
    if explicit in ("openai", "deepseek"):
        return explicit
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "deepseek"


def _parse_thumbnail_json(raw: str) -> list:
    raw = re.sub(r"^\s*```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    data = json.loads(raw)
    if isinstance(data, dict) and "variants" in data:
        data = data["variants"]
    if not isinstance(data, list) or len(data) != 3:
        raise ValueError("Expected variants array of 3 thumbnail objects")
    return data


async def generate_three_thumbnail_prompts(
    *,
    title: str,
    script_excerpt: str,
) -> list[dict]:
    base = load_thumbnail_system_prompt()
    if not base:
        raise RuntimeError(
            "thumbnail-system-prompt.md or deepseek-thumbnail-system-prompt.md not found in project root"
        )

    system = base + _JSON_CONTRACT

    provider = _thumbnail_provider()
    temperature = _thumbnail_temperature()

    user = (
        f"VIDEO TITLE:\n{title}\n\n"
        f"SCRIPT (extract concrete nouns, props, settings, emotions — use verbatim where useful):\n{script_excerpt[:12000]}\n\n"
        "TASK: Fill every mandatory clause for THREE variants. Do not invent plot not implied by title+script; "
        "you may specify stock-photo-plausible detail (wardrobe, lighting) that fits the topic. "
        "Return JSON only using the variants shape from the system instructions.\n"
    )

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "THUMBNAIL_PROMPT_PROVIDER=openai requires OPENAI_API_KEY in backend/.env"
            )
        client = AsyncOpenAI(api_key=api_key)
        model = os.getenv("OPENAI_THUMBNAIL_MODEL", "gpt-4o")
        try:
            max_out = int(os.getenv("OPENAI_THUMBNAIL_MAX_TOKENS", "8192"))
        except ValueError:
            max_out = 8192
        max_out = max(512, min(32768, max_out))
        resp = await client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_out,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
    else:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Set OPENAI_API_KEY for thumbnail prompts (recommended) or DEEPSEEK_API_KEY with THUMBNAIL_PROMPT_PROVIDER=deepseek"
            )
        client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        resp = await client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=8192,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

    raw = (resp.choices[0].message.content or "").strip()
    data = _parse_thumbnail_json(raw)
    out = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "variant": i + 1,
                "title": str(item.get("title", f"Option {i+1}")),
                "grok_prompt": str(item.get("grok_prompt", "")),
            }
        )
    if len(out) != 3:
        raise ValueError("Thumbnail response must contain 3 valid items")
    return out
