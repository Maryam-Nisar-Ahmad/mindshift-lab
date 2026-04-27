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
  {"title": "short label for this variant", "grok_prompt": "one English paragraph ...", "review": "SCROLL TEST: Strong Stop. FORMULA: Named Concept Trap. MOBILE: yes. SPECIFICITY: derived from script. CTR Potential: HIGH — reason."},
  {"title": "...", "grok_prompt": "...", "review": "..."},
  {"title": "...", "grok_prompt": "...", "review": "..."}
]}

THREE VARIANTS — THREE DIFFERENT visual styles AND three different emotional entry points. Variant A = bold illustrated character portrait (Pain Mirror). Variant B = typographic-dominant design (Named Concept / Trap / Paradox). Variant C = illustrated conceptual scene or second character portrait with different angle. Same visual style twice = failure.

Each grok_prompt MUST be ONE dense flowing paragraph (no internal line breaks) that begins with an explicit style declaration and covers all 10 mandatory clauses: (1) style declaration including "NOT a 3D render, NOT CGI, NOT photorealistic" for character variants, (2) layout/crop with exact positioning, (3) main subject with exhaustive physical detail including extreme expression, (4) ONE KEY WORD in text highlighted in a contrasting colour or underlined, (5) secondary element or explicit none, (6) background with exact hex code, (7) lighting and atmosphere, (8) colour palette 2-3 named colours, (9) text overlay exact words ALL CAPS bold Impact-style thick black stroke with specific placement, (10) safe zones + negative prompt including "NO YouTube logo, NO YouTube watermark, NO platform UI elements, NO badges, NO play button icons, NO social media icons" + hook sentence. Target 150-240 words. DO NOT mention "YouTube thumbnail" or any dimensions or aspect ratios in the prompt — this causes Grok to embed platform UI elements in the image.

EXPRESSION RULE: Every human face must have a cartoonishly extreme expression readable at 116x65px. NOT "slightly frustrated." Use physical body-level descriptors: "brow forcing a deep angry V-crease between the eyes, jaw jutting forward with visible clenched-muscle tension at jaw corners, lips pulled tight into a grimace, both fists clenched visible at frame edge."

STYLE A ART STYLE ANTI-3D RULE: Variant A MUST explicitly include in the grok_prompt: "2D flat editorial illustration style, thick ink outlines, NOT a 3D render, NOT CGI, NOT photorealistic" — Grok defaults to 3D video-game-style characters without this. The target style is flat editorial cartoon illustration (think Escaping Ordinary / Antidote thechannel), NOT Pixar or video game art.

STYLE B RULE: Variant B must be typographic-dominant — text occupies 50-65% of visual space, ONE simple iconic illustration (brain, broken chain, mouse trap, hourglass, one figure in crowd, anchor), NO close-up face. This is the After Skool "THE BEGINNING IS NEAR" / "MIND TRAPS" / Nischa "BORING BUT RICH" visual strategy. The text IS the emotional hook.

VARIANT C TEXT RULE: Variant C MUST include a text overlay of 2-3 words. No-text thumbnails only work when the concept is instantaneously readable in under 0.3 seconds (e.g., one green figure in a sea of grey). Most conceptual scenes are NOT that instant — without text, the viewer's brain cannot attach a meaning quickly enough before their thumb scrolls past.

TEXT FORMULA RULE: Every thumbnail text must use one of these proven formulas: (a) THE [SPECIFIC CONCEPT] TRAP, (b) PARADOX/COUNTERINTUITIVE ("SLEPT 8 HOURS, STILL TIRED?", "BORING BUT PRODUCTIVE"), (c) #1 SIGN OF [ASPIRATIONAL CONCEPT], (d) NAMED COINED CONCEPT specific to this script ("DOPAMINE DEBT", "QUIET DISCIPLINE", "REST PARADOX"), (e) STOP [SPECIFIC ACTION FROM THIS SCRIPT — not generic], (f) [NUMBER] [SITUATION], STILL [NEGATIVE OUTCOME]?. MANDATORY SELF-CHECK BEFORE WRITING TEXT: Ask "could this text appear on a motivational poster for any topic?" — if yes, it is WRONG. BANNED DEAD PHRASES (these are real data failures from channels getting 6K-12K views while competitors get 300K+ with specific text): "START NOW", "ACT NOW", "DO IT NOW", "BEGIN TODAY", "JUST DO IT", "TAKE ACTION", "LASER FOCUS", "WIN AT LIFE", "WAKE UP CALL", "LEVEL UP", "UNFAIR ADVANTAGE", "SECRET HACKS", "NEVER ENOUGH", "CHANGE YOUR LIFE", "THE TRUTH", "HUSTLE", "UNLOCK YOUR POTENTIAL", "MINDSET SHIFT", "BE GREAT", "MAKE IT HAPPEN" — these create exactly zero curiosity gap.

CONCEPT RULE: Variant C must NOT use overused self-help visual metaphors: glowing door representing opportunity, person running toward light, butterfly from cocoon, stairs to clouds, road into horizon. Proven WORKING concepts: one figure in a sea of identical grey figures (277K+ views), before/after split composition, person trapped in a specific literal version of the script's trap, illustrated metaphor specific to THIS script's mechanism.

ONE WORD HIGHLIGHT RULE: In every variant, identify the single most important word in the text overlay and describe it as a contrasting colour or underlined — this is the Dan Koe model proven to focus the viewer's eye on the exact concept that matters (e.g., the word "TRAP" in red while rest is white, or "BORING" underlined while "BUT RICH" is normal weight).

Each review MUST answer: SCROLL TEST (Strong Stop/Maybe/Weak), FORMULA USED (name the formula from the list above), SPECIFICITY CHECK (is text derived from THIS script or could it appear on any self-help video?), MOBILE at 116x65px (yes/borderline/no). End with: CTR Potential: HIGH/MEDIUM/LOW — one sentence on the single most important strength or weakness.
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
        f"SCRIPT (read carefully — extract: the single most counterintuitive claim, the viewer's exact pain state, what 'trap' the viewer is in, what named concept the video teaches):\n{script_excerpt[:12000]}\n\n"
        "TASK: Generate THREE thumbnail prompts for Grok, each targeting a different emotional entry point.\n\n"
        "BEFORE WRITING: Identify (a) the single most counterintuitive claim in the script, "
        "(b) the TRAP name for what the viewer is unknowingly doing wrong, "
        "(c) a coined specific concept name that could be the mechanism the video explains.\n\n"
        "Variant A — PAIN MIRROR: Bold illustrated character portrait. [CHARACTER_B] (exhausted/frustrated) or [CHARACTER_A] (overwhelmed). "
        "EXTREME cartoonish expression — physical body-level descriptors, not 'slightly' anything. "
        "CRITICAL: your grok_prompt MUST include 'flat 2D editorial illustration, thick ink outlines, NOT 3D render, NOT CGI, NOT photorealistic' — Grok defaults to 3D video game art style without this explicit instruction. "
        "Text: maximum 3 words from the TRAP or PARADOX formula. "
        "ONE key word in contrasting colour. Background dark (deep navy/charcoal/crimson with hex code).\n\n"
        "Variant B — NAMED CONCEPT/[REDACTED CONCEPT]: Typographic-dominant — text fills 50-65% of frame. "
        "ONE simple iconic illustration (NOT a face). No close-up character portrait. "
        "Like After Skool's 'THE BEGINNING IS NEAR' or Nischa's 'BORING BUT RICH' or 'STOP BUYING THIS'. "
        "Use the counterintuitive claim or coined concept name from the script. "
        "ONE key word in contrasting colour or underlined.\n\n"
        "Variant C — SCENE/TRANSFORMATION: Illustrated conceptual scene OR [CHARACTER_A] character with different emotional angle. "
        "MUST include a 2-3 word text overlay derived from THIS script's specific concept — NEVER 'START NOW', 'ACT NOW', 'BEGIN TODAY', or any generic call-to-action. "
        "Good text examples for scene variants: the named concept ('THE WAITING TRAP'), or a specific coined phrase ('QUIET ACTION'), or a script-specific paradox. "
        "Consider: one figure clearly different from a sea of grey identical figures (proven 277K+ formula), "
        "or [CHARACTER_A] (relieved/determined) with a short positive script-specific text, or a scene showing the literal mechanism of THIS script's trap. "
        "Background warmer (teal/green/orange with hex code).\n\n"
        "FINAL CHECK before returning JSON: verify that NONE of the three text overlays contain: 'START NOW', 'ACT NOW', 'DO IT NOW', 'BEGIN TODAY', 'TAKE ACTION', 'LEVEL UP', 'WIN AT LIFE', 'LASER FOCUS', 'WAKE UP CALL'. If any do, rewrite that text with a formula from the system instructions.\n"
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
        entry: dict = {
            "variant": i + 1,
            "title": str(item.get("title", f"Option {i+1}")),
            "grok_prompt": str(item.get("grok_prompt", "")),
        }
        review = item.get("review")
        if review:
            entry["review"] = str(review)
        out.append(entry)
    if len(out) != 3:
        raise ValueError("Thumbnail response must contain 3 valid items")
    return out
