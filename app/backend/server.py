"""AutoCut API — upload a video, get it back edited (silence cuts, 1.2x, burned subtitles).

Run locally:
    uvicorn app.backend.server:app --reload --port 8001
Then open http://localhost:8001/

Storage: one folder per job under $AUTOCUT_DATA (default ./data/jobs/<id>/) holding
input, work files (transcript.json, plan.json, subs.ass) and the output mp4/srt.
Job metadata is mirrored to job.json so a restart does not lose it.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # allow running from the repo without installing the package
    sys.path.insert(0, str(ROOT))

from autocut import pipeline  # noqa: E402
from autocut.plan import EditPlan  # noqa: E402
from autocut.presets import BASE, LAYOUT, PRESETS  # noqa: E402
from autocut.transcribe import Transcript  # noqa: E402

DATA = Path(os.environ.get("AUTOCUT_DATA", ROOT / "data")).resolve()
JOBS = DATA / "jobs"
JOBS.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_MB = int(os.environ.get("AUTOCUT_MAX_UPLOAD_MB", "4096"))
ALLOWED_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}

app = FastAPI(title="AutoCut", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_executor = ThreadPoolExecutor(max_workers=int(os.environ.get("AUTOCUT_WORKERS", "1")))
_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


# --------------------------------------------------------------------------- job store


def _job_dir(job_id: str) -> Path:
    return JOBS / job_id


def _save(job: dict[str, Any]) -> None:
    (_job_dir(job["id"]) / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=1))


def _load_all() -> None:
    for d in JOBS.iterdir():
        f = d / "job.json"
        if f.is_file():
            try:
                job = json.loads(f.read_text())
                if job.get("status") in ("queued", "transcribing", "planning", "rendering"):
                    job["status"] = "error"
                    job["error"] = "server restarted during processing"
                _jobs[job["id"]] = job
            except Exception:
                pass


_load_all()


def _update(job_id: str, **fields: Any) -> None:
    with _lock:
        job = _jobs[job_id]
        job.update(fields)
        job["updated_at"] = time.time()
        _save(job)


def _public(job: dict[str, Any]) -> dict[str, Any]:
    out = dict(job)
    d = _job_dir(job["id"])
    plan_file = d / "work" / "plan.json"
    if plan_file.exists():
        try:
            out["plan"] = json.loads(plan_file.read_text())
        except Exception:
            out["plan"] = None
    out["download_url"] = f"/api/jobs/{job['id']}/download" if job.get("status") == "done" else None
    out["srt_url"] = f"/api/jobs/{job['id']}/srt" if job.get("status") == "done" and (d / "output.srt").exists() else None
    return out


# --------------------------------------------------------------------------- processing


def _progress_fn(job_id: str):
    stage_to_status = {
        "probing": "transcribing", "loading whisper model": "transcribing", "transcribing": "transcribing",
        "transcribing (api)": "transcribing", "planning": "planning", "planned": "planning", "rendering": "rendering",
    }
    last = {"t": 0.0}

    def fn(stage: str, frac: float) -> None:
        now = time.time()
        if now - last["t"] < 0.5 and frac < 1.0:
            return
        last["t"] = now
        _update(job_id, status=stage_to_status.get(stage, "rendering"), stage=stage, progress=round(frac, 3))

    return fn


def _process(job_id: str) -> None:
    job = _jobs[job_id]
    d = _job_dir(job_id)
    work = d / "work"
    try:
        _update(job_id, status="transcribing", started_at=time.time())
        pipeline.run(
            str(d / job["input_name"]),
            str(d / "output.mp4"),
            preset=job["preset"],
            overrides=job.get("overrides") or {},
            workdir=str(work),
            progress=_progress_fn(job_id),
        )
        plan = EditPlan.load(work / "plan.json")
        _update(job_id, status="done", stage="done", progress=1.0, stats=plan.stats(), finished_at=time.time())
    except Exception as exc:  # noqa: BLE001
        _update(job_id, status="error", error=str(exc)[:2000], finished_at=time.time())


def _rerender(job_id: str) -> None:
    d = _job_dir(job_id)
    work = d / "work"
    try:
        _update(job_id, status="rendering", stage="rendering", progress=0.8)
        plan = EditPlan.load(work / "plan.json")
        transcript = Transcript.load(work / "transcript.json") if (work / "transcript.json").exists() else None
        pipeline.render_plan(plan, str(d / "output.mp4"), str(work), transcript=transcript, progress=_progress_fn(job_id))
        plan = EditPlan.load(work / "plan.json")
        _update(job_id, status="done", stage="done", progress=1.0, stats=plan.stats(), finished_at=time.time())
    except Exception as exc:  # noqa: BLE001
        _update(job_id, status="error", error=str(exc)[:2000], finished_at=time.time())


# --------------------------------------------------------------------------- API


@app.get("/api/presets")
def presets() -> dict:
    return {"presets": {k: {**BASE, **v} for k, v in PRESETS.items()}, "layouts": LAYOUT, "defaults": BASE}


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    preset: str = Form("auto"),
    overrides: str = Form("{}"),
) -> dict:
    if preset not in PRESETS:
        raise HTTPException(400, f"unknown preset {preset}")
    ext = Path(file.filename or "video.mp4").suffix.lower() or ".mp4"
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"unsupported file type {ext}")
    try:
        ov = json.loads(overrides or "{}")
        if not isinstance(ov, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "overrides must be a JSON object")

    job_id = uuid.uuid4().hex[:12]
    d = _job_dir(job_id)
    d.mkdir(parents=True)
    input_name = f"input{ext}"
    size = 0
    with open(d / input_name, "wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_MB * 1024 * 1024:
                out.close()
                shutil.rmtree(d, ignore_errors=True)
                raise HTTPException(413, f"file larger than {MAX_UPLOAD_MB} MB")
            out.write(chunk)

    job = {
        "id": job_id,
        "original_name": file.filename,
        "input_name": input_name,
        "size_bytes": size,
        "preset": preset,
        "overrides": ov,
        "status": "queued",
        "stage": "queued",
        "progress": 0.0,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    with _lock:
        _jobs[job_id] = job
        _save(job)
    _executor.submit(_process, job_id)
    return _public(job)


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    with _lock:
        jobs = sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)
    return [{k: v for k, v in j.items() if k != "overrides"} for j in jobs]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return _public(job)


class SegmentPatch(BaseModel):
    index: int
    speed: float | None = None
    enabled: bool | None = None
    zoom: float | None = None


class PlanPatch(BaseModel):
    segments: list[SegmentPatch] = []
    settings: dict[str, Any] = {}  # e.g. {"subtitle_style": "box", "speed": 1.3}


@app.put("/api/jobs/{job_id}/plan")
def update_plan(job_id: str, patch: PlanPatch) -> dict:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] not in ("done", "error"):
        raise HTTPException(409, "job is still processing")
    plan_file = _job_dir(job_id) / "work" / "plan.json"
    if not plan_file.exists():
        raise HTTPException(409, "no plan yet")
    plan = EditPlan.load(plan_file)
    for sp in patch.segments:
        if not 0 <= sp.index < len(plan.segments):
            raise HTTPException(400, f"bad segment index {sp.index}")
        seg = plan.segments[sp.index]
        if sp.speed is not None:
            seg.speed = max(0.5, min(float(sp.speed), 4.0))
        if sp.enabled is not None:
            seg.enabled = bool(sp.enabled)
        if sp.zoom is not None:
            seg.zoom = max(1.0, min(float(sp.zoom), 2.0))
    allowed = {"subtitle_style", "uppercase", "font_size", "margin_v", "max_chars", "normalize_audio", "fit", "subtitles", "font",
               "caption_mode", "word_case", "word_size", "word_y", "grade", "color_pops", "fade_out", "title_duration", "title_size", "title_y"}
    for k, v in patch.settings.items():
        if k in allowed:
            plan.settings[k] = v
    plan.save(plan_file)
    return {"ok": True, "stats": plan.stats()}


@app.post("/api/jobs/{job_id}/render")
def render_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] not in ("done", "error"):
        raise HTTPException(409, "job is still processing")
    _update(job_id, status="queued", stage="queued", progress=0.8, error=None)
    _executor.submit(_rerender, job_id)
    return _public(_jobs[job_id])


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str):
    job = _jobs.get(job_id)
    if not job or job.get("status") != "done":
        raise HTTPException(404, "output not ready")
    out = _job_dir(job_id) / "output.mp4"
    stem = Path(job.get("original_name") or "video").stem
    return FileResponse(out, media_type="video/mp4", filename=f"{stem}_autocut.mp4")


@app.get("/api/jobs/{job_id}/srt")
def download_srt(job_id: str):
    out = _job_dir(job_id) / "output.srt"
    if not out.exists():
        raise HTTPException(404, "srt not found")
    return FileResponse(out, media_type="text/plain", filename="legendas.srt")


@app.get("/api/jobs/{job_id}/preview")
def preview(job_id: str):
    out = _job_dir(job_id) / "output.mp4"
    if not out.exists():
        raise HTTPException(404, "output not ready")
    return FileResponse(out, media_type="video/mp4")


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    with _lock:
        job = _jobs.pop(job_id, None)
    if not job:
        raise HTTPException(404, "job not found")
    shutil.rmtree(_job_dir(job_id), ignore_errors=True)
    return {"ok": True}


@app.get("/api/health")
def health() -> dict:
    from autocut import ffbin

    try:
        ff = ffbin.ffmpeg()
    except Exception as exc:  # noqa: BLE001
        ff = f"ERROR: {exc}"
    return {"ok": True, "ffmpeg": ff, "jobs": len(_jobs)}


# --------------------------------------------------------------------------- minimal UI for local testing

_UI = ROOT / "app" / "frontend" / "index.html"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    if _UI.exists():
        return _UI.read_text(encoding="utf-8")
    return "<h1>AutoCut API</h1><p>POST /api/jobs</p>"
