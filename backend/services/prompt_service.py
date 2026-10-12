from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

import json
import re
import os
from openai import AsyncOpenAI
from .segment_service import split_script_to_segments


# ──────────────────────────────────────────────────────────────────────────────
# Visual-cue helpers  (used when script_master.txt is saved alongside script.txt)
# ──────────────────────────────────────────────────────────────────────────────

def parse_visual_cues_from_master(master_text: str) -> list[dict]:
    """Parse (visual_cue, narration) pairs from a Claude-formatted master script.

    Each [VISUAL CUE: ...] block is paired with the narration text that follows
    it (up to the next [VISUAL CUE: ...] or end of file).

    Safety guarantee: this function only READS the master script.
    It never touches script.txt — the clean narration used for audio alignment.

    Returns list of dicts: {'visual_cue': str, 'narration': str}.
    """
    # Split on every [VISUAL CUE: occurrence (case-insensitive)
    parts = re.split(r'\[VISUAL CUE:', master_text, flags=re.IGNORECASE)
    if len(parts) <= 1:
        return []

    pairs: list[dict] = []
    # parts[0] is preamble/metadata before the first cue — skip it
    for part in parts[1:]:
        lines = part.split('\n')
        cue_lines: list[str] = []
        narration_lines: list[str] = []
        in_cue = True

        for line in lines:
            stripped = line.strip()
            if in_cue:
                if not stripped:
                    in_cue = False  # blank line ends the cue block
                else:
                    cue_lines.append(stripped.rstrip(']').strip())
            else:
                # Skip structural markers — they are not spoken narration
                if (stripped.startswith('[') or stripped.startswith('★')
                        or re.match(r'^(Phase|Front|Fix|★|\d+:\d+)', stripped)):
                    continue
                narration_lines.append(stripped)

        cue_text = ' '.join(cue_lines).strip().rstrip(']').strip()
        narration_text = ' '.join(l for l in narration_lines if l).strip()

        # Skip false matches from the script's own legend/key section
        # e.g. "[VISUAL CUE: …] = instruction to animator"
        if not cue_text or len(cue_text) < 15:
            continue
        if cue_text.startswith(']') or '…' in cue_text[:5] or cue_text.startswith('='):
            continue
        # Skip if narration looks like a structural label rather than spoken text
        if re.match(r'^(Target emotion|Phase|Fix \d|Front \d|Open|PATTERN|MID-VIDEO)', narration_text):
            continue

        pairs.append({'visual_cue': cue_text, 'narration': narration_text})

    return pairs


def match_visual_cues_to_segments(
    segments: list[str],
    cue_pairs: list[dict],
) -> list[str | None]:
    """For each clean-narration segment, find the best-matching visual cue.

    Matching uses word-overlap between the segment text and the narration text
    that followed each [VISUAL CUE] block in the master script.

    Returns a list of visual_cue strings (or None when confidence is too low).

    Safety: visual cues are returned as separate strings — they are NEVER
    merged into segment_text, so the audio-alignment pipeline is untouched.
    """
    if not cue_pairs:
        return [None] * len(segments)

    _stop = {'the', 'a', 'an', 'and', 'or', 'in', 'on', 'at', 'to', 'of',
             'is', 'it', 'its', 'that', 'this', 'was', 'are', 'for', 'with'}

    def _words(text: str) -> set[str]:
        tokens = re.sub(r'[^\w\s]', '', text.lower()).split()
        return {t for t in tokens if t not in _stop and len(t) > 2}

    cue_word_sets = [_words(p['narration']) for p in cue_pairs]

    results: list[str | None] = []
    for seg in segments:
        seg_words = _words(seg)
        if not seg_words:
            results.append(None)
            continue

        best_score = 0.0
        best_cue: str | None = None
        for idx, cue_ws in enumerate(cue_word_sets):
            if not cue_ws:
                continue
            overlap = len(seg_words & cue_ws)
            # Normalize by segment word count — shorter segments still match
            score = overlap / len(seg_words)
            if score > best_score:
                best_score = score
                best_cue = cue_pairs[idx]['visual_cue']

        # Require at least 20 % word overlap to avoid false matches
        results.append(best_cue if best_score >= 0.20 else None)

    return results


def _repo_prompts_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "prompts"


def _repo_prompts_example_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "prompts.example"


def _load_text(filename: str) -> str:
    """Private prompt file if present (gitignored), else its prompts.example/
    fallback, else empty string."""
    private_path = _repo_prompts_dir() / filename
    if private_path.is_file():
        return private_path.read_text(encoding="utf-8")
    example_path = _repo_prompts_example_dir() / filename
    if example_path.is_file():
        return example_path.read_text(encoding="utf-8")
    return ""


def _load_grok_system_prompt() -> str:
    scene = _load_text("grok_scene_system.txt")
    style = _load_text("grok_style_spec_v1.txt")
    if not scene and not style:
        return ""
    if not scene:
        return style.strip()
    return f"{scene}\n\n---\n{style}".strip()


SREF = _load_text("midjourney_sref.txt").strip()
# --cref removed: MJ7 follows text descriptions precisely enough without it,
# and it was locking all characters into the same static reference pose.
STYLE_TAIL = '--ar 16:9 --stylize 100 --style raw --sw 50'

# The real style bible + character anchors are private (see prompts/ vs
# prompts.example/). SYSTEM_PROMPT falls back to the generic placeholder
# when no private file is present.
SYSTEM_PROMPT = "\n" + _load_text("midjourney_system_prompt.txt")

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
    visual_cues: list[str | None] | None = None,
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
            _load_text('grok_batch_instructions.txt'),
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
        # Append visual cue hint when available — clearly labeled so DeepSeek
        # treats it as image-composition guidance only, never as spoken narration.
        if visual_cues is not None:
            cue_idx = lo + i
            if cue_idx < len(visual_cues) and visual_cues[cue_idx]:
                lines.append(
                    f'\n[DIRECTOR\'S NOTE — image composition hint, NOT part of the narration; '
                    f'do NOT treat this as spoken text or include it in the prompt as dialogue]: '
                    f'{visual_cues[cue_idx]}'
                )
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
    visual_cues: list[str | None] | None = None,
) -> list[dict]:
    """One or more API calls covering [lo, hi). Splits recursively if output truncates."""
    is_grok = (image_provider or "midjourney").lower() == "grok"
    system_prompt = GROK_SYSTEM_COMBINED if is_grok else SYSTEM_PROMPT
    if lo >= hi:
        return []
    try:
        parsed = await _mj_fetch_parsed_batch(
            client, model, segments, lo, hi, system_prompt=system_prompt, is_grok=is_grok, visual_cues=visual_cues
        )
    except Exception:
        # If a batch call is slow/fails, split and continue so one request never stalls the whole job.
        if hi - lo > 1:
            mid = lo + (hi - lo) // 2
            left = await _mj_prompt_range(
                client, model, segments, lo, mid, allow_retry_single=True, image_provider=image_provider, visual_cues=visual_cues
            )
            right = await _mj_prompt_range(
                client, model, segments, mid, hi, allow_retry_single=True, image_provider=image_provider, visual_cues=visual_cues
            )
            return left + right
        raise
    rows = _rows_from_parsed_batch(segments, lo, hi, parsed, image_provider=image_provider)
    if all((r.get('prompt_text') or '').strip() for r in rows):
        return rows

    if hi - lo > 1:
        mid = lo + (hi - lo) // 2
        left = await _mj_prompt_range(
            client, model, segments, lo, mid, allow_retry_single=True, image_provider=image_provider, visual_cues=visual_cues
        )
        right = await _mj_prompt_range(
            client, model, segments, mid, hi, allow_retry_single=True, image_provider=image_provider, visual_cues=visual_cues
        )
        return left + right

    if allow_retry_single:
        parsed2 = await _mj_fetch_parsed_batch(
            client, model, segments, lo, hi, system_prompt=system_prompt, is_grok=is_grok, visual_cues=visual_cues
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


async def generate_mj_prompts(
    project_id,
    script_text,
    job_status,
    image_provider: str = "midjourney",
    job_id: str | None = None,
    master_script_path: Path | None = None,
):
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

    # ── Visual cue context (optional, toggle-controlled) ────────────────────
    # Segments are ALWAYS split from the clean script.txt — visual cues are
    # only passed to DeepSeek as a separate "director's note" hint.
    # Audio alignment is 100% unaffected: it never sees this data.
    visual_cues: list[str | None] | None = None
    if master_script_path is not None and master_script_path.is_file():
        try:
            master_text = master_script_path.read_text(encoding='utf-8')
            cue_pairs = parse_visual_cues_from_master(master_text)
            if cue_pairs:
                visual_cues = match_visual_cues_to_segments(segments, cue_pairs)
                matched = sum(1 for c in visual_cues if c)
                print(f"[prompt_service] Visual cues: {matched}/{n} segments matched from master script.")
        except Exception as exc:
            print(f"[prompt_service] Visual cue loading failed (non-fatal): {exc}")
            visual_cues = None

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
            client, model, segments, lo, hi, allow_retry_single=True, image_provider=image_provider, visual_cues=visual_cues
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
            _load_text('grok_regenerate_instructions.txt')
            + f"--- SEGMENT {segment_id} ---\n\n{segment_text}"
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
