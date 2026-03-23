from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

import os
from openai import AsyncOpenAI


async def clean_script(script: str) -> str:
    client = AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    response = await client.chat.completions.create(
        model='gpt-4o',
        temperature=0,
        messages=[
            {
                'role': 'system',
                'content': 'You are a script cleaner. Remove all [PAUSE] markers, [VISUAL CUE: ...] blocks, hashtags, markdown formatting (# ** * _), section header lines (ALL CAPS or bold headers), stage directions, Hook:/Bridge:/Outro: labels. Return ONLY the clean spoken narration. Do not summarize. Do not add anything. Do not change wording. Return paragraph by paragraph.'
            },
            {
                'role': 'user',
                'content': script
            }
        ]
    )
    return response.choices[0].message.content.strip()
