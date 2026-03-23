# MindShift Lab

**Automated video production pipeline** for a self-help / educational YouTube-style workflow: channel strategy → script → MidJourney image prompts → manual images → thumbnail/SEO prompts → voice recording → Whisper transcript + script alignment → timeline editor → FFmpeg export.

This README is the **full system specification**. Give it to an AI before asking for code changes so it does not break contracts between frontend, backend, files on disk, and external APIs.

---

## Quick start (human)

| Requirement | Notes |
|-------------|------|
| **FFmpeg** | Must be on `PATH` (export uses `ffmpeg` subprocess). |
| **Python** | 3.11+ recommended. |
| **Node** | 18+. |

```bash
cp backend/.env.example backend/.env   # fill OPENAI_API_KEY, DEEPSEEK_API_KEY
cd backend && pip install -r requirements.txt
cd frontend && npm install
```

**Terminal 1:** `cd backend && uvicorn main:app --reload --port 8000`  
**Terminal 2:** `cd frontend && npm run dev` → http://localhost:3000  

**Frontend API base:** set `NEXT_PUBLIC_API_URL` in `frontend/.env.local` (e.g. `http://localhost:8000`). If unset, `lib/api.ts` defaults to `http://localhost:8000`. **Some editor code** uses `process.env.NEXT_PUBLIC_API_URL` **without** that fallback for image upload—set the env in production.

---

## Repository layout

| Path | Role |
|------|------|
| `backend/main.py` | FastAPI app, all HTTP routes, job registry `_job_status`, project paths. |
| `backend/services/*.py` | Domain logic (LLM, audio, assembly, export). |
| `backend/.env` | Secrets (not committed). See `.env.example`. |
| `frontend/app/` | Next.js App Router pages. |
| `frontend/lib/api.ts` | **Single source of truth** for backend URL and fetch wrappers. |
| `frontend/components/` | `SceneBatchPage.tsx` (MJ prompt UI), `Toast.tsx`. |
| `frontend/hooks/useToast.ts` | Toast state. |
| `videos/` | **All project data lives here** (at repo root: `MindShift-Lab/videos/`). Each subfolder = one project. |
| `prompts/` | Editable `.txt` templates served by API (`1_channel_strategy`, `2_script_generation`, `4_thumbnail`, `5_seo`). File `8_thumbnail_review_prompt.txt` exists but is **not** wired in code. |

**Important:** `main.py` sets `VIDEOS_DIR = Path(__file__).parent.parent / 'videos'` → repo root `videos/`, **not** `backend/videos/`. Sample data may exist under `backend/videos/` for local tests; production flow uses root `videos/`.

---

## Environment variables (`backend/.env`)

| Variable | Used by |
|----------|---------|
| `OPENAI_API_KEY` | Whisper transcription (`audio_service`), script cleaning (`clean_service` → `gpt-4o`). |
| `DEEPSEEK_API_KEY` | Segment split, MJ prompt generation, transcript word correction, regenerate prompt. |
| `WHISPER_MODEL` | Default `whisper-1`. |
| `DEEPSEEK_MODEL` | Default `deepseek-chat` (split + correction batches). |
| `DEEPSEEK_REASONER_MODEL` | Default `deepseek-reasoner` (full MJ prompt generation). |

---

## Backend dependencies (`backend/requirements.txt`)

`fastapi`, `uvicorn[standard]`, `python-multipart`, `openai`, `python-dotenv`, `rapidfuzz`, `aiofiles`, `pydub`

- **pydub** needed when `audio.mp3` exceeds **24 MB** (chunking for Whisper).

---

## Frontend stack

- **Next.js** 16.1.6, **React** 19, **TypeScript**, **Tailwind** 4.
- Client-heavy UI; all data via REST to FastAPI.

---

## Project identity & folder naming

- **Project ID** = folder name under `videos/`, e.g. `1 - My Title`.
- **Create project:** `POST /api/projects` counts **directories** in `videos/`, then creates `{count+1} - {title}` with `meta.json`, `images/`.
- **URLs:** project id must be URL-encoded in paths (spaces → `%20`).

---

## Per-project files (contract — do not rename casually)

| File | Written by | Purpose |
|------|------------|---------|
| `meta.json` | API | `id`, `title`, `current_step` (number; can be **float** e.g. `4.5`), `audio_job_id` optional. |
| `script_raw.txt` | API | Raw script if saved uncleaned. |
| `script.txt` | API | Clean narration; required for MJ prompts + DeepSeek correction. |
| `mj_prompts.json` | API / prompt pipeline | Array of segment records (see schema below). |
| `images/{segment_id}.png` | User (or `replace-image`) | One image per segment; names must match `segment_id`. |
| `audio.mp3` | User upload | Full narration. |
| `_chunk_*.mp3` | Temp | Removed after transcribe. |
| `audio_transcript.json` | Whisper | Words + segments + duration. |
| `audio_subtitles.srt` | Whisper | Per-word SRT. |
| `audio_corrected.json` | DeepSeek | Word-level correction vs script. |
| `audio_corrected.srt` | DeepSeek | SRT from corrected words. |
| `segments.json` | Assemble | Timeline segments with `start`/`end`/`image`/`matched`. |
| `editor_state.json` | Editor PUT | Full editor snapshot (segments, subtitles, transitions, settings). |
| `subtitles.ass` | Export | Generated during export if subtitles on. |
| `output.mp4` | FFmpeg export | Final video. |

---

## `mj_prompts.json` record shape

Each element:

- `segment_id` (int, 1-based)
- `segment_text` (string)
- `scene_type`, `character`, `act` (strings from LLM)
- `prompt_text` (raw MJ description)
- `mj_line` — **full line for MidJourney**: `prompt_text` + fixed `--sref` URLs + `--ar 16:9 --stylize 100 --style raw --sw 250` (see `prompt_service.py`).

---

## Pipeline steps (user flow vs `current_step`)

UI steps in `frontend/app/project/[id]/page.tsx` (`STEPS` array):

| UI step | `current_step` when active | Notes |
|---------|---------------------------|--------|
| 1 Channel Strategy | `1` | Template from `prompts/1_channel_strategy.txt`; save title + `stepComplete(1)`. |
| 2 Script | `2` | Template + title; optional `clean-script` (OpenAI); `saveScript(..., true)` + `stepComplete(2)`. |
| 3 MidJourney | `3` | `generate-prompts` job → `mj_prompts.json`; backend sets step **≥4** when done. |
| 3.5 Image Check | `3.5` | **Problem:** After prompts, meta is already **4**, so 3 and 3.5 may show **Completed** without image check. User can still open 3.5 when advancing manually. |
| 4 Thumbnail | `4` | Template `4_thumbnail.txt`; `stepComplete(4)`. |
| 4.5 Recording script | `4.5` | Shows `script.txt`; user records → `audio.mp3`; `stepComplete(4.5)`. |
| 5 Audio | `5` | Upload MP3 → transcribe + correct → `stepComplete` not auto; user clicks **Proceed to Assembly** → `assemble` → navigates to editor. |
| 6 Assemble & Edit | `6` | Route `/project/[id]/editor`; export sets `output.mp4`; **Next Step** calls `stepComplete(6)` → step 7. |
| 7 SEO | `7` | Template `5_seo.txt` + script; save uses `saveScript` + `stepComplete(7)`. |

**Completion banner** when `current_step > 7` (download `output.mp4`).

**`step_complete` API:** `POST .../step-complete` body `{ "step": N }` sets `current_step = max(current, step + 1)`. So fractional steps work (e.g. `3.5` → `4.5`).

**`reset_step` API:** `POST .../reset-step` body `{ "step": N }` deletes downstream artifacts and sets `current_step = N`. Thresholds: ≤2 wipes script files; ≤3 wipes `mj_prompts.json` + `images/*`; ≤5 wipes audio artifacts; ≤6 wipes `segments.json`, `editor_state.json`, `output.mp4`, `subtitles.ass`.

---

## Backend API reference (all routes in `main.py`)

| Method | Path | Body / notes |
|--------|------|----------------|
| GET | `/ping-file`, `/test-file` | Debug / dev only. |
| GET | `/api/projects` | List projects (folders with `meta.json`). |
| POST | `/api/projects` | `{ "title" }` → create folder. |
| GET | `/api/projects/{id}` | `meta.json`. |
| DELETE | `/api/projects/{id}` | `{ "confirm_title" }` must match title. |
| PATCH | `/api/projects/{id}/title` | `{ "title" }`. |
| POST | `/api/projects/{id}/reset-step` | `{ "step": number }`. |
| POST | `/api/projects/{id}/step-complete` | `{ "step": number }`. |
| GET | `/api/prompts/{name}` | `1_channel_strategy`, `2_script_generation`, `4_thumbnail`, `5_seo`. |
| GET | `/api/projects/{id}/files/{filepath}` | Safe path under project dir only. |
| POST | `/api/projects/{id}/script` | `{ "script", "is_cleaned": bool }`. |
| POST | `/api/projects/{id}/clean-script` | `{ "script" }` → OpenAI cleaner. |
| POST | `/api/projects/{id}/generate-prompts` | Background job → returns `{ job_id }`. |
| GET | `/api/projects/{id}/prompts` | `mj_prompts.json` or `[]`. |
| POST | `/api/projects/{id}/regenerate-prompt/{segment_id}` | `{ "segment_text" }` — **path must include integer segment_id**. |
| GET | `/api/projects/{id}/image-check` | `{ present, missing, total_expected }`. |
| POST | `/api/projects/{id}/replace-image/{image_name}` | multipart `file`. |
| POST | `/api/projects/{id}/audio` | multipart `file` → background transcribe/correct. |
| GET | `/api/projects/{id}/audio-status` | Job status from `meta.audio_job_id` + `_job_status`. |
| POST | `/api/projects/{id}/assemble` | Builds `segments.json` via fuzzy match. |
| GET | `/api/projects/{id}/segments` | Segments array. |
| POST | `/api/projects/{id}/generate-subtitles` | Returns subtitle **lines** (not persisted server-side). |
| PUT | `/api/projects/{id}/editor-state` | Raw JSON body → `editor_state.json`. |
| GET | `/api/projects/{id}/editor-state` | Object or `{}`. |
| POST | `/api/projects/{id}/export` | `{ "editor_state": { ... } }` → background FFmpeg. |
| GET | `/api/jobs/{job_id}` | `{ status, percent?, error?, ... }`. |

**Job storage:** In-memory `_job_status` dict — **lost on server restart**. Prompt/audio/export jobs are not durable.

**CORS:** `allow_origins=["*"]`.

---

## Service modules (backend)

### `segment_service.py`

- **`split_script_to_segments(script_text)`** — DeepSeek chat returns **JSON array of strings** (markdown fences stripped). Rules in `SPLIT_PROMPT`: 6–14 words per segment, frequent splits.

### `prompt_service.py`

- **`generate_mj_prompts(project_id, script_text, job_status)`** — Splits script → one DeepSeek **reasoner** call with huge `SYSTEM_PROMPT` → parses blocks for `SCENE TYPE`, `CHARACTER`, `ACT`, `MIDJOURNEY_PROMPT` → builds `mj_line` with `SREF` + `STYLE_TAIL`.
- **`regenerate_single_prompt(segment_text, segment_id)`** — Same parser for one segment.
- Parsing is **fragile**: relies on double-newline-separated blocks aligned with segment index order.

### `clean_service.py`

- **`clean_script(script)`** — OpenAI `gpt-4o`, strips stage directions / markers from narration.

### `audio_service.py`

- Chunk if > 24 MB → Whisper **verbose_json** with word timestamps → merge → `audio_transcript.json` + SRT → optional DeepSeek batch correction (300 words/batch) vs `script.txt` → `audio_corrected.json`.

### `assembly_service.py`

- **`map_segments_to_timestamps`** — For each `mj_prompts` segment, first 6 words fuzzy-matched (`rapidfuzz.fuzz.ratio`) against transcript words (ratio ≥75 → start time; else chain from previous end). Writes `segments.json`. Last segment `end` = audio duration.
- **`build_subtitle_lines`** — Groups words into lines (max 7 words or sentence end).

### `export_service.py`

- **`build_and_export(project_id, project_dir, editor_state, job_status)`** — Runs in thread from async route.
- Reads `editor_state.segments` or falls back to `segments.json`.
- Builds FFmpeg: one input per segment image looped for duration, concat/xfade chain, map `audio.mp3`, optional ASS burn-in from `editor_state.subtitles` (karaoke `\k` timing).
- **Working directory** for FFmpeg = `project_dir` (so `subtitles.ass` relative path works).
- Output: `output.mp4`.

---

## Frontend ↔ backend wiring (`lib/api.ts`)

Every feature should call these helpers so the base URL stays consistent.

**Known mismatch (do not ignore when fixing):**

- **Regenerate prompt:** Backend expects  
  `POST /api/projects/{id}/regenerate-prompt/{segment_id}`  
  Frontend `regeneratePrompt()` currently posts to  
  `/api/projects/{id}/regenerate-prompt` **without** `segment_id` → will **404** unless fixed. Correct client URL: append `/${encodeURIComponent(segId)}`.

---

## Editor page (`app/project/[id]/editor/page.tsx`)

- Loads: `getProject`, `getSegments`, `getEditorState`. Prefers **saved** `editor_state.segments` over fresh `segments.json` if non-empty.
- **Canvas preview:** 16:9, images from `fileUrl(id, images/...)`, optional fade between segments (must match export transition keys `"0-1"`, `"1-2"`, …).
- **Audio:** `<audio src={fileUrl(id, 'audio.mp3')}>` — timeline sync via `requestAnimationFrame` + `playbackSpeed`.
- **Subtitles:** `generateSubtitles(id)` loads lines; word-level highlight uses per-word timestamps.
- **Export:** `buildEditorState()` sends `segments`, `subtitles`, `has_subtitles`, `font_size`, `alignment` (from `subtitleSettings.position`), `transitions`, `subtitleSettings`, `canvas_display_width`.
- **Replace / add image:** direct `fetch` to `replace-image/{image_name}` — uses `NEXT_PUBLIC_API_URL` without fallback.
- **Timeline:** drag segment edges; snap to playhead; select segment → replace image, delete scene, add scene after (new numeric `segment_id` = `Date.now()`).

---

## Scene batch UI (`SceneBatchPage.tsx`)

- Pagination 13 per page; **Copy Scene N** sequence stored in `localStorage` (`mindshift_seq_{projectId}`, `mindshift_copied_{projectId}`).
- Regen calls parent `onRegenerate` → should hit regenerate API correctly once URL is fixed.

---

## Home page (`app/page.tsx`)

- Lists projects when drawer opens; **list API does not return `current_step`** — displayed “Step X/7” in drawer may be wrong unless API is extended.

---

## Safe modification guidelines (for AI)

1. **Preserve JSON shapes** consumed by editor and export (`segments`, `subtitle` lines with `words[]`, `transitions` keys `"i-(i+1)"`).
2. **Segment images** must remain `{segment_id}.png` (or export falls back to jpg/PNG variants).
3. **FFmpeg filter graph** in `export_service.py` is order-sensitive; changing transition logic requires matching **editor preview** math (fade window at end of segment).
4. **Do not** store secrets in frontend; **do not** commit `.env`.
5. **New API routes:** add mirror function in `lib/api.ts` and use from pages.
6. **Changing step numbers:** update `STEPS`, all `stepComplete` / `resetStep` call sites, and backend `reset_step` file deletion thresholds together.
7. **In-memory jobs:** if adding long-running tasks, document that restart loses status unless persisted.

---

## Absolute paths in code (portability)

- `main.py` contains a debug route with hardcoded Windows path `D:\Goal\MindShift-Lab\...` — remove or gate for non-Windows devs.

---

## Summary diagram (data flow)

```
script.txt → [DeepSeek split] → segments text
          → [DeepSeek reasoner] → mj_prompts.json → user → images/{id}.png

audio.mp3 → [Whisper] → words + times → [DeepSeek correct vs script] → audio_corrected.json

mj_prompts.json + audio_corrected.json → [rapidfuzz assemble] → segments.json

segments.json + editor tweaks + subtitles + transitions → [FFmpeg] → output.mp4
```

---

*End of specification. When changing this system, cross-check this document against actual `main.py`, `lib/api.ts`, and `editor/page.tsx` behavior.*
