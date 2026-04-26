from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

import os
from openai import AsyncOpenAI

SYSTEM_CLEAN = (
    'You are a script cleaner. Remove all [PAUSE] markers, [VISUAL CUE: ...] blocks, hashtags, '
    'markdown formatting (# ** * _), section header lines (ALL CAPS or bold headers), stage directions, '
    'Hook:/Bridge:/Outro: labels. Return ONLY the clean spoken narration. Do not summarize. Do not add anything. '
    'Do not change wording. Return paragraph by paragraph.'
)


async def clean_script(script: str) -> str:
    """Uses DeepSeek only (no OpenAI billing)."""
    deepseek_key = os.getenv('DEEPSEEK_API_KEY')
    if not deepseek_key:
        raise RuntimeError(
            'DEEPSEEK_API_KEY is required for script cleaning. '
            'Set it in backend/.env (same key as the rest of the DeepSeek pipeline).'
        )
    client = AsyncOpenAI(api_key=deepseek_key, base_url='https://api.deepseek.com')
    model = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')
    response = await client.chat.completions.create(
        model=model,
        temperature=0,
        max_tokens=8192,
        messages=[
            {'role': 'system', 'content': SYSTEM_CLEAN},
            {'role': 'user', 'content': script},
        ],
    )
    return (response.choices[0].message.content or '').strip()
