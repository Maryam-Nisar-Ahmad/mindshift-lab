from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

import json
import os
import re
from openai import AsyncOpenAI

SPLIT_PROMPT = (
    'You are splitting a narration script into visual segments for a fast-paced animated video. RULES: Each segment = ONE image on screen for 3-5 seconds. Ideal 8-12 words per segment (3-5 seconds at 2.5 words/second). Split frequently — at every new idea, every statistic, every emotional beat, and every sentence where the visual would naturally change. Split a long sentence if it is over 14 words and contains two distinct ideas. NEVER merge different ideas into one segment. Min 6 words. Max 14 words. When in doubt, split more — shorter segments create faster cuts and better viewer retention. Return ONLY a JSON array of strings. No markdown. No explanation. SCRIPT: {script_text} Return JSON array now:'
)


def _clean_script_for_splitting(script_text: str) -> str:
    """
    Strip non-narration content that causes the segment splitter to hang or
    produce broken JSON:
      - Markdown table rows (lines starting with |)
      - Phase/section headers (## PHASE, ## CITATIONS, ## RETENTION etc.)
      - Retention engineering map tables
      - Citation reference blocks (lines that look like "| Claim | Citation |")
      - Lines that are only markdown formatting (---, ===, ---)
      - [VISUAL CUE: ...] and other production markers are kept — they help
        the animator but do not break segment JSON parsing.
    """
    lines = script_text.splitlines()
    cleaned: list[str] = []
    in_citation_block = False
    for line in lines:
        stripped = line.strip()
        # Detect start of citations block and skip everything after it
        if re.match(r'^#{1,3}\s*(CITATIONS|CITATION|REFERENCES|SOURCES)', stripped, re.IGNORECASE):
            in_citation_block = True
        if in_citation_block:
            continue
        # Skip markdown table rows
        if stripped.startswith('|'):
            continue
        # Skip pure horizontal rules
        if re.match(r'^[-=]{3,}$', stripped):
            continue
        # Skip phase/section markdown headers that are structural, not narration
        if re.match(r'^#{1,4}\s*(PHASE|RETENTION|HOOK SELECTED|SCORE:|REASON:)', stripped, re.IGNORECASE):
            continue
        # Skip timing/metadata lines like "0:00 – 0:30 · Emotion: ..."
        if re.match(r'^\d+:\d+\s*[–-]', stripped):
            continue
        cleaned.append(line)

    # Collapse runs of 3+ blank lines to 2
    result = re.sub(r'\n{3,}', '\n\n', '\n'.join(cleaned))
    return result.strip()


def _chunk_script_for_split(script_text: str, max_chars: int) -> list[str]:
    """
    Split a long script into chunks that each fit comfortably in one API call
    (input + output). Uses paragraph boundaries where possible.
    """
    script_text = (script_text or '').strip()
    if not script_text:
        return []
    if len(script_text) <= max_chars:
        return [script_text]

    rough: list[str] = []
    paras = re.split(r'\n\s*\n+', script_text)
    cur: list[str] = []
    cur_len = 0

    for p in paras:
        p = p.strip()
        if not p:
            continue
        sep = 2 if cur else 0
        if cur_len + sep + len(p) > max_chars and cur:
            rough.append('\n\n'.join(cur))
            cur = [p]
            cur_len = len(p)
        else:
            cur.append(p)
            cur_len += sep + len(p)
    if cur:
        rough.append('\n\n'.join(cur))

    out: list[str] = []
    for part in rough:
        if len(part) <= max_chars:
            out.append(part)
        else:
            for i in range(0, len(part), max_chars):
                out.append(part[i:i + max_chars])
    return out


def _parse_split_response(raw: str) -> list[str]:
    raw = raw.strip()
    lines = raw.split('\n')
    if lines and lines[0].strip().startswith('```'):
        lines = lines[1:]
    if lines and lines[-1].strip() == '```':
        lines = lines[:-1]
    raw = '\n'.join(lines)
    return [s.strip() for s in json.loads(raw.strip()) if s.strip()]


async def split_script_to_segments(script_text: str) -> list:
    """
    Split full script into segments. Long scripts (e.g. 20–30+ min) are processed
    in multiple API calls so input/output limits never truncate the full segment list.
    Non-narration content (citation tables, markdown headers, phase timestamps) is
    stripped before splitting to prevent JSON parse failures and API hangs.
    """
    script_text = _clean_script_for_splitting(script_text or '')
    if not script_text:
        return []

    chunk_limit = max(3000, int(os.getenv('SCRIPT_SPLIT_CHUNK_CHARS', '9000')))
    # DeepSeek enforces a provider-side max_tokens cap (often 1–8192).
    # Clamp so the job never crashes and truncates the segment list.
    max_out_raw = int(os.getenv('DEEPSEEK_SPLIT_MAX_TOKENS', '8192'))
    max_out = max(1, min(8192, max_out_raw))
    chunks = _chunk_script_for_split(script_text, chunk_limit)

    client = AsyncOpenAI(api_key=os.getenv('DEEPSEEK_API_KEY'), base_url='https://api.deepseek.com')
    model = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')

    all_segments: list[str] = []
    timeout_s = float(os.getenv('DEEPSEEK_SPLIT_TIMEOUT_SECONDS', '90'))
    for chunk in chunks:
        prompt = SPLIT_PROMPT.format(script_text=chunk)
        response = await client.chat.completions.create(
            model=model,
            temperature=0.3,
            max_tokens=max_out,
            messages=[{'role': 'user', 'content': prompt}],
            timeout=timeout_s,
        )
        raw = response.choices[0].message.content or ''
        all_segments.extend(_parse_split_response(raw))

    return all_segments
