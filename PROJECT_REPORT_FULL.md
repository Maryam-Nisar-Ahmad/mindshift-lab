# MindShift-Lab Full Technical Reconstruction Report

This report is designed so another AI can recreate this project rapidly with high fidelity.
It is based on direct inspection of the current codebase and data artifacts.

## 1) Project Intent and Product Scope

`MindShift-Lab` is an end-to-end video production workflow for YouTube-style self-help content:

1. Channel strategy prompt
2. Script generation + cleaning
3. MidJourney scene prompt generation
4. Manual image placement/check
5. Thumbnail and thumbnail-review prompt workflows
6. Script-for-recording handoff
7. Audio upload + Whisper transcription + optional DeepSeek correction
8. Segment-to-timestamp assembly
9. Browser editor (timeline, subtitles, transitions, image ops)
10. FFmpeg export to `output.mp4`
11. SEO prompt + final review/save

Architecture:
- Frontend: Next.js App Router, React + TS
- Backend: FastAPI + service modules
- Data persistence: filesystem (per-project folders)
- Jobs: in-memory dict in backend process

---

## 2) Exact Repository Inventory (code + text artifacts)

Top-level:
- `README.md` (system specification, mostly aligned to code)
- `PROJECT_REPORT_FULL.md` (this file)
- `backend/`
- `frontend/`
- `prompts/`
- `videos/`

### Backend
- `backend/main.py`
- `backend/requirements.txt`
- `backend/services/segment_service.py`
- `backend/services/prompt_service.py`
- `backend/services/clean_service.py`
- `backend/services/audio_service.py`
- `backend/services/assembly_service.py`
- `backend/services/export_service.py`
- `backend/videos/sleep/` sample dataset:
  - `meta.json`
  - `script.txt`
  - `mj_prompts.json`
  - `audio_transcript.json`
  - `audio_subtitles.srt`
  - `audio_corrected.json`
  - `audio_corrected.srt`
  - `segments.json`
  - `editor_state.json`
- `backend/test_output.png` exists

### Frontend
- `frontend/package.json`
- `frontend/package-lock.json`
- `frontend/tsconfig.json`
- `frontend/next.config.ts`
- `frontend/next-env.d.ts`
- `frontend/README.md`
- `frontend/app/layout.tsx`
- `frontend/app/globals.css`
- `frontend/app/page.tsx`
- `frontend/app/project/[id]/page.tsx`
- `frontend/app/project/[id]/editor/page.tsx`
- `frontend/lib/api.ts`
- `frontend/components/SceneBatchPage.tsx`
- `frontend/components/Toast.tsx`
- `frontend/hooks/useToast.ts`

### Prompt Templates
- `prompts/1_channel_strategy.txt`
- `prompts/2_script_generation.txt`
- `prompts/4_thumbnail.txt`
- `prompts/5_seo.txt`
- `prompts/8_thumbnail_review_prompt.txt`

### Runtime Project Data
- `videos/2 - Video/meta.json`
- `videos/3 - Video/meta.json`
- `videos/3 - Video/script.txt`
- `videos/3 - Video/mj_prompts.json`
- `videos/3 - Video/audio_transcript.json`
- `videos/3 - Video/audio_subtitles.srt`
- `videos/3 - Video/audio_corrected.json`
- `videos/3 - Video/audio_corrected.srt`
- `videos/3 - Video/segments.json`
- `videos/3 - Video/editor_state.json`

---

## 3) Environment and Runtime Contracts

### Runtime requirements
- Python 3.11+
- Node 18+
- `ffmpeg` on system PATH

### Backend env (`backend/.env`, loaded by `dotenv`)
- `OPENAI_API_KEY` (Whisper + GPT-4o script cleaner)
- `DEEPSEEK_API_KEY` (split/correct/MJ generation)
- Optional model selectors:
  - `DEEPSEEK_MODEL` default `deepseek-chat`
  - `DEEPSEEK_REASONER_MODEL` default `deepseek-reasoner`

### Frontend env
- `NEXT_PUBLIC_API_URL`
  - API helpers fallback to `http://localhost:8000`
  - Editor image upload code directly uses env var and has no fallback

---

## 4) Backend: FastAPI App Behavior (`backend/main.py`)

## 4.1 Global app state
- `VIDEOS_DIR = Path(__file__).parent.parent / 'videos'`
- `PROMPTS_DIR = Path(__file__).parent.parent / 'prompts'`
- `_job_status = {}` in-memory dict for async job progress
- CORS = allow all origins/methods/headers
- startup warning if ffmpeg missing

## 4.2 Pydantic request bodies
- `CreateProjectBody { title: str }`
- `DeleteProjectBody { confirm_title: str }`
- `UpdateTitleBody { title: str }`
- `StepBody { step: float }`
- `StepCompleteBody { step: float; next_step?: float }`
- `ScriptBody { script: str; is_cleaned: bool }`
- `CleanScriptBody { script: str }`
- `SeoDataBody { description: str; tags: str }`
- `RegeneratePromptBody { segment_text: str }`
- `ExportBody { editor_state: dict }`

## 4.3 Project lifecycle routes
- `GET /api/projects`
  - scans project directories with `meta.json`
  - sorts by folder mtime desc
  - returns only `[ {id, title} ]` (no `current_step`)
- `POST /api/projects`
  - counts directories in `videos/`
  - creates folder `"N - {title}"`, `images/`, `meta.json`
  - initial `current_step = 1`
- `GET /api/projects/{id}` returns `meta.json`, forces float cast for `current_step`
- `PATCH /api/projects/{id}/title`
- `DELETE /api/projects/{id}` requires exact `confirm_title`

## 4.4 Step machine routes
- `POST /api/projects/{id}/step-complete`
  - sets `current_step = max(current, next_step || step+1)`
- `POST /api/projects/{id}/reset-step`
  - destructive cleanup by thresholds:
    - `<=2`: delete `script.txt`, `script_raw.txt`
    - `<=3`: delete `mj_prompts.json`, clear all `images/*`
    - `<=5`: delete audio and transcript artifacts + `_chunk_*.mp3`
    - `<=6`: delete `segments.json`, `editor_state.json`, `output.mp4`, `subtitles.ass`
  - sets `current_step = requested step`

## 4.5 Prompt template route
- `GET /api/prompts/{name}`
  - names include: `1_channel_strategy`, `2_script_generation`, `4_thumbnail`, `5_seo`, `8_thumbnail_review_prompt`

## 4.6 Script routes
- `POST /api/projects/{id}/script`
  - if `is_cleaned=true` -> writes `script.txt` and pushes `current_step>=3`
  - else -> writes `script_raw.txt`
- `POST /api/projects/{id}/clean-script`
  - delegates to GPT-4o cleaner service

## 4.7 Prompt generation routes
- `POST /api/projects/{id}/generate-prompts`
  - needs `script.txt`
  - creates job id in `_job_status`
  - background task runs DeepSeek generation
  - writes `mj_prompts.json`, pushes step to `>=4`
- `GET /api/projects/{id}/prompts` -> `mj_prompts.json` or `[]`
- `POST /api/projects/{id}/regenerate-prompt/{segment_id}`
  - regenerates one prompt from `segment_text`, updates matching record by numeric `segment_id`

## 4.8 Image routes
- `GET /api/projects/{id}/image-check`
  - expected images from `mj_prompts.segment_id` as `{id}.png`
  - returns `present[]`, `missing[]`, `total_expected`
- `POST /api/projects/{id}/replace-image/{image_name}`
  - multipart upload writes `images/{image_name}`

## 4.9 Audio + assembly routes
- `POST /api/projects/{id}/audio`
  - writes `audio.mp3`
  - starts background job `process_audio(...)`
- `GET /api/projects/{id}/audio-status`
  - reads `meta.audio_job_id` and `_job_status`
- `POST /api/projects/{id}/assemble`
  - calls `map_segments_to_timestamps`, returns array
- `GET /api/projects/{id}/segments`

## 4.10 Editor/export routes
- `POST /api/projects/{id}/generate-subtitles`
  - reads `audio_corrected.json` fallback `audio_transcript.json`
  - returns subtitle line array (not persisted by backend)
- `PUT /api/projects/{id}/editor-state` writes JSON
- `GET /api/projects/{id}/editor-state` returns JSON or `{}`
- `POST /api/projects/{id}/export`
  - starts background ffmpeg job from provided `editor_state`
- `GET /api/jobs/{job_id}` returns in-memory status

## 4.11 SEO routes
- `GET /api/projects/{project_id}/seo-data` -> defaults empty fields
- `POST /api/projects/{project_id}/seo-data` writes `seo_data.json`

## 4.12 Utility/debug routes
- `GET /ping-file` hardcoded local path response
- `GET /test-file` tests one image path under `VIDEOS_DIR`
- `GET /api/projects/{id}/files/{filepath:path}` secure file-serving with normalized-path containment check

---

## 5) Backend Service Logic

## 5.1 `segment_service.py`
- `split_script_to_segments(script_text)`
  - DeepSeek chat completion with strict splitting prompt
  - expects JSON array of strings
  - strips markdown fence if present
  - output: list of trimmed segment strings

## 5.2 `prompt_service.py`
- constants:
  - `SREF` has 4 style refs
  - `STYLE_TAIL = --ar 16:9 --stylize 100 --style raw --sw 50`
- `_strip_mj_params()` removes `--sref` URLs and known MJ params
- `_build_mj_line()` appends `SREF + STYLE_TAIL` to cleaned prompt text
- `generate_mj_prompts(project_id, script_text, job_status)`:
  1. sets job status splitting
  2. gets split segments
  3. sends one large DeepSeek reasoner request with long visual-system prompt
  4. parses response blocks with regex (`SCENE TYPE`, `CHARACTER`, `ACT`, `MIDJOURNEY_PROMPT`)
  5. constructs records:
     - `segment_id`, `segment_text`, `scene_type`, `character`, `act`, `prompt_text`, `mj_line`
  6. marks job done
- `regenerate_single_prompt(segment_text, segment_id)`
  - one-segment variant, similar parser

## 5.3 `clean_service.py`
- `clean_script(script)`
  - OpenAI `gpt-4o`, temp 0
  - strips formatting tags/markers/stage directions, preserving wording and paragraphs

## 5.4 `audio_service.py`
- chunking:
  - if audio >24MB, split with `pydub` into `_chunk_###.mp3`
- transcription:
  - OpenAI Whisper `verbose_json` with word timestamps
- postprocess:
  - merge chunks preserving offsets
  - save `audio_transcript.json`
  - save word-level SRT `audio_subtitles.srt`
- correction:
  - if `script.txt` exists, use DeepSeek in 300-word batches
  - strict line-count-preserving correction strategy
  - fallback to original batch if output line count mismatch
  - save `audio_corrected.json` + `audio_corrected.srt`
- final job fields:
  - `status`, `percent`, `duration`, `word_count`, `words_corrected`

## 5.5 `assembly_service.py`
- `map_segments_to_timestamps(project_id, project_dir)`
  - loads `mj_prompts.json` and corrected transcript fallback raw transcript
  - uses first 6 words of each segment as probe
  - scans transcript windows with `rapidfuzz.fuzz.ratio`
  - matched if ratio >=75
  - unmatched starts at previous segment end
  - builds `segments.json` with:
    - `segment_id`, `image`, `start`, `end`, `text`, `matched`, `match_ratio`
  - ensures end > start by bumping min 4s
- `build_subtitle_lines(audio_corrected_json, max_words_per_line=7)`
  - groups by punctuation or max words
  - returns line objects with words arrays

## 5.6 `export_service.py`
- `build_and_export(project_id, project_dir, editor_state, job_status)`
  - source segments: editor state or fallback `segments.json`
  - optional ASS subtitle generation:
    - uses style/color/outline/highlight settings
    - karaoke timing with `\k{centiseconds}` per word
  - ffmpeg input:
    - one looped image input per segment duration
    - audio input `audio.mp3`
  - video filter graph:
    - per-image scale/pad
    - per-transition xfade when type=fade
    - concat for none
    - optional `ass=subtitles.ass` burn-in
  - output:
    - `output.mp4` (`libx264` + `aac`, `30fps`, `-shortest`)
  - working dir is project directory to resolve relative ASS path

---

## 6) Frontend: Pages and State Machines

## 6.1 API wrapper (`frontend/lib/api.ts`)
- single base constant:
  - `API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"`
- all operations implemented as typed `fetch` wrappers
- `fileUrl(id,path)` constructs backend file endpoint
- **critical mismatch bug**:
  - `regeneratePrompt(id, segId, segment_text)` posts to `/regenerate-prompt` (missing `/{segment_id}`)
  - backend expects `/regenerate-prompt/{segment_id}`

## 6.2 Home page (`frontend/app/page.tsx`)
- modal to create project
- drawer lists previous projects
- displayed step uses `project.current_step` even though list API does not return it
- uses custom toasts

## 6.3 Project wizard page (`frontend/app/project/[id]/page.tsx`)
- step list includes: `1, 2, 3, 3.5, 4, 4.1, 4.5, 5, 6, 7, 8`
- loads templates and files conditionally by active step
- local per-step state for copy flags, job polling, inputs, etc.
- major actions by step:
  - 1: title update + `stepComplete(1)`
  - 2: clean + save script + `stepComplete(2)`
  - 3: generate prompts job + poll + batch UI
  - 3.5: image check + mark complete
  - 4: thumbnail prompt + optional custom note + `stepComplete(4,4.1)`
  - 4.1: thumbnail-review prompt + copy + `stepComplete(4.1,4.5)`
  - 4.5: display script + proceed `stepComplete(4.5,5)`
  - 5: upload audio + poll status + on done assemble + navigate editor
  - 7: SEO prompt + user paste output + save as script + `stepComplete(7)`
  - 8: final review; save `seo_data` + `stepComplete(8)`
- supports reset modal (`reset-step`) and full delete modal

## 6.4 Scene batch component (`frontend/components/SceneBatchPage.tsx`)
- paginated cards (13/page)
- sequence-copy mode with localStorage persistence:
  - `mindshift_seq_{projectId}`
  - `mindshift_copied_{projectId}`
- per-card copy and regenerate controls

## 6.5 Editor (`frontend/app/project/[id]/editor/page.tsx`)
- loads project, segments, and editor state
- uses saved `editor_state.segments` when present
- preloads all scene images
- renders playback canvas with transition crossfade emulation
- separate hidden audio element for sync
- subtitle rendering:
  - supports per-word highlight color
  - outline width, color, font size, positionY
- subtitle editing:
  - contentEditable line text
  - recalculates per-word times evenly within line span
- transitions panel:
  - per-boundary none/fade + duration
  - apply/unapply fade globally
- timeline:
  - zoom controls
  - draggable segment edges (left/right)
  - snap to playhead threshold
  - select segment: replace image / delete scene / add scene
- image replace/add uses direct fetch and cache-busting URL query
- export:
  - builds `editor_state` payload and posts export job
  - polls job status overlay
  - on done offers download + `Next Step` button (`stepComplete(6)`)

---

## 7) Styling/UI system

- Tailwind v4 via `@import "tailwindcss"` in `globals.css`
- custom CSS vars:
  - `--bg-main`, `--bg-card`, `--accent-green`, etc.
- dark-green design language across all pages
- custom toast hook/component for success/error notifications

---

## 8) Data Contracts and File Schemas

## 8.1 `meta.json`
- typical keys:
  - `id` (present in root `videos/*`, absent in backend sample)
  - `title`
  - `current_step` (float in root samples)
  - `audio_job_id` optional

## 8.2 `mj_prompts.json` array item
- `segment_id: number`
- `segment_text: string`
- `scene_type: string`
- `character: string`
- `act: string`
- `prompt_text: string`
- `mj_line: string` (appended params)

## 8.3 transcript JSONs (`audio_transcript.json`, `audio_corrected.json`)
- root keys:
  - `text`, `language`, `segments`, `words`, `duration`
- `words[]`: `{ word, start, end }`

## 8.4 `segments.json` item
- `segment_id`, `image`, `start`, `end`, `text`, `matched`, `match_ratio`

## 8.5 `editor_state.json`
- keys observed:
  - `segments[]`
  - `subtitles[]`
  - `transitions` map with `"i-(i+1)"` keys
  - `subtitleSettings`
- subtitle item:
  - `start`, `end`, `text`, `words[]`

## 8.6 SRT outputs
- `audio_subtitles.srt`: raw whisper words
- `audio_corrected.srt`: corrected words

---

## 9) Prompt Template System

Prompt files are plain text served by backend:
- `1_channel_strategy.txt`: asks for 30 title ideas grouped by categories with emotion/angle/CPM
- `2_script_generation.txt`: two-stage workflow (hook options then full script), research-heavy rules
- `4_thumbnail.txt`: 3 thumbnail concept generator with detailed style/composition constraints
- `5_seo.txt`: asks for full YouTube upload package (description, chapters, tags, alt titles)
- `8_thumbnail_review_prompt.txt`: critique/regeneration workflow for selecting best thumbnail set

All templates are very long and instruction-dense. They are critical to output quality and should be preserved verbatim for functional parity.

---

## 10) Known Mismatches, Risks, and Behavioral Quirks

1. **Regenerate prompt endpoint mismatch**
   - frontend URL omits `segment_id`, backend requires it.

2. **Project list step display mismatch**
   - UI expects `current_step` from list route; backend list omits it.

3. **Hardcoded debug path**
   - `ping-file` references fixed local Windows path.

4. **In-memory job status**
   - any backend restart loses all job states.

5. **Image upload URL fallback inconsistency**
   - editor uses `process.env.NEXT_PUBLIC_API_URL` directly for replace/add image requests.

6. **Timing overlap possible**
   - assembled segment starts can overlap or reset to 0 for unmatched probes; editor may later adjust manually.

---

## 11) Dependencies Snapshot

Backend (`requirements.txt`):
- `fastapi`
- `uvicorn[standard]`
- `python-multipart`
- `openai`
- `python-dotenv`
- `rapidfuzz`
- `aiofiles`
- `pydub`

Frontend (`package.json`):
- deps: `next@16.1.6`, `react@19.2.3`, `react-dom@19.2.3`
- devDeps: tailwind v4 toolchain, types, eslint, typescript

---

## 12) End-to-End Rebuild Blueprint (for another AI)

If reproducing from scratch:

1. Create monorepo folders `backend`, `frontend`, `prompts`, `videos`.
2. Rebuild FastAPI app with route signatures and JSON contracts listed above.
3. Recreate service modules with same function names and algorithmic behavior:
   - script split -> MJ generation -> audio transcribe/correct -> assemble -> export.
4. Recreate Next.js app with routes:
   - `/` home, `/project/[id]`, `/project/[id]/editor`.
5. Mirror step state machine values including fractional steps (`3.5`, `4.1`, `4.5`).
6. Preserve file naming conventions exactly (`{segment_id}.png`, `audio_corrected.json`, etc.).
7. Implement editor timeline + subtitle + transition + export payload format.
8. Preserve prompt templates as text assets and serve them through backend route.
9. Use local filesystem persistence under `videos/{project-id}`.
10. Add ffmpeg export logic with ASS subtitle optional burn-in and xfade transitions.

---

## 13) What This Report Does and Doesn’t Include

Included:
- full architecture
- route-by-route behavior
- service-level logic
- frontend flow/state behavior
- data schemas and file contracts
- observed sample data behavior and quirks

Not fully inlined:
- entire gigantic text bodies of long prompt templates and large JSON arrays are not duplicated completely in this report because they already exist as authoritative source files in:
  - `prompts/*.txt`
  - `videos/*/*.json`
  - `backend/videos/sleep/*.json`

For exact byte-level reconstruction, copy these files directly from repository.

