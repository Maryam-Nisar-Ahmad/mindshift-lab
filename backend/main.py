import asyncio
import json
import os
import shutil
import traceback
import uuid
import re

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
from dotenv import load_dotenv

from services.prompt_service import *
from services.segment_service import *
from services.audio_service import *
from services.assembly_service import *
from services.export_service import *
from services.clean_service import *

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

app = FastAPI()
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
    "2_script_generation": "2_script_generation.txt",
    "4_thumbnail": "4_thumbnail.txt",
    "5_seo": "5_seo.txt",
}

VIDEOS_DIR.mkdir(exist_ok=True)
PROMPTS_DIR.mkdir(exist_ok=True)

_job_status = {}


def get_project_dir(id: str) -> Path:
    project_dir = VIDEOS_DIR / id
    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")
    return project_dir


def read_meta(id: str) -> dict:
    project_dir = get_project_dir(id)
    meta_path = project_dir / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Project meta not found")
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


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


@app.on_event("startup")
def startup():
    if not shutil.which("ffmpeg"):
        print("Warning: ffmpeg not found in PATH. Video export may fail.")


# --- Request bodies ---


class CreateProjectBody(BaseModel):
    title: str


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


class RegeneratePromptBody(BaseModel):
    segment_text: str


class ExportBody(BaseModel):
    editor_state: dict


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
    return [{"id": p["id"], "title": p["title"]} for p in projects]


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
    meta = {"id": folder_name, "title": body.title, "current_step": 1}
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
    return meta


@app.delete("/api/projects/{id}")
def delete_project(id: str, body: DeleteProjectBody):
    meta = read_meta(id)
    if body.confirm_title != meta.get("title", ""):
        raise HTTPException(status_code=400, detail="confirm_title does not match project title")
    project_dir = get_project_dir(id)
    shutil.rmtree(project_dir)
    return {"ok": True}


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
        for name in ("segments.json", "editor_state.json", "output.mp4", "subtitles.ass"):
            (project_dir / name).unlink(missing_ok=True)

    meta = read_meta(id)
    meta["current_step"] = step
    write_meta(id, meta)
    return meta


@app.post("/api/projects/{id}/step-complete")
def step_complete(id: str, body: StepCompleteBody):
    meta = read_meta(id)
    current = meta.get("current_step", 0)
    effective_next = body.next_step if body.next_step is not None else body.step + 1
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


def _run_generate_prompts(project_id: str, script_text: str, job_id: str):
    job_status = _job_status[job_id]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        results = loop.run_until_complete(generate_mj_prompts(project_id, script_text, job_status))
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
    _job_status[job_id] = {"status": "pending", "percent": 0}
    background_tasks.add_task(_run_generate_prompts, id, script_text, job_id)
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
    record = await regenerate_single_prompt(body.segment_text, segment_id)
    for i, p in enumerate(prompts):
        if p.get("segment_id") == segment_id:
            record.setdefault("scene_type", p.get("scene_type", ""))
            prompts[i] = record
            break
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prompts, f, indent=2)
    return record


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
        }
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
        return {
            "description": data.get("description", ""),
            "chapter_timestamps": data.get("chapter_timestamps", ""),
            "tags": data.get("tags", ""),
            "seo_title": data.get("seo_title", ""),
            "cleaned_script": data.get("cleaned_script", ""),
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
        n = f"{seg_id}.png"
        if (images_dir / n).exists():
            present.append(n)
        else:
            missing.append(n)
    return {"present": present, "missing": missing, "total_expected": total_expected}


@app.post("/api/projects/{id}/replace-image/{image_name}")
async def replace_image(id: str, image_name: str, file: UploadFile = File(...)):
    project_dir = get_project_dir(id)
    images_dir = project_dir / "images"
    images_dir.mkdir(exist_ok=True)
    dest = images_dir / image_name
    contents = await file.read()
    with open(dest, "wb") as f:
        f.write(contents)
    return {"status": "ok", "image": image_name}


# --- Part 3: Audio, Assembly, Export, Files ---


async def _run_audio_task(project_id: str, job_id: str) -> None:
    project_dir = get_project_dir(project_id)
    await process_audio(project_id, project_dir, _job_status[job_id])
    meta = read_meta(project_id)
    meta["audio_job_id"] = job_id
    write_meta(project_id, meta)


async def _run_export_task(project_id: str, project_dir: Path, editor_state: dict, job_id: str) -> None:
    try:
        await asyncio.to_thread(build_and_export, project_id, project_dir, editor_state, _job_status[job_id])
    except Exception:
        import traceback
        traceback.print_exc()
        _job_status[job_id]["status"] = "error"
        _job_status[job_id]["error"] = traceback.format_exc()


@app.post("/api/projects/{id}/audio")
async def upload_audio(id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    project_dir = get_project_dir(id)
    audio_path = project_dir / "audio.mp3"
    contents = await file.read()
    with open(audio_path, "wb") as f:
        f.write(contents)
    job_id = str(uuid.uuid4())
    _job_status[job_id] = {"status": "pending", "percent": 0}
    background_tasks.add_task(_run_audio_task, id, job_id)
    return {"job_id": job_id}


@app.get("/api/projects/{id}/audio-status")
def get_audio_status(id: str):
    meta = read_meta(id)
    audio_job_id = meta.get("audio_job_id")
    if not audio_job_id or audio_job_id not in _job_status:
        return {"status": "not_started"}
    return _job_status[audio_job_id]


@app.post("/api/projects/{id}/assemble")
def assemble(id: str):
    import traceback
    try:
        project_dir = get_project_dir(id)
        segments = map_segments_to_timestamps(id, project_dir)
        meta = read_meta(id)
        write_meta(id, meta)
        return segments
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
    lines = build_subtitle_lines(data)
    return lines


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
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@app.post("/api/projects/{id}/export")
async def export_project(id: str, body: ExportBody, background_tasks: BackgroundTasks):
    project_dir = get_project_dir(id)
    job_id = str(uuid.uuid4())
    _job_status[job_id] = {"status": "pending", "percent": 0}
    background_tasks.add_task(_run_export_task, id, project_dir, body.editor_state, job_id)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    if job_id not in _job_status:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_status[job_id]
