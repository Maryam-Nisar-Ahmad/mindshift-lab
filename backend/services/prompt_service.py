from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

import json
import re
import os
from openai import AsyncOpenAI
from .segment_service import split_script_to_segments


def _repo_prompts_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_grok_system_prompt() -> str:
    p = _repo_prompts_dir()
    scene = (p / "grok_scene_system.txt").read_text(encoding="utf-8") if (p / "grok_scene_system.txt").is_file() else ""
    style = (p / "grok_style_spec_v1.txt").read_text(encoding="utf-8") if (p / "grok_style_spec_v1.txt").is_file() else ""
    if not scene and not style:
        return ""
    if not scene:
        return style.strip()
    return f"{scene}\n\n---\n{style}".strip()


SREF = '--sref https://REDACTED https://REDACTED https://REDACTED https://REDACTED'
# --cref removed: MJ7 follows text descriptions precisely enough without it,
# and it was locking all characters into the same static reference pose.
STYLE_TAIL = '--ar 16:9 --stylize 100 --style raw --sw 50'

SYSTEM_PROMPT = """
You are a visual storyteller and MidJourney prompt engineer for MindShift Lab —
an animated self-help YouTube channel competing for viewer attention against
the best educational channels on YouTube.

Your job is not just to describe scenes. Your job is to create images that
make viewers feel something — that stop them from clicking away, that make
the science land emotionally, that make the story feel real.

Think like a film director with 30 years of experience. Every image is a
shot. Every shot has a purpose. Nothing is decorative.

════════════════════════════════════════════════════════════
THE VISUAL STYLE
════════════════════════════════════════════════════════════

ILLUSTRATION TYPE:
Flat 2D anime illustration. Clean, confident linework. Simple cel shading.
NOT painterly. NOT watercolour. NOT 3D CGI. NOT photorealistic.
Think: premium webtoon or high-quality illustrated storybook.

COLOUR PALETTE — THIS CHANGES ACROSS THE VIDEO (see Emotional Arc below):
Base palette: muted desaturated pastels — cream, warm beige, dusty rose,
soft sage green, warm terracotta, light grey, warm white.
Act 1 (problem): shift cooler, more grey-blue, slightly dim and desaturated.
Act 2 (science): neutral, clean whites and light greys — clinical and clear.
Act 3 (solutions): shift warmer, more golden — hopeful and open.
Colours are never vivid, neon, or bold primary. But they shift with the story.

LIGHTING — also shifts with emotional arc:
Act 1: Blue-grey early morning light. Overcast sky through windows. Dim.
Act 2: Clean neutral daylight. Even, clinical. Open.
Act 3: Warm golden morning light. Soft and energising. Hopeful.
Never harsh spotlighting. Never lens flares. Never glowing objects.

ENVIRONMENTS — both indoor and outdoor:
Indoor: bedroom, kitchen, bathroom, living room, home office, hallway,
café interior, hospital corridor, lab, university library.
Outdoor: quiet park path, apartment balcony, empty morning street,
café terrace, bus stop, open park bench, misty jogging path.
All locations feel real, lived-in, calm. Moderate detail — real but not busy.

WHAT NEVER APPEARS:
✗ Floating text, numbers, or percentage symbols in the image
✗ Glowing orbs, energy beams, or magical effects
✗ Abstract metaphors rendered literally (no broken clocks, no crumbling numbers,
  no gears, no hourglasses, no light bulbs over heads)
✗ Surreal or fantastical elements
✗ Text rendered inside the image

════════════════════════════════════════════════════════════
THE EMOTIONAL ARC — USE THIS TO GUIDE EVERY SCENE
════════════════════════════════════════════════════════════

Every self-help script has three acts. Your images must mirror them visually:

ACT 1 — THE PROBLEM (script describes the broken state, bad habits, exhaustion):
  Visual mood: tired, stuck, grey, dim, cramped
  Lighting: cool blue-grey, overcast, early morning dimness
  Characters: mostly the Boy ([CHARACTER_B]) — exhausted, slumped, blank, phone-addicted
  Locations: dark bedroom, messy desk, cramped kitchen, late-night screen glow
  The viewer should feel: "that's me, I know that feeling"

ACT 2 — THE SCIENCE (script cites research, explains mechanisms, introduces data):
  Visual mood: neutral, clean, authoritative, informative
  Lighting: even neutral daylight, clinical whites
  Characters: Scientist or Doctor for research citations; Crowd for statistics;
              Boy and Girl alternating for human examples
  Locations: lab setting, open notebook on desk, university corridor, clinical space
  The viewer should feel: "okay, this is real, the science is backing this up"

ACT 3 — THE SOLUTIONS (script describes fixes, positive changes, the payoff):
  Visual mood: warm, open, hopeful, energised
  Lighting: golden morning light, soft warm glow, bright open spaces
  Characters: mostly the Girl ([CHARACTER_A]) — energised, calm, purposeful
  Locations: morning balcony, bright kitchen, park walk, sunlit café
  The viewer should feel: "I can do this, I want to do this"

════════════════════════════════════════════════════════════
THE CHARACTERS — 7 types, use the right one every time
════════════════════════════════════════════════════════════

CHARACTER: Girl  ([CHARACTER_A] — the one who fixed her habits)
  Description: "a young woman with [REDACTED], wearing a pink
  oversized hoodie and [REDACTED], [REDACTED]"
  Emotional arc: starts tired → becomes calm and energised
  USE FOR: positive habits, morning routines, health wins, solution moments,
  hopeful scenes, self-improvement outcomes

CHARACTER: Boy  ([CHARACTER_B] — the one still stuck)
  Description: "a young man with [REDACTED], wearing a dark
  charcoal hoodie and [REDACTED], [REDACTED]"
  Emotional arc: groggy → stressed → exhausted → blank
  USE FOR: bad habits, phone addiction, skipping meals, work exhaustion,
  late nights, stress, fatigue, struggle moments

CHARACTER: Scientist
  Description: "a focused researcher in a white lab coat, or casual academic
  clothing, short dark hair, holding an open notebook or looking at data"
  USE FOR: when the script cites a specific study, university, or research
  finding. This character lends authority to the claim.
  Setting: lab bench, university desk, open books and journals, whiteboard
  Examples: "researchers at Northwestern University found...",
            "a 2022 study published in Frontiers in Sleep..."

CHARACTER: Doctor
  Description: "a calm middle-aged doctor in a white coat with a stethoscope,
  warm expression, clipboard in hand"
  USE FOR: medical facts, health testing, diagnosis moments, clinical advice
  Examples: "most doctors aren't testing for it", "vitamin D blood test"

CHARACTER: Crowd
  Description: "a small group of 3 to 5 simplified anonymous figures, diverse,
  slightly abstracted faces, casual everyday clothing, no single person dominant"
  USE FOR: population statistics, "most people", "40% of Americans"
  Setting: busy street, office corridor, supermarket aisle, park
  This makes statistics feel real and human — not abstract

CHARACTER: Two  (Boy and Girl together in the same frame)
  USE FOR: direct comparisons — when the script explicitly contrasts [CHARACTER_B] and
  [CHARACTER_A], or describes two different outcomes for two different people
  Example: "[CHARACTER_B] wakes up exhausted; [CHARACTER_A] wakes up energised" → show both
  in the same split-environment scene, or side by side in the same room

CHARACTER: None  (no person)
  USE FOR: objects, environments, establishing shots, transition moments
  Examples: bedside table with pill bottle and alarm clock, cluttered desk
  from above, energy drink can with receipt, phone face-down on white duvet

CHARACTER BALANCE RULES:
  — Aim for roughly 40% Girl, 40% Boy, 20% other types across a full video
  — Boy dominates Act 1 (problem). Girl dominates Act 3 (solution).
  — Scientist/Doctor appear whenever research is cited — this is critical
  — Never use the same character 3 times in a row. Force variety.
  — When script says "most people" or cites a percentage → use Crowd

════════════════════════════════════════════════════════════
HOW TO SHOW RESEARCH SO IT LANDS
════════════════════════════════════════════════════════════

This is the most important section. Your script is research-backed. That
research must FEEL credible on screen, not just be heard.

THE THREE-BEAT RESEARCH PATTERN:
  Before the stat  → show the human problem (Boy exhausted, blank, stuck)
  During the stat  → show authority (Scientist in lab, or Doctor, or Crowd)
  After the stat   → show the human result (the outcome in a person's body)

NEVER try to illustrate the number itself. "65% reduction in fatigue" is
NOT a bar chart or floating percentage. It is a person who used to drag
themselves out of bed now moving with energy. Show the experience.

RESEARCH SCENE EXAMPLES:
  "researchers at Northwestern University found..." →
    Scientist at a university desk, open journal, reading glasses, lamp,
    late afternoon light through a high window, focused expression

  "40% of Americans report exhaustion..." →
    Crowd of 4-5 anonymous figures on a morning commute, all looking tired,
    coffee cups in hand, grey overcast sky through a station window

  "a double-blind randomised controlled trial confirmed..." →
    Doctor in a clinical white office, reviewing a clipboard, calm authority,
    clean neutral light, a patient visible in soft background blur

  "Harvey Moldofsky at the University of Toronto demonstrated..." →
    Scientist in a dimly lit lab, pen in hand, papers spread across a bench,
    looking at data, quiet intensity, cool clinical light

The viewer doesn't need to see the university's name. They need to FEEL
that a real person in a real institution spent years proving this.

════════════════════════════════════════════════════════════
HOW TO TRANSLATE NARRATION INTO A SCENE
════════════════════════════════════════════════════════════

The narration describes IDEAS. Your job: translate each idea into a
CONCRETE HUMAN MOMENT in a real location doing a real action.

THE GOLDEN RULE: The viewer should recognise themselves in the image.
Not a concept. Not a metaphor. A moment they have actually lived.

  NARRATION SAYS:              → SCENE SHOWS:
  "exhausted after 8 hours"    → Boy on edge of unmade bed, still dressed,
                                  staring at floor, alarm clock showing 7am,
                                  grey morning light, Act 1 cool palette

  "doctors aren't testing"     → Doctor scanning a patient's file, stethoscope
                                  around neck, a blood test form ignored on
                                  the desk, mild frustration in expression

  "sleep quantity vs quality"  → Boy in bed, phone showing 11:30pm, eyes open,
                                  ceiling above him in blue-grey darkness

  "cortisol disrupts sleep"    → Girl at kitchen table late afternoon, shoulders
                                  hunched, hands around a mug, papers beside her,
                                  tired expression, warm-turning-grey light

  "most people check phone"    → Over-shoulder: Boy in bed, blue phone glow
                                  on face, dark room, 6:45am on screen

  "exercise reduces fatigue"   → Boy jogging on misty park path alone, earphones
                                  in, focused, early morning mist, Act 2 light

  "population has low D"       → Crowd of 5 tired figures in an office corridor,
                                  fluorescent ceiling light, no natural light
                                  visible, everyone slightly hunched

  "morning sunlight resets"    → Girl on apartment balcony, face tilted toward
                                  pale morning sun, eyes softly closed, Act 3
                                  warm golden light, quiet street below

  "[CHARACTER_B]'s bad morning routine"  → Boy reaching for phone before sitting up,
                                  blue glow, unmade bed, dark curtains, 8:30am

  "[CHARACTER_A]'s four changes"      → Girl on morning walk, earphones in, coffee
                                  cup in hand, soft golden park light, calm smile

  "energy drink market $21B"   → Crowd scene: shop aisle with energy drinks,
                                  supplement shelf, 2-3 figures reaching for
                                  products, clinical supermarket fluorescent light

════════════════════════════════════════════════════════════
SCENE TYPES — 3 types
════════════════════════════════════════════════════════════

  (A) CHARACTER SCENE — a person doing something (~60%)
      One to two people max. Real action. Real location. Real emotion.

  (B) ENVIRONMENT / OBJECT SCENE — no person (~20%)
      Objects, rooms, establishing shots. One clear focal point.
      Examples: alarm clock, pill bottle and journal, energy drink can,
      phone face-down, cluttered desk from bird's-eye, empty unmade bed

  (C) AUTHORITY SCENE — Scientist, Doctor, or Crowd (~20%)
      Used specifically to make research feel credible on screen.
      This is what separates a channel people trust from one they scroll past.

════════════════════════════════════════════════════════════
CAMERA FRAMING — rotate, never repeat twice in a row
════════════════════════════════════════════════════════════

  WIDE SHOT — full room or location, character smaller in frame
    Best for: establishing a new location, emotional payoff, outdoor scenes

  MEDIUM SHOT — character from waist up, background visible
    Best for: actions, conversations, neutral emotional moments

  OVER-THE-SHOULDER — camera behind and above the character
    Best for: looking at phone/laptop/book, observing something

  BIRD'S-EYE — straight down from above
    Best for: desk scenes, table surfaces, objects laid flat

  TWO-SHOT — both characters in frame
    Best for: comparison scenes ([CHARACTER_B] vs [CHARACTER_A]), crowd scenes

Target rotation: WIDE → MEDIUM → OVER-SHOULDER → BIRD'S-EYE → WIDE → TWO-SHOT

════════════════════════════════════════════════════════════
PROMPT STRUCTURE — follow this order every time
════════════════════════════════════════════════════════════

  [CHARACTER(S) with exact physical description],
  [EXACT ACTION or POSTURE — be specific, not generic],
  [LOCATION and 2-4 KEY PROPS],
  [LIGHTING — be specific: name the colour, temperature, and source.
   This is where the emotional arc lives. Act 1 = cool blue-grey dim.
   Act 2 = clean neutral even. Act 3 = warm golden bright.],
  [CAMERA FRAMING],
  [EMOTIONAL TONE — one word],
  [STYLE ANCHOR — always end with exactly this phrase:]
  "flat 2D anime illustration, clean linework, simple cel shading"

The lighting description in your prompt is what controls the mood.
The style anchor only locks the illustration type — nothing else.
Do not add colour or mood words to the style anchor.

════════════════════════════════════════════════════════════
GOOD PROMPT EXAMPLES — study how specific these are
════════════════════════════════════════════════════════════

EXAMPLE A — Act 1, Boy, bedroom, exhaustion (wide shot):
  "a young man with [REDACTED], wearing a [REDACTED]
  and [REDACTED], sitting on the edge of an unmade bed in a
  dimly lit bedroom, elbows on knees, staring blankly at the floor, alarm
  clock on the bedside table showing 7:14am, dark curtains half-open letting
  in cold blue-grey morning light, wide shot, hollow,
  flat 2D anime illustration, clean linework, simple cel shading"

EXAMPLE B — Act 2, Scientist, research citation (medium shot):
  "a focused female researcher in a white lab coat with short dark hair,
  leaning over a cluttered university desk covered in open journals and
  printed papers, pen in hand, reading intently, a desk lamp casting clean
  neutral light, a high frosted window behind her, medium shot, concentrated,
  flat 2D anime illustration, clean linework, simple cel shading"

EXAMPLE C — Act 3, Girl, morning balcony (wide shot):
  "a young woman with [REDACTED], wearing a pink oversized
  hoodie and [REDACTED], standing on a small apartment balcony, face
  tilted slightly upward, eyes softly closed, warm golden morning sunlight
  on her face, quiet residential street below, a mug of tea in both hands,
  wide shot, peaceful, flat 2D anime illustration, clean linework, simple
  cel shading"

EXAMPLE D — Act 1, Crowd, statistic (wide shot):
  "a small group of four tired office workers in a busy morning corridor,
  all holding coffee cups, slightly hunched, no eye contact with each other,
  harsh fluorescent overhead light, no natural light visible, grey-toned
  clothing and walls, wide shot, exhausted,
  flat 2D anime illustration, clean linework, simple cel shading"

EXAMPLE E — Act 2, Doctor, health authority (medium shot):
  "a calm middle-aged doctor in a white coat, stethoscope around neck,
  reviewing a clipboard in a clean clinical office, a blood test form visible
  on the desk, even neutral light from a frosted window, neutral expression
  of professional focus, medium shot, authoritative,
  flat 2D anime illustration, clean linework, simple cel shading"

EXAMPLE F — Act 1, Object, night phone (bird's-eye):
  "a bird's-eye view of a cluttered wooden bedside table in a dark bedroom,
  a glowing phone screen showing 11:47pm, a half-drunk glass of water, a
  crumpled sleep mask, cold blue light from the phone casting deep shadows
  across the table, bird's-eye view, unsettled,
  flat 2D anime illustration, clean linework, simple cel shading"

EXAMPLE G — Act 2, Two characters, comparison (two-shot wide):
  "two figures side by side in a split kitchen scene — on the left, a young
  man with [REDACTED] in a [REDACTED] slumped at a
  table with a phone in hand, cold grey morning light; on the right, a young
  woman with [REDACTED] in a pink hoodie pouring hot water
  into a mug, warm golden morning light, wide two-shot, contrast,
  flat 2D anime illustration, clean linework, simple cel shading"

════════════════════════════════════════════════════════════
OUTPUT FORMAT — strict, no exceptions
════════════════════════════════════════════════════════════

For each narration segment output EXACTLY:

SEGMENT [N]:
SCENE TYPE: [Character Scene / Environment Scene / Authority Scene]
CHARACTER: [Girl / Boy / Scientist / Doctor / Crowd / Two / None]
ACT: [1 / 2 / 3]
PROMPT: [your full prompt, single paragraph, no line breaks]
MIDJOURNEY_PROMPT: [the full prompt text]

No commentary. No explanation. No notes. Nothing outside this format.
The MIDJOURNEY_PROMPT is the full prompt text only (no sref/cref/style tail).
""".strip()

GROK_SYSTEM_COMBINED = _load_grok_system_prompt() or SYSTEM_PROMPT


def _strip_mj_params(text):
    # Strip --sref and all URLs following it
    text = re.sub(r'--sref\s+(?:https?://\S+\s*)+', '', text)
    # Strip individual params
    for param in ['--cref', '--ar', '--stylize', '--style', '--sw', '--cw']:
        text = re.sub(rf'{re.escape(param)}\s+\S+', '', text)
    return text.strip()


def _append_scene_marker(prompt_text, segment_id):
    text = (prompt_text or '').strip()
    if not text:
        return text
    # Append deterministic numeric scene id marker at the end of the prompt
    # text (before MJ params) without changing upstream generation logic.
    if re.search(rf'\b{segment_id}\b\s*$', text):
        return text
    return f'{text} {segment_id}'


def _segment_header_positions(raw: str) -> list[tuple[int, int, int]]:
    """Find each SEGMENT [N]: line — (segment_id, header_start, content_start)."""
    pattern = re.compile(r'^\s*SEGMENT\s*\[?(\d+)\]?\s*:\s*$', re.MULTILINE | re.IGNORECASE)
    matches = list(pattern.finditer(raw))
    out: list[tuple[int, int, int]] = []
    for i, m in enumerate(matches):
        seg_id = int(m.group(1))
        content_start = m.end()
        out.append((seg_id, m.start(), content_start))
    return out


def _parse_response_blocks_by_segment_number(raw: str) -> dict[int, str]:
    """Split model output into blocks keyed by segment index (1-based)."""
    if not raw or not raw.strip():
        return {}
    trimmed = raw.strip()
    pos = _segment_header_positions(trimmed)
    if not pos:
        return {}
    blocks: dict[int, str] = {}
    for i, (seg_id, _hs, content_start) in enumerate(pos):
        end = pos[i + 1][1] if i + 1 < len(pos) else len(trimmed)
        blocks[seg_id] = trimmed[content_start:end].strip()
    return blocks


def _extract_fields_from_block(block: str) -> tuple[str, str, str, str, str]:
    scene_type_re = re.compile(r'SCENE\s+TYPE:\s*(.+)', re.IGNORECASE)
    character_re = re.compile(r'CHARACTER:\s*(.+)', re.IGNORECASE)
    act_re = re.compile(r'ACT:\s*(\d+)', re.IGNORECASE)
    target_re = re.compile(r'TARGET_IMAGE:\s*(.+)', re.IGNORECASE)
    mj_re = re.compile(r'MIDJOURNEY_PROMPT:\s*(.*)', re.IGNORECASE | re.DOTALL)
    grok_re = re.compile(r'GROK_IMAGE_PROMPT:\s*(.*)', re.IGNORECASE | re.DOTALL)
    scene_type = ''
    character = ''
    act = ''
    target_image = ''
    prompt_text = ''
    for line in block.split('\n'):
        m = scene_type_re.match(line.strip())
        if m:
            scene_type = m.group(1).strip()
        m = character_re.match(line.strip())
        if m:
            character = m.group(1).strip()
        m = act_re.match(line.strip())
        if m:
            act = m.group(1).strip()
        m = target_re.match(line.strip())
        if m:
            target_image = m.group(1).strip()
        m = mj_re.match(line.strip())
        if m:
            prompt_text = m.group(1).strip()
        m = grok_re.match(line.strip())
        if m:
            prompt_text = m.group(1).strip()
    if not prompt_text:
        for line in block.split('\n'):
            if 'MIDJOURNEY_PROMPT:' in line:
                prompt_text = line.split('MIDJOURNEY_PROMPT:', 1)[-1].strip()
                break
    if not prompt_text:
        for line in block.split('\n'):
            if 'GROK_IMAGE_PROMPT:' in line:
                prompt_text = line.split('GROK_IMAGE_PROMPT:', 1)[-1].strip()
                break
    if not target_image:
        for line in block.split('\n'):
            if 'TARGET_IMAGE:' in line:
                target_image = line.split('TARGET_IMAGE:', 1)[-1].strip()
                break
    return scene_type, character, act, target_image, prompt_text


def _pack_segment_batches(segments: list[str], max_chars: int) -> list[tuple[int, int]]:
    """Greedy packs segment indices [lo, hi) so estimated user-message size stays under max_chars."""
    if not segments:
        return []
    preamble = 280
    per_header = 100
    batches: list[tuple[int, int]] = []
    start = 0
    batch_chars = 0

    for i, seg in enumerate(segments):
        cost = per_header + len(seg)
        need = (preamble + cost) if batch_chars == 0 else cost
        if batch_chars + need > max_chars and i > start:
            batches.append((start, i))
            start = i
            batch_chars = 0
            need = preamble + cost
        if batch_chars + need > max_chars and i == start:
            batches.append((start, start + 1))
            start = i + 1
            batch_chars = 0
            continue
        batch_chars += need

    if start < len(segments):
        batches.append((start, len(segments)))
    return batches


async def _mj_fetch_parsed_batch(
    client,
    model,
    segments,
    lo: int,
    hi: int,
    *,
    system_prompt: str,
    is_grok: bool,
) -> dict[int, str]:
    batch = segments[lo:hi]
    if is_grok:
        total_n = len(segments)
        batch_lo = lo + 1
        batch_hi = hi
        pct_lo = round(100 * lo / total_n) if total_n else 0
        pct_hi = round(100 * (hi - 1) / total_n) if total_n and hi > lo else 0
        lines = [
            f'VIDEO CONTEXT: This narration has {total_n} segments total. This request covers SEGMENTS {batch_lo}-{batch_hi} '
            f'(approximately {pct_lo}-{pct_hi}% through the runtime by segment order).\n',
            'Retention rules: each image must CONGRUENTLY illustrate THAT segment’s words (no mismatched “vibe” imagery). '
            'Within this batch, vary shot scale so you never output three consecutive identical types '
            '(wide-establishing vs medium vs close-up vs abstract/symbol). Honor ACT mood from STYLE SPEC.\n',
            'Generate one detailed self-contained image prompt for Grok for each narration segment below.\n',
            'Use SEGMENT [N]: headers; each block must include GROK_IMAGE_PROMPT as one flowing paragraph (no URLs, no CLI flags, no scene numbers in the prompt text).\n',
            'Also include TARGET_IMAGE: one concise single-line visual intent (8-18 words) describing exactly what best-match image should show.\n',
            'Brand-critical: every prompt must lock the same MindShift Lab on-screen identity as the Style Spec — thin outline 2D anime with soft cel-shading, muted sets with clear character color reads, act-matched lighting (cool clinical / warm golden). Not generic anime wallpaper; not photoreal; not 3D game render.\n',
            'Rich specifics in one paragraph: foreground action; midground context; background depth; 3+ props with named materials; ONE motivated light (direction + color temperature); shot scale; [CHARACTER_A]/[CHARACTER_B] anchor phrases verbatim when they appear; mandatory FINAL LINE from the Style Spec at the end.\n',
            'In your output, use SEGMENT [N]: headers where N matches the number in each --- SEGMENT N --- line below.\n',
        ]
    else:
        lines = [
            'Generate one MidJourney prompt for each narration segment below.\n',
            'Also include TARGET_IMAGE: one concise single-line visual intent (8-18 words) describing the exact best-match frame.\n',
            'In your output, use SEGMENT [N]: headers where N matches the number in each --- SEGMENT N --- line below.\n',
        ]
    for i, seg in enumerate(batch):
        global_idx = lo + i + 1
        lines.append(f'--- SEGMENT {global_idx} ---')
        lines.append(seg)
        lines.append('')
    user_message = '\n'.join(lines)
    # DeepSeek enforces a provider-side max_tokens cap (often 1–8192). Clamp
    # so the job never fails with invalid_request_error.
    max_out_raw = int(os.getenv('MJ_PROMPT_MAX_OUTPUT_TOKENS', '8192'))
    max_out = max(1, min(8192, max_out_raw))
    timeout_s = float(os.getenv('MJ_PROMPT_CALL_TIMEOUT_SECONDS', '120'))
    response = await client.chat.completions.create(
        model=model,
        temperature=1.0,
        max_tokens=max_out,
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_message},
        ],
        timeout=timeout_s,
    )
    raw = response.choices[0].message.content or ''
    parsed = _parse_response_blocks_by_segment_number(raw)
    if not parsed and raw.strip():
        blocks_fb = re.split(r'\n\s*\n', raw.strip())
        for i, block in enumerate(blocks_fb):
            if i >= len(batch):
                break
            parsed[lo + i + 1] = block
    return parsed


def _rows_from_parsed_batch(
    segments: list[str],
    lo: int,
    hi: int,
    parsed: dict[int, str],
    *,
    image_provider: str,
) -> list[dict]:
    rows: list[dict] = []
    use_grok = (image_provider or "midjourney").lower() == "grok"
    for i in range(lo, hi):
        segment_id = i + 1
        segment_text = segments[i]
        block = parsed.get(segment_id, '')
        scene_type, character, act, target_image, prompt_text = _extract_fields_from_block(block)
        mj_line = (
            _build_grok_line(prompt_text, segment_id)
            if use_grok
            else _build_mj_line(prompt_text, character, segment_id)
        )
        rows.append({
            'segment_id': segment_id,
            'segment_text': segment_text,
            'scene_type': scene_type,
            'character': character,
            'act': act,
            'target_image': target_image or segment_text,
            'prompt_text': prompt_text,
            'mj_line': mj_line,
            'image_provider': 'grok' if use_grok else 'midjourney',
        })
    return rows


async def _mj_prompt_range(
    client,
    model,
    segments: list[str],
    lo: int,
    hi: int,
    *,
    allow_retry_single: bool,
    image_provider: str,
) -> list[dict]:
    """One or more API calls covering [lo, hi). Splits recursively if output truncates."""
    is_grok = (image_provider or "midjourney").lower() == "grok"
    system_prompt = GROK_SYSTEM_COMBINED if is_grok else SYSTEM_PROMPT
    if lo >= hi:
        return []
    try:
        parsed = await _mj_fetch_parsed_batch(
            client, model, segments, lo, hi, system_prompt=system_prompt, is_grok=is_grok
        )
    except Exception:
        # If a batch call is slow/fails, split and continue so one request never stalls the whole job.
        if hi - lo > 1:
            mid = lo + (hi - lo) // 2
            left = await _mj_prompt_range(
                client, model, segments, lo, mid, allow_retry_single=True, image_provider=image_provider
            )
            right = await _mj_prompt_range(
                client, model, segments, mid, hi, allow_retry_single=True, image_provider=image_provider
            )
            return left + right
        raise
    rows = _rows_from_parsed_batch(segments, lo, hi, parsed, image_provider=image_provider)
    if all((r.get('prompt_text') or '').strip() for r in rows):
        return rows

    if hi - lo > 1:
        mid = lo + (hi - lo) // 2
        left = await _mj_prompt_range(
            client, model, segments, lo, mid, allow_retry_single=True, image_provider=image_provider
        )
        right = await _mj_prompt_range(
            client, model, segments, mid, hi, allow_retry_single=True, image_provider=image_provider
        )
        return left + right

    if allow_retry_single:
        parsed2 = await _mj_fetch_parsed_batch(
            client, model, segments, lo, hi, system_prompt=system_prompt, is_grok=is_grok
        )
        rows2 = _rows_from_parsed_batch(segments, lo, hi, parsed2, image_provider=image_provider)
        if any((r.get('prompt_text') or '').strip() for r in rows2):
            return rows2

    return rows


def _build_mj_line(prompt_text, character, segment_id):
    # All characters get sref only — no cref for any character type.
    # Character consistency is maintained through precise text descriptions
    # and the shared --sref style reference, not --cref which locked poses.
    clean = _append_scene_marker(_strip_mj_params(prompt_text), segment_id)
    parts = [p for p in [clean, SREF, STYLE_TAIL] if p]
    return re.sub(r'  +', ' ', ' '.join(parts))


def _build_grok_line(prompt_text, _segment_id):
    """Single text block for Grok — prompt only (no scene id suffix; users paste into Grok as-is)."""
    t = (prompt_text or "").strip()
    if not t:
        return t
    t = re.sub(r"\s+", " ", t)
    # Strip legacy suffix if the model echoed it
    t = re.sub(r"\s*\[scene\s*\d+\]\s*$", "", t, flags=re.IGNORECASE).strip()
    return t


async def generate_mj_prompts(project_id, script_text, job_status, image_provider: str = "midjourney", job_id: str | None = None):
    from .job_store import job_put as _job_put

    def _persist():
        """Write the current in-memory status dict to SQLite so the frontend sees it immediately."""
        if job_id:
            _job_put(job_id, dict(job_status))

    job_status['status'] = 'splitting'
    job_status['percent'] = 0
    _persist()

    segments = await split_script_to_segments(script_text)
    n = len(segments)

    job_status['status'] = 'generating'
    job_status['percent'] = 10
    _persist()

    if n == 0:
        job_status['status'] = 'done'
        job_status['percent'] = 100
        _persist()
        return []

    # Long scripts: pack requests by character budget, then recursively split any batch
    # that still truncates. Same SYSTEM_PROMPT and per-segment task as before.
    max_user_chars = max(2500, int(os.getenv('MJ_PROMPT_USER_MAX_CHARS', '4500')))
    client = AsyncOpenAI(api_key=os.getenv('DEEPSEEK_API_KEY'), base_url='https://api.deepseek.com')
    model = os.getenv('DEEPSEEK_REASONER_MODEL', 'deepseek-reasoner')
    results: list[dict] = []
    batch_sz_env = os.getenv('MJ_PROMPT_BATCH_SIZE')
    if batch_sz_env:
        bs = max(1, int(batch_sz_env))
        packed = [(lo, min(lo + bs, n)) for lo in range(0, n, bs)]
    else:
        packed = _pack_segment_batches(segments, max_user_chars)
    done = 0
    for lo, hi in packed:
        part = await _mj_prompt_range(
            client, model, segments, lo, hi, allow_retry_single=True, image_provider=image_provider
        )
        results.extend(part)
        done += hi - lo
        job_status['percent'] = 10 + int(85 * done / max(n, 1))
        _persist()

    job_status['status'] = 'done'
    job_status['percent'] = 100
    _persist()
    return results


async def regenerate_single_prompt(segment_text, segment_id, image_provider: str = "midjourney"):
    is_grok = (image_provider or "midjourney").lower() == "grok"
    system_prompt = GROK_SYSTEM_COMBINED if is_grok else SYSTEM_PROMPT
    client = AsyncOpenAI(api_key=os.getenv('DEEPSEEK_API_KEY'), base_url='https://api.deepseek.com')
    if is_grok:
        user_message = (
            "VIDEO CONTEXT: Single-segment regeneration. Illustrate THIS segment’s words with full congruency; "
            "pick shot scale (wide/medium/close/abstract) appropriate to the beat and STYLE SPEC ACT.\n\n"
            f"--- SEGMENT {segment_id} ---\n\n{segment_text}"
        )
    else:
        user_message = f'--- SEGMENT {segment_id} ---\n\n{segment_text}'
    response = await client.chat.completions.create(
        model=os.getenv('DEEPSEEK_REASONER_MODEL', 'deepseek-reasoner'),
        temperature=1.0,
        max_tokens=4000,
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_message},
        ],
    )
    raw = response.choices[0].message.content or ''
    scene_type, character, act, target_image, prompt_text = _extract_fields_from_block(raw)
    if not prompt_text:
        character_re = re.compile(r'CHARACTER:\s*(.+)', re.IGNORECASE)
        target_re = re.compile(r'TARGET_IMAGE:\s*(.+)', re.IGNORECASE)
        mj_re = re.compile(r'MIDJOURNEY_PROMPT:\s*(.*)', re.IGNORECASE | re.DOTALL)
        grok_re = re.compile(r'GROK_IMAGE_PROMPT:\s*(.*)', re.IGNORECASE | re.DOTALL)
        character = ''
        for line in raw.split('\n'):
            m = character_re.match(line.strip())
            if m:
                character = m.group(1).strip()
            m = target_re.match(line.strip())
            if m:
                target_image = m.group(1).strip()
            m = mj_re.match(line.strip())
            if m:
                prompt_text = m.group(1).strip()
            m = grok_re.match(line.strip())
            if m:
                prompt_text = m.group(1).strip()
        if not prompt_text and 'MIDJOURNEY_PROMPT:' in raw:
            prompt_text = raw.split('MIDJOURNEY_PROMPT:', 1)[-1].strip()
        if not prompt_text and 'GROK_IMAGE_PROMPT:' in raw:
            prompt_text = raw.split('GROK_IMAGE_PROMPT:', 1)[-1].strip()
    mj_line = (
        _build_grok_line(prompt_text, segment_id)
        if is_grok
        else _build_mj_line(prompt_text, character, segment_id)
    )
    return {
        'segment_id': segment_id,
        'segment_text': segment_text,
        'character': character,
        'act': act,
        'target_image': target_image or segment_text,
        'prompt_text': prompt_text,
        'mj_line': mj_line,
        'image_provider': 'grok' if is_grok else 'midjourney',
    }


def get_grok_image_system_prompt_combined() -> str:
    """Full DeepSeek system text for whole-script Grok image prompts (scene + style spec)."""
    return _load_grok_system_prompt()
