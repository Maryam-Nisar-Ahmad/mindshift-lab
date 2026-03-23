from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

import json
import os
from openai import AsyncOpenAI

SPLIT_PROMPT = (
    'You are splitting a narration script into visual segments for a fast-paced animated video. RULES: Each segment = ONE image on screen for 3-5 seconds. Ideal 8-12 words per segment (3-5 seconds at 2.5 words/second). Split frequently — at every new idea, every statistic, every emotional beat, and every sentence where the visual would naturally change. Split a long sentence if it is over 14 words and contains two distinct ideas. NEVER merge different ideas into one segment. Min 6 words. Max 14 words. When in doubt, split more — shorter segments create faster cuts and better viewer retention. Return ONLY a JSON array of strings. No markdown. No explanation. SCRIPT: {script_text} Return JSON array now:'
)


async def split_script_to_segments(script_text: str) -> list:
    client = AsyncOpenAI(api_key=os.getenv('DEEPSEEK_API_KEY'), base_url='https://api.deepseek.com')
    prompt = SPLIT_PROMPT.format(script_text=script_text)
    response = await client.chat.completions.create(
        model=os.getenv('DEEPSEEK_MODEL', 'deepseek-chat'),
        temperature=0.3,
        max_tokens=8000,
        messages=[{'role': 'user', 'content': prompt}],
    )
    raw = response.choices[0].message.content.strip()
    lines = raw.split('\n')
    if lines and lines[0].strip().startswith('```'):
        lines = lines[1:]
    if lines and lines[-1].strip() == '```':
        lines = lines[:-1]
    raw = '\n'.join(lines)
    return [s.strip() for s in json.loads(raw.strip()) if s.strip()]
