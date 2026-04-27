import asyncio
import json
import os
import shutil
import traceback
import uuid
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from pathlib import Path
from dotenv import load_dotenv

from services.prompt_service import *
from services.punctuation_service import punctuate_subtitle_lines
from services.segment_images import (
    grok_stem_for_segment_id,
    insert_grok_download_slot,
    resolve_segment_image_basename,
)
from services.segment_service import *
from services.audio_service import *
from services.assembly_service import *
from services.export_service import *
from services.clean_service import *
from services.job_store import job_put, job_get, recover_stale_jobs_on_startup, watchdog_reap_stale_processing
from services.thumbnail_service import generate_three_thumbnail_prompts
from services.title_service import generate_video_title
from services.seo_service import generate_seo_package

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    if not shutil.which("ffmpeg"):
        print("Warning: ffmpeg not found in PATH. Video export may fail.")
    try:
        n = recover_stale_jobs_on_startup()
        if n:
            print(f"Job store: marked {n} interrupted job(s) after restart.")
    except Exception as e:
        print("Job recovery:", e)

    async def _watchdog():
        while True:
            await asyncio.sleep(300)
            try:
                watchdog_reap_stale_processing()
            except Exception as ex:
                print("Job watchdog:", ex)

    wd = asyncio.create_task(_watchdog())
    yield
    wd.cancel()
    try:
        await wd
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
VIDEOS_DIR = Path(__file__).parent.parent / 'videos'
PROMPTS_DIR = Path(__file__).parent.parent / 'prompts'

PROMPT_NAME_TO_FILE = {
    "1_channel_strategy": "1_channel_strategy.txt",
    "2a_perplexity_research": "2a_perplexity_research.txt",
    "2_script_generation": "2_script_generation.txt",
    "4_thumbnail": "4_thumbnail.txt",
    "5_seo": "5_seo.txt",
}

VIDEOS_DIR.mkdir(exist_ok=True)
PROMPTS_DIR.mkdir(exist_ok=True)


def get_project_dir(id: str) -> Path:
    project_dir = VIDEOS_DIR / id
    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")
    return project_dir


def _maybe_migrate_pipeline_step_order(meta: dict) -> bool:
    """
    Older meta used: 4=thumb, 4.1, 4.5=script, 7=SEO, 8=final.
    New order: 4=script, 5=audio, 6=edit, 7/7.1=thumb, 8=SEO, 9=final.
    If step_schema is 2, no migration. Otherwise rewrite current_step and set step_schema=2.
    """
    if meta.get("step_schema") == 2:
        return False
    try:
        cs = meta.get("current_step", 1)
        f = float(cs)
    except (TypeError, ValueError):
        meta["step_schema"] = 2
        return True
    m: dict = {4.1: 7.1, 4.5: 4.0, 7: 8.0, 8: 9.0}
    if f in m:
        meta["current_step"] = m[f]
    elif abs(f - 4.0) < 1e-6:
        # Old "Thumbnail" was 4; new 4 is "Script for recording" (was 4.5)
        meta["current_step"] = 7.0
    meta["step_schema"] = 2
    return True


def read_meta(id: str) -> dict:
    project_dir = get_project_dir(id)
    meta_path = project_dir / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Project meta not found")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    if _maybe_migrate_pipeline_step_order(meta):
        with open(meta_path, "w", encoding="utf-8") as out:
            json.dump(meta, out, indent=2, ensure_ascii=False)
    return meta


def write_meta(id: str, meta: dict) -> None:
    project_dir = get_project_dir(id)
    meta_path = project_dir / "meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def slugify(title: str) -> str:
    s = title.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[-\s]+", "-", s).strip("-")
    return s[:60] if len(s) > 60 else s


# --- Request bodies ---


class CreateProjectBody(BaseModel):
    title: str
    image_provider: str | None = "midjourney"  # "midjourney" | "grok"


class DeleteProjectBody(BaseModel):
    confirm_title: str


class UpdateTitleBody(BaseModel):
    title: str


class StepBody(BaseModel):
    step: float


class StepCompleteBody(BaseModel):
    step: float
    next_step: float | None = None


class ScriptBody(BaseModel):
    script: str
    is_cleaned: bool


class CleanScriptBody(BaseModel):
    script: str


class SeoDataBody(BaseModel):
    description: str = ""
    chapter_timestamps: str = ""
    tags: str = ""
    seo_title: str = ""
    cleaned_script: str = ""
    deepseek_video_title: str = ""
    deepseek_title_raw: str = ""


class RegeneratePromptBody(BaseModel):
    segment_text: str


class ExportBody(BaseModel):
    editor_state: dict


class ImageProviderBody(BaseModel):
    image_provider: str


class PipelineSettingsBody(BaseModel):
    whisperx_align: bool | None = None
    demucs_vocals: bool | None = None
    subtitle_max_cps: float | None = None


# --- Routes ---


@app.get("/ping-file")
def ping_file():
    """Test route: serve the known file directly."""
    return FileResponse(r"D:\Goal\MindShift-Lab\videos\sleep\images\1.png")


@app.get("/api/projects")
def list_projects():
    projects = []
    for p in VIDEOS_DIR.iterdir():
        if not p.is_dir():
            continue
        meta_path = p / "meta.json"
        if not meta_path.exists():
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        mtime = p.stat().st_mtime
        projects.append({"id": p.name, "title": meta.get("title", p.name), "mtime": mtime})
    projects.sort(key=lambda x: x["mtime"], reverse=True)
    return [
        {"id": p["id"], "title": p["title"], "mtime": p["mtime"]}
        for p in projects
    ]


@app.post("/api/projects")
def create_project(body: CreateProjectBody):
    # Count existing project folders (directories only; ignore .gitkeep and other files)
    count = sum(1 for p in VIDEOS_DIR.iterdir() if p.is_dir())
    number = count + 1
    folder_name = f"{number} - {body.title}"
    project_dir = VIDEOS_DIR / folder_name
    project_dir.mkdir(parents=True)
    (project_dir / "images").mkdir(exist_ok=True)
    thumb_dir = project_dir / "thumbnail"
    (thumb_dir / "working").mkdir(parents=True, exist_ok=True)
    (thumb_dir / "final").mkdir(parents=True, exist_ok=True)
    prov = (body.image_provider or "midjourney").lower()
    if prov not in ("midjourney", "grok"):
        prov = "midjourney"
    meta = {
        "id": folder_name,
        "title": body.title,
        "current_step": 1,
        "step_schema": 2,
        "image_provider": prov,
        "whisperx_align": True,
        "demucs_vocals": False,
        "subtitle_max_cps": 20.0,
    }
    with open(project_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return {"id": folder_name, "title": body.title}


@app.get("/test-file")
def test_file():
    """Debug: verify VIDEOS_DIR and that the test image exists."""
    p = VIDEOS_DIR / "sleep" / "images" / "1.png"
    return {"videos_dir": str(VIDEOS_DIR), "file_exists": str(p.exists())}


@app.get("/api/projects/{id}/files/{filepath:path}")
def get_project_file(id: str, filepath: str):
    project_dir = get_project_dir(id)
    full_path = (project_dir / filepath).resolve()
    project_dir_resolved = project_dir.resolve()
    # Windows-safe "path under project" check: ensure full_path is inside project_dir
    base = os.path.normpath(os.path.normcase(str(project_dir_resolved).rstrip(os.sep))) + os.sep
    full_str = os.path.normpath(os.path.normcase(str(full_path)))
    if not (full_str == base.rstrip(os.sep) or full_str.startswith(base)):
        raise HTTPException(status_code=404, detail="Not found")
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(full_path))


@app.get("/api/projects/{id}")
def get_project(id: str):
    meta = read_meta(id)
    meta['current_step'] = float(meta.get('current_step', 1))
    if meta.get("image_provider") not in ("midjourney", "grok"):
        meta["image_provider"] = "midjourney"
    meta.setdefault("whisperx_align", True)
    meta.setdefault("demucs_vocals", False)
    if "subtitle_max_cps" not in meta:
        meta["subtitle_max_cps"] = 20.0
    return meta


@app.delete("/api/projects/{id}")
def delete_project(id: str, body: DeleteProjectBody):
    meta = read_meta(id)
    if body.confirm_title != meta.get("title", ""):
        raise HTTPException(status_code=400, detail="confirm_title does not match project title")
    project_dir = get_project_dir(id)
    shutil.rmtree(project_dir)
    return {"ok": True}


@app.patch("/api/projects/{id}/image-provider")
def patch_image_provider(id: str, body: ImageProviderBody):
    meta = read_meta(id)
    p = (body.image_provider or "").lower()
    if p not in ("midjourney", "grok"):
        raise HTTPException(status_code=400, detail="image_provider must be midjourney or grok")
    meta["image_provider"] = p
    write_meta(id, meta)
    return meta


@app.patch("/api/projects/{id}/pipeline-settings")
def patch_pipeline_settings(id: str, body: PipelineSettingsBody):
    meta = read_meta(id)
    if body.whisperx_align is not None:
        meta["whisperx_align"] = bool(body.whisperx_align)
    if body.demucs_vocals is not None:
        meta["demucs_vocals"] = bool(body.demucs_vocals)
    if body.subtitle_max_cps is not None:
        try:
            v = float(body.subtitle_max_cps)
            meta["subtitle_max_cps"] = max(14.0, min(v, 24.0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="subtitle_max_cps must be a number")
    write_meta(id, meta)
    return meta


@app.patch("/api/projects/{id}/title")
def update_project_title(id: str, body: UpdateTitleBody):
    meta = read_meta(id)
    meta["title"] = body.title
    write_meta(id, meta)
    return meta


@app.post("/api/projects/{id}/reset-step")
def reset_step(id: str, body: StepBody):
    project_dir = get_project_dir(id)
    step = body.step
    if step <= 2:
        (project_dir / "script.txt").unlink(missing_ok=True)
        (project_dir / "script_raw.txt").unlink(missing_ok=True)
    if step <= 3:
        (project_dir / "mj_prompts.json").unlink(missing_ok=True)
        images_dir = project_dir / "images"
        if images_dir.is_dir():
            for f in images_dir.iterdir():
                f.unlink()
    if step <= 5:
        for name in (
            "audio.mp3",
            "audio_transcript.json",
            "audio_subtitles.srt",
            "audio_corrected.json",
            "audio_corrected.srt",
        ):
            (project_dir / name).unlink(missing_ok=True)
        for f in project_dir.glob("_chunk_*.mp3"):
            f.unlink(missing_ok=True)
    if step <= 6:
        for name in (
            "segments.json",
            "editor_state.json",
            "output.mp4",
            "subtitles.ass",
            "subtitles_upload.srt",
            "subtitles_upload.vtt",
            "thumbnail_prompts.json",
            "publish_bundle.json",
        ):
            (project_dir / name).unlink(missing_ok=True)

    meta = read_meta(id)
    meta["current_step"] = step
    write_meta(id, meta)
    return meta


# 4 = script for recording, 7/7.1 = thumbnails (after 6 = assemble and edit), 8 = SEO, 9 = final
_PIPELINE_STEPS: tuple[float, ...] = (1, 2, 3, 3.5, 4, 5, 6, 7, 7.1, 8, 9)


def _default_next_current_step(completed: float) -> float:
    for i, s in enumerate(_PIPELINE_STEPS):
        if s == completed:
            if i + 1 < len(_PIPELINE_STEPS):
                return _PIPELINE_STEPS[i + 1]
            return completed + 1.0
    return completed + 1.0


@app.post("/api/projects/{id}/step-complete")
def step_complete(id: str, body: StepCompleteBody):
    meta = read_meta(id)
    current = meta.get("current_step", 0)
    effective_next = (
        body.next_step if body.next_step is not None else _default_next_current_step(body.step)
    )
    meta["current_step"] = max(current, effective_next)
    write_meta(id, meta)
    return meta


@app.get("/api/prompts/{name}")
def get_prompt(name: str):
    PROMPT_FILES = {
        "1_channel_strategy": "1_channel_strategy.txt",
        "2_script_generation": "2_script_generation.txt",
        "4_thumbnail": "4_thumbnail.txt",
        "5_seo": "5_seo.txt",
        "8_thumbnail_review_prompt": "8_thumbnail_review_prompt.txt",
    }
    filename = PROMPT_FILES.get(name)
    if not filename:
        raise HTTPException(status_code=404, detail="Unknown prompt")
    path = PROMPTS_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    return {"content": path.read_text(encoding="utf-8")}


@app.post("/api/projects/{id}/script")
def save_script(id: str, body: ScriptBody):
    project_dir = get_project_dir(id)
    if body.is_cleaned:
        with open(project_dir / "script.txt", "w", encoding="utf-8") as f:
            f.write(body.script)
        meta = read_meta(id)
        meta["current_step"] = max(meta.get("current_step", 0), 3)
        write_meta(id, meta)
    else:
        with open(project_dir / "script_raw.txt", "w", encoding="utf-8") as f:
            f.write(body.script)
    return {"ok": True}


@app.post("/api/clean-script")
async def clean_script_global(body: CleanScriptBody):
    """Body-only cleaning (no project id in URL) — avoids fetch failures from encoded path segments."""
    cleaned_script = await clean_script(body.script)
    return {"cleaned_script": cleaned_script}


@app.post("/api/projects/{id}/clean-script")
async def clean_script_route(id: str, body: CleanScriptBody):
    cleaned_script = await clean_script(body.script)
    return {"cleaned_script": cleaned_script}


def _run_generate_prompts(project_id: str, script_text: str, job_id: str, image_provider: str):
    job_status: dict = {"status": "pending", "percent": 0}
    job_put(job_id, job_status)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        results = loop.run_until_complete(
            generate_mj_prompts(project_id, script_text, job_status, image_provider=image_provider)
        )
        project_dir = get_project_dir(project_id)
        with open(project_dir / "mj_prompts.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        meta = read_meta(project_id)
        # Keep user at image-check stage after prompt generation so progress
        # can be resumed without forcing a fresh prompt run.
        meta["current_step"] = max(meta.get("current_step", 0), 3.5)
        write_meta(project_id, meta)
    except Exception:
        import traceback
        job_status["status"] = "error"
        job_status["error"] = traceback.format_exc()
    finally:
        job_put(job_id, job_status)
        loop.close()


@app.post("/api/projects/{id}/generate-prompts")
def generate_prompts(id: str, background_tasks: BackgroundTasks):
    project_dir = get_project_dir(id)
    script_path = project_dir / "script.txt"
    if not script_path.exists():
        raise HTTPException(status_code=400, detail="script.txt not found")
    with open(script_path, "r", encoding="utf-8") as f:
        script_text = f.read()
    job_id = str(uuid.uuid4())
    job_put(job_id, {"status": "pending", "percent": 0})
    meta_pm = read_meta(id)
    ip = str(meta_pm.get("image_provider", "midjourney")).lower()
    if ip not in ("midjourney", "grok"):
        ip = "midjourney"
    background_tasks.add_task(_run_generate_prompts, id, script_text, job_id, ip)
    return {"job_id": job_id}


@app.get("/api/projects/{id}/prompts")
def get_prompts(id: str):
    project_dir = get_project_dir(id)
    path = project_dir / "mj_prompts.json"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@app.post("/api/projects/{id}/regenerate-prompt/{segment_id}")
async def regenerate_prompt(id: str, segment_id: int, body: RegeneratePromptBody):
    project_dir = get_project_dir(id)
    path = project_dir / "mj_prompts.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="mj_prompts.json not found")
    with open(path, "r", encoding="utf-8") as f:
        prompts = json.load(f)
    meta_r = read_meta(id)
    ip = str(meta_r.get("image_provider", "midjourney")).lower()
    if ip not in ("midjourney", "grok"):
        ip = "midjourney"
    record = await regenerate_single_prompt(body.segment_text, segment_id, ip)
    for i, p in enumerate(prompts):
        if p.get("segment_id") == segment_id:
            record.setdefault("scene_type", p.get("scene_type", ""))
            prompts[i] = record
            break
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prompts, f, indent=2)
    return record


@app.get("/api/prompts/grok-image-system")
def grok_image_system_prompt_route():
    """Combined DeepSeek system prompt for Grok scene generation (whole script)."""
    text = get_grok_image_system_prompt_combined()
    return {"content": text}


@app.post("/api/projects/{id}/generate-video-title")
async def generate_video_title_route(id: str):
    """DeepSeek CTR title using deepseek-title-system-prompt.md + script.txt."""
    project_dir = get_project_dir(id)
    script_path = project_dir / "script.txt"
    if not script_path.exists():
        raise HTTPException(status_code=400, detail="script.txt required — save your script first")
    script_text = script_path.read_text(encoding="utf-8")
    final_title, raw_resp = await generate_video_title(script_text)
    seo_path = project_dir / "seo_data.json"
    merged: dict = {}
    if seo_path.exists():
        try:
            merged = json.loads(seo_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            merged = {}
    merged["deepseek_video_title"] = final_title
    merged["deepseek_title_raw"] = raw_resp
    with open(seo_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    return {"final_title": final_title, "raw": raw_resp}


@app.post("/api/projects/{id}/generate-seo")
async def generate_seo_route(id: str):
    """DeepSeek generates full SEO package: title, description, timestamps, tags, alt titles."""
    project_dir = get_project_dir(id)
    script_path = project_dir / "script.txt"
    if not script_path.exists():
        raise HTTPException(status_code=400, detail="script.txt required — save your script first")
    script_text = script_path.read_text(encoding="utf-8")
    meta_path = project_dir / "meta.json"
    working_title = ""
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            working_title = meta.get("title", "")
        except json.JSONDecodeError:
            pass
    pkg = await generate_seo_package(working_title, script_text)
    # Merge into existing seo_data.json so we don't clobber deepseek_video_title
    seo_path = project_dir / "seo_data.json"
    merged: dict = {}
    if seo_path.exists():
        try:
            merged = json.loads(seo_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            merged = {}
    merged.update({
        "description": pkg["description"],
        "chapter_timestamps": pkg["chapter_timestamps"],
        "tags": pkg["tags"],
        "seo_title": pkg["title"],
        "alternative_titles": pkg["alternative_titles"],
    })
    with open(seo_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    return pkg


@app.get("/api/projects/{project_id}/seo-data")
def get_seo_data(project_id: str):
    project_dir = get_project_dir(project_id)
    path = project_dir / "seo_data.json"
    if not path.exists():
        return {
            "description": "",
            "chapter_timestamps": "",
            "tags": "",
            "seo_title": "",
            "cleaned_script": "",
            "deepseek_video_title": "",
            "deepseek_title_raw": "",
        }
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
        return {
            "description": data.get("description", ""),
            "chapter_timestamps": data.get("chapter_timestamps", ""),
            "tags": data.get("tags", ""),
            "seo_title": data.get("seo_title", ""),
            "cleaned_script": data.get("cleaned_script", ""),
            "deepseek_video_title": data.get("deepseek_video_title", ""),
            "deepseek_title_raw": data.get("deepseek_title_raw", ""),
        }


@app.post("/api/projects/{project_id}/seo-data")
def save_seo_data(project_id: str, body: SeoDataBody):
    project_dir = get_project_dir(project_id)
    path = project_dir / "seo_data.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "description": body.description,
                "chapter_timestamps": body.chapter_timestamps,
                "tags": body.tags,
                "seo_title": body.seo_title,
                "cleaned_script": body.cleaned_script,
                "deepseek_video_title": body.deepseek_video_title,
                "deepseek_title_raw": body.deepseek_title_raw,
            },
            f,
            indent=2,
        )
    return {"ok": True}


@app.get("/api/projects/{id}/image-check")
def image_check(id: str):
    project_dir = get_project_dir(id)
    path = project_dir / "mj_prompts.json"
    if not path.exists():
        return {"present": [], "missing": [], "total_expected": 0}
    with open(path, "r", encoding="utf-8") as f:
        prompts = json.load(f)
    total_expected = len(prompts)
    images_dir = project_dir / "images"
    present = []
    missing = []
    for p in prompts:
        seg_id = p.get("segment_id")
        if seg_id is None:
            continue
        try:
            sid = int(seg_id)
        except (TypeError, ValueError):
            continue
        found = resolve_segment_image_basename(images_dir, sid)
        if found:
            present.append(found)
        else:
            missing.append(str(sid))
    return {"present": present, "missing": missing, "total_expected": total_expected}


@app.post("/api/projects/{id}/replace-image/{image_name:path}")
async def replace_image(id: str, image_name: str, file: UploadFile = File(...)):
    project_dir = get_project_dir(id)
    images_dir = project_dir / "images"
    images_dir.mkdir(exist_ok=True)
    # Only basename: ignore path segments so clients never write outside images/
    dest = images_dir / Path(str(image_name)).name
    contents = await file.read()
    with open(dest, "wb") as f:
        f.write(contents)
    return {"status": "ok", "image": image_name}


class InsertGrokImageSlotBody(BaseModel):
    segment_id: int


@app.post("/api/projects/{id}/images/insert-grok-slot")
def insert_grok_image_filename_slot(id: str, body: InsertGrokImageSlotBody):
    """
    Shift Grok download–named files (download.jfif, download (1).jfif, …) in images/
    so there is a free filename for the given scene. Does not change numbered 1.png style files.
    """
    if body.segment_id < 1:
        raise HTTPException(status_code=400, detail="segment_id must be >= 1")
    project_dir = get_project_dir(id)
    images_dir = project_dir / "images"
    images_dir.mkdir(exist_ok=True)
    try:
        renamed = insert_grok_download_slot(images_dir, int(body.segment_id))
    except OSError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    stem = grok_stem_for_segment_id(int(body.segment_id))
    return {
        "segment_id": body.segment_id,
        "save_next_as": f"{stem}.jfif (or .png / .jpg)",
        "renamed": renamed,
    }


# --- Part 3: Audio, Assembly, Export, Files ---


async def _run_audio_task(project_id: str, job_id: str) -> None:
    project_dir = get_project_dir(project_id)
    st: dict = {"status": "pending", "percent": 0}
    job_put(job_id, st)
    try:
        await process_audio(project_id, project_dir, st, job_id)
    except Exception:
        st["status"] = "error"
        st["error"] = traceback.format_exc()
    job_put(job_id, st)
    meta = read_meta(project_id)
    meta["audio_job_id"] = job_id
    write_meta(project_id, meta)


async def _run_export_task(project_id: str, project_dir: Path, editor_state: dict, job_id: str) -> None:
    st = {"status": "pending", "percent": 0}
    job_put(job_id, st)
    try:
        await asyncio.to_thread(build_and_export, project_id, project_dir, editor_state, st)
    except Exception:
        traceback.print_exc()
        st["status"] = "error"
        st["error"] = traceback.format_exc()
    job_put(job_id, st)


@app.post("/api/projects/{id}/audio")
async def upload_audio(id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    project_dir = get_project_dir(id)
    audio_path = project_dir / "audio.mp3"
    contents = await file.read()
    with open(audio_path, "wb") as f:
        f.write(contents)
    job_id = str(uuid.uuid4())
    job_put(job_id, {"status": "pending", "percent": 0})
    background_tasks.add_task(_run_audio_task, id, job_id)
    return {"job_id": job_id}


@app.get("/api/projects/{id}/audio-status")
def get_audio_status(id: str):
    project_dir = get_project_dir(id)
    transcript_ready = (
        (project_dir / "audio_corrected.json").is_file()
        or (project_dir / "audio_transcript.json").is_file()
    )
    meta = read_meta(id)
    audio_job_id = meta.get("audio_job_id")
    if not audio_job_id:
        return {"status": "not_started", "transcript_ready": transcript_ready}
    try:
        j = dict(job_get(audio_job_id))
    except KeyError:
        return {"status": "not_started", "transcript_ready": transcript_ready}
    # Job DB can say "done" after server restart or if JSON files were deleted — don't trust it alone.
    if j.get("status") == "done" and not transcript_ready:
        j["status"] = "error"
        j["error"] = (
            "Transcript files are missing (audio_corrected.json / audio_transcript.json). "
            "Re-upload your audio.mp3 on Step 5 to regenerate them."
        )
    j["transcript_ready"] = transcript_ready
    return j


@app.post("/api/projects/{id}/assemble")
def assemble(id: str):
    import traceback
    import time

    t0 = time.perf_counter()
    try:
        project_dir = get_project_dir(id)
        segments = map_segments_to_timestamps(id, project_dir)
        meta = read_meta(id)
        write_meta(id, meta)
        elapsed = time.perf_counter() - t0
        print(f"[assemble] project={id!r} segments={len(segments)} took {elapsed:.2f}s", flush=True)
        return segments
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{id}/segments")
def get_segments(id: str):
    project_dir = get_project_dir(id)
    path = project_dir / "segments.json"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@app.post("/api/projects/{id}/generate-subtitles")
def generate_subtitles(id: str):
    project_dir = get_project_dir(id)
    path = project_dir / "audio_corrected.json"
    if not path.exists():
        path = project_dir / "audio_transcript.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No audio transcript found")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    meta = read_meta(id)
    try:
        max_cps = float(meta.get("subtitle_max_cps", 20.0))
    except (TypeError, ValueError):
        max_cps = 20.0
    max_cps = max(14.0, min(max_cps, 24.0))
    lines = build_subtitle_lines(data, max_cps=max_cps)
    lines = punctuate_subtitle_lines(lines)
    return JSONResponse(
        content=lines,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.put("/api/projects/{id}/editor-state")
def save_editor_state(id: str, body: dict = Body(...)):
    project_dir = get_project_dir(id)
    with open(project_dir / "editor_state.json", "w", encoding="utf-8") as f:
        json.dump(body, f, indent=2)
    return {"saved": True}


@app.get("/api/projects/{id}/editor-state")
def get_editor_state(id: str):
    project_dir = get_project_dir(id)
    path = project_dir / "editor_state.json"
    if not path.exists():
        return JSONResponse(
            content={},
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )
    with open(path, "r", encoding="utf-8") as f:
        body = json.load(f)
    return JSONResponse(
        content=body,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.post("/api/projects/{id}/export")
async def export_project(id: str, body: ExportBody, background_tasks: BackgroundTasks):
    project_dir = get_project_dir(id)
    job_id = str(uuid.uuid4())
    job_put(job_id, {"status": "pending", "percent": 0})
    background_tasks.add_task(_run_export_task, id, project_dir, body.editor_state, job_id)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    try:
        return job_get(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")


@app.post("/api/projects/{id}/thumbnail-prompts/generate")
async def generate_thumbnail_prompts_route(id: str):
    meta = read_meta(id)
    script_path = get_project_dir(id) / "script.txt"
    if not script_path.exists():
        raise HTTPException(status_code=400, detail="script.txt required")
    script_text = script_path.read_text(encoding="utf-8")
    prompts = await generate_three_thumbnail_prompts(
        title=meta.get("title", ""),
        script_excerpt=script_text,
    )
    out_path = get_project_dir(id) / "thumbnail_prompts.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(prompts, f, indent=2, ensure_ascii=False)
    return {"prompts": prompts}


@app.get("/api/projects/{id}/thumbnail-prompts")
def get_thumbnail_prompts_route(id: str):
    path = get_project_dir(id) / "thumbnail_prompts.json"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
