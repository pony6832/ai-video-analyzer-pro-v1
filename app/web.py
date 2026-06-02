from __future__ import annotations

import mimetypes
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import API_KEY_PLACEHOLDER, ENV_FILE, PROJECT_ROOT, get_settings
from app.gem_profiles import load_profiles
from app.schemas import ReviewDecision
from app.transcript_workflow import TranscriptWorkflow
from app.video_utils import AUDIO_EXTENSIONS
from app.workbench import WorkbenchStore


app = FastAPI(title="AI Video News Analyzer Web")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
OUTPUT_EXTENSIONS = {".md", ".json"}
PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
PRO_WEBAPP_DIST = PROJECT_ROOT / "webapp" / "dist"
PRO_WEBAPP_ASSETS = PRO_WEBAPP_DIST / "assets"
KNOWN_FFMPEG_BIN = (
    Path.home()
    / "AppData"
    / "Local"
    / "Microsoft"
    / "WinGet"
    / "Packages"
    / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    / "ffmpeg-8.1.1-full_build"
    / "bin"
)

if PRO_WEBAPP_ASSETS.exists():
    app.mount(
        "/pro-workbench/assets",
        StaticFiles(directory=PRO_WEBAPP_ASSETS),
        name="pro-workbench-assets",
    )


class Job(BaseModel):
    id: str
    video_name: str
    status: Literal["queued", "running", "completed", "failed", "stopped"]
    started_at: str
    ended_at: str | None = None
    return_code: int | None = None
    command: list[str]
    log: list[str] = []


class ApiKeyUpdate(BaseModel):
    api_key: str


jobs: dict[str, Job] = {}
processes: dict[str, subprocess.Popen[bytes]] = {}
jobs_lock = threading.Lock()


def _settings():
    settings = get_settings()
    settings.ensure_directories()
    return settings


def _allowed_media_extensions() -> set[str]:
    return AUDIO_EXTENSIONS if _settings().app_audio_only else MEDIA_EXTENSIONS


def _media_kind_label() -> str:
    return "音訊檔" if _settings().app_audio_only else "影音檔"


def _allowed_media_hint() -> str:
    if _settings().app_audio_only:
        return "只支援音訊檔：mp3, wav, m4a, aac, flac, ogg, opus, wma。"
    return "只支援影音檔：mp4, mov, mkv, avi, m4v, webm, mp3, wav, m4a, aac, flac, ogg, opus, wma。"


def _is_allowed_media_path(path: Path) -> bool:
    return path.suffix.lower() in _allowed_media_extensions()


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    name = re.sub(r"[^\w.\-()\u4e00-\u9fff]+", "_", name, flags=re.UNICODE)
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="檔名不合法。")
    return name


def _resolve_output_file(filename: str) -> Path:
    safe_name = Path(filename).name
    path = (_settings().outputs_dir / safe_name).resolve()
    outputs_dir = _settings().outputs_dir.resolve()
    if outputs_dir not in path.parents and path != outputs_dir:
        raise HTTPException(status_code=400, detail="輸出檔路徑不合法。")
    if path.suffix.lower() not in OUTPUT_EXTENSIONS:
        raise HTTPException(status_code=400, detail="只允許讀取 Markdown 或 JSON 輸出。")
    if not path.exists():
        raise HTTPException(status_code=404, detail="找不到輸出檔。")
    return path


def _append_log(job_id: str, text: str) -> None:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return
        job.log.append(text)
        if len(job.log) > 2000:
            job.log = job.log[-2000:]


def _decode_process_output(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp950", "mbcs"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
        except LookupError:
            continue
    return data.decode("utf-8", errors="replace")


def _write_api_key(api_key: str) -> None:
    key = api_key.strip()
    if key.startswith("GEMINI_API_KEY="):
        key = key.split("=", 1)[1].strip()
    key = key.strip("\"'")
    if not key or key == API_KEY_PLACEHOLDER or "\n" in key or "\r" in key:
        raise HTTPException(status_code=400, detail="API Key 格式不正確。請貼上 Google AI Studio 產生的完整 Gemini API Key。")

    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    else:
        lines = [
            "# Gemini API key for AI 影音分析專業版",
            f"GEMINI_API_KEY={API_KEY_PLACEHOLDER}",
            "",
            "GEMINI_MODEL=gemini-2.5-flash",
            "GEMINI_FALLBACK_MODELS=gemini-2.0-flash",
        ]

    updated = False
    next_lines: list[str] = []
    for line in lines:
        if line.strip().startswith("GEMINI_API_KEY="):
            next_lines.append(f"GEMINI_API_KEY={key}")
            updated = True
        else:
            next_lines.append(line)
    if not updated:
        next_lines.insert(0, f"GEMINI_API_KEY={key}")

    ENV_FILE.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")
    os.environ["GEMINI_API_KEY"] = key
    get_settings.cache_clear()


def _clear_video_cache() -> dict[str, object]:
    videos_dir = _settings().videos_dir.resolve()
    removed = 0
    failed: list[str] = []
    for path in videos_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        if videos_dir not in path.resolve().parents:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            failed.append(f"{path.name}: {exc}")
    return {"removed": removed, "failed": failed}


def _extract_workbench_job_id(log_text: str) -> str | None:
    match = re.search(r"Job ID[:：]\s*([A-Za-z0-9_-]+)", log_text)
    return match.group(1) if match else None


def _log_has_completed_workbench_job(log_text: str) -> bool:
    workbench_job_id = _extract_workbench_job_id(log_text)
    if not workbench_job_id or "新版工作台分析完成" not in log_text:
        return False
    analysis_path = _settings().outputs_dir / "jobs" / workbench_job_id / "analysis.json"
    return analysis_path.exists()


def _run_job(job_id: str) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job.status = "running"
        command = job.command

    env = os.environ.copy()
    if KNOWN_FFMPEG_BIN.exists():
        env["PATH"] = str(KNOWN_FFMPEG_BIN) + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    try:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        with jobs_lock:
            processes[job_id] = process
        assert process.stdout is not None
        for line in process.stdout:
            _append_log(job_id, _decode_process_output(line))
        return_code = process.wait()
        with jobs_lock:
            job = jobs[job_id]
            job.return_code = return_code
            job.ended_at = datetime.now().astimezone().isoformat(timespec="seconds")
            if job.status == "stopped":
                return
            log_text = "".join(job.log)
            job.status = "completed" if return_code == 0 or _log_has_completed_workbench_job(log_text) else "failed"
    except Exception as exc:
        _append_log(job_id, f"啟動分析失敗：{exc}\n")
        with jobs_lock:
            job = jobs[job_id]
            job.status = "failed"
            job.ended_at = datetime.now().astimezone().isoformat(timespec="seconds")
    finally:
        with jobs_lock:
            processes.pop(job_id, None)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.get("/workbench", response_class=HTMLResponse)
def workbench_index() -> str:
    return WORKBENCH_HTML


@app.get("/goldmine", response_class=HTMLResponse)
def goldmine_index() -> str:
    return GOLDMINE_HTML


@app.get("/pro-workbench", response_class=HTMLResponse)
def pro_workbench_index() -> str:
    _clear_video_cache()
    index_path = PRO_WEBAPP_DIST / "index.html"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return PRO_WORKBENCH_FALLBACK_HTML


@app.get("/api/health")
def health() -> dict[str, object]:
    settings = _settings()
    api_key = settings.gemini_api_key.get_secret_value().strip() if settings.gemini_api_key else ""
    return {
        "ok": True,
        "project_root": str(PROJECT_ROOT),
        "env_file": str(ENV_FILE),
        "has_env": (PROJECT_ROOT / ".env").exists(),
        "has_api_key": bool(api_key and "your_gemini_api_key_here" not in api_key),
        "model": settings.gemini_model,
        "mode": "free_manual",
        "display_name": settings.app_display_name,
        "audio_only": settings.app_audio_only,
        "allowed_extensions": sorted(_allowed_media_extensions()),
    }


@app.post("/api/settings/api-key")
def update_api_key(payload: ApiKeyUpdate) -> dict[str, object]:
    _write_api_key(payload.api_key)
    return health()


@app.get("/api/profiles")
def profiles() -> list[dict[str, str]]:
    return [profile.model_dump(mode="json") for profile in load_profiles()]


@app.get("/api/videos")
def list_videos() -> list[dict[str, object]]:
    videos_dir = _settings().videos_dir
    items = []
    for path in sorted(videos_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if path.is_file() and _is_allowed_media_path(path):
            items.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "type": "audio" if path.suffix.lower() in AUDIO_EXTENSIONS else "video",
                    "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
                    "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                }
            )
    return items


@app.post("/api/videos/clear")
def clear_videos() -> dict[str, object]:
    return _clear_video_cache()


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)) -> dict[str, object]:
    safe_name = _safe_filename(file.filename or "uploaded.mp4")
    suffix = Path(safe_name).suffix.lower()
    if _settings().app_audio_only and (file.content_type or "").lower().startswith("video/"):
        raise HTTPException(status_code=400, detail=_allowed_media_hint())
    if suffix not in _allowed_media_extensions():
        raise HTTPException(status_code=400, detail=_allowed_media_hint())

    target = _settings().videos_dir / safe_name
    if target.exists():
        stem = target.stem
        target = target.with_name(f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{target.suffix}")

    with target.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            output.write(chunk)

    return {"name": target.name, "path": str(target), "size_mb": round(target.stat().st_size / (1024 * 1024), 2)}


@app.post("/api/analyze")
def analyze(
    background_tasks: BackgroundTasks,
    video_name: str = Form(...),
    chunk_minutes: int = Form(10),
    force: bool = Form(False),
    confirm_paid: bool = Form(False),
    profile_id: str = Form("news_editor"),
) -> dict[str, object]:
    if not confirm_paid:
        raise HTTPException(status_code=400, detail="請先確認付費 API 模式會消耗 Gemini API 額度。")
    if chunk_minutes <= 0:
        raise HTTPException(status_code=400, detail="切段分鐘必須大於 0。")
    safe_name = Path(video_name).name
    video_path = (_settings().videos_dir / safe_name).resolve()
    if not video_path.exists() or not _is_allowed_media_path(video_path):
        raise HTTPException(status_code=404, detail=f"找不到可用的{_media_kind_label()}。")

    python_exe = PYTHON_EXE if PYTHON_EXE.exists() else Path(os.sys.executable)
    command = [str(python_exe), "main.py"]
    if profile_id == "production_director":
        command.extend(
            [
                "analyze-production-video",
                str(video_path),
                "--chunk-minutes",
                str(chunk_minutes),
                "--profile-id",
                profile_id,
            ]
        )
    else:
        command.extend(["analyze", str(video_path), "--chunk-minutes", str(chunk_minutes)])
    if force:
        command.append("--force")

    job_id = uuid.uuid4().hex[:12]
    job = Job(
        id=job_id,
        video_name=safe_name,
        status="queued",
        started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        command=command,
    )
    with jobs_lock:
        jobs[job_id] = job
    background_tasks.add_task(_run_job, job_id)
    return {"job_id": job_id, "status": job.status}


@app.post("/api/manual-prepare")
def manual_prepare(
    background_tasks: BackgroundTasks,
    video_name: str = Form(...),
    chunk_minutes: int = Form(10),
    profile_id: str = Form("news_editor"),
) -> dict[str, object]:
    if chunk_minutes <= 0:
        raise HTTPException(status_code=400, detail="切段分鐘必須大於 0。")
    safe_name = Path(video_name).name
    video_path = (_settings().videos_dir / safe_name).resolve()
    if not video_path.exists() or not _is_allowed_media_path(video_path):
        raise HTTPException(status_code=404, detail=f"找不到可用的{_media_kind_label()}。")

    python_exe = PYTHON_EXE if PYTHON_EXE.exists() else Path(os.sys.executable)
    command = [
        str(python_exe),
        "main.py",
        "prepare-manual",
        str(video_path),
        "--chunk-minutes",
        str(chunk_minutes),
        "--profile-id",
        profile_id,
    ]

    job_id = uuid.uuid4().hex[:12]
    job = Job(
        id=job_id,
        video_name=safe_name,
        status="queued",
        started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        command=command,
    )
    with jobs_lock:
        jobs[job_id] = job
    background_tasks.add_task(_run_job, job_id)
    return {"job_id": job_id, "status": job.status}


@app.post("/api/transcript/manual-prepare")
def transcript_manual_prepare(
    transcript: str = Form(...),
    profile_id: str = Form("production_director"),
) -> dict[str, object]:
    if not transcript.strip():
        raise HTTPException(status_code=400, detail="逐字稿不可為空。")
    settings = _settings()
    prompt = TranscriptWorkflow(settings=settings).prepare_prompt_text(transcript, profile_id=profile_id)
    base_name = f"transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    path = settings.outputs_dir / f"{base_name}_prompt.md"
    path.write_text(
        "\n".join(["# Transcript Free Mode Prompt", "", "```text", prompt, "```", ""]),
        encoding="utf-8",
    )
    return {
        "name": path.name,
        "url": f"/outputs/{path.name}",
        "prompt": prompt,
    }


@app.post("/api/transcript/analyze")
def transcript_analyze(
    transcript: str = Form(...),
    profile_id: str = Form("production_director"),
    confirm_paid: bool = Form(False),
) -> dict[str, object]:
    if not confirm_paid:
        raise HTTPException(status_code=400, detail="請先確認付費 API 模式會消耗 Gemini API 額度。")
    if not transcript.strip():
        raise HTTPException(status_code=400, detail="逐字稿不可為空。")
    settings = _settings()
    api_key = settings.gemini_api_key.get_secret_value().strip() if settings.gemini_api_key else ""
    if not api_key or "your_gemini_api_key_here" in api_key:
        raise HTTPException(status_code=400, detail="找不到有效 GEMINI_API_KEY，請先設定 .env。")
    base_name = f"transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    outputs = TranscriptWorkflow(settings=settings).analyze_text_paid_outputs(
        transcript,
        profile_id=profile_id,
        base_name=base_name,
    )
    md_path = outputs["md_path"]
    json_path = outputs["json_path"]
    return {
        "report": outputs["report"].model_dump(mode="json"),
        "markdown_url": f"/outputs/{md_path.name}",
        "json_url": f"/outputs/{json_path.name}",
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> Job:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="找不到任務。")
        return job.model_copy(deep=True)


@app.post("/api/jobs/{job_id}/stop")
def stop_job(job_id: str) -> dict[str, object]:
    with jobs_lock:
        job = jobs.get(job_id)
        process = processes.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="找不到任務。")
        job.status = "stopped"
        job.ended_at = datetime.now().astimezone().isoformat(timespec="seconds")
    if process and process.poll() is None:
        process.terminate()
    return {"job_id": job_id, "status": "stopped"}


@app.get("/api/reports")
def list_reports() -> list[dict[str, object]]:
    outputs_dir = _settings().outputs_dir
    reports = []
    for path in sorted(outputs_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if path.is_file() and path.suffix.lower() in OUTPUT_EXTENSIONS:
            reports.append(
                {
                    "name": path.name,
                    "size_kb": round(path.stat().st_size / 1024, 1),
                    "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                    "url": f"/outputs/{path.name}",
                }
            )
    return reports


@app.get("/api/workbench/jobs")
def list_workbench_jobs() -> list[dict[str, object]]:
    return [job.model_dump(mode="json") for job in WorkbenchStore(_settings()).list_jobs()]


@app.post("/api/workbench/analyze")
def workbench_analyze(
    background_tasks: BackgroundTasks,
    video_name: str = Form(""),
    source_url: str = Form(""),
    chunk_minutes: int = Form(10),
    draft_clips: bool = Form(True),
    force: bool = Form(False),
    confirm_paid: bool = Form(False),
) -> dict[str, object]:
    if not confirm_paid:
        raise HTTPException(status_code=400, detail="請先確認工作台分析會消耗 Gemini API 額度。")
    if chunk_minutes <= 0:
        raise HTTPException(status_code=400, detail="切段分鐘必須大於 0。")
    if _settings().app_audio_only and source_url.strip():
        raise HTTPException(status_code=400, detail="純音檔版本只接受本機音訊檔案，不支援 URL 或影片來源。")
    source = source_url.strip()
    if not source:
        safe_name = Path(video_name).name
        video_path = (_settings().videos_dir / safe_name).resolve()
        if not video_path.exists() or not _is_allowed_media_path(video_path):
            raise HTTPException(status_code=404, detail=f"找不到可用的{_media_kind_label()}。")
        source = str(video_path)

    python_exe = PYTHON_EXE if PYTHON_EXE.exists() else Path(os.sys.executable)
    command = [
        str(python_exe),
        "main.py",
        "analyze-video",
        source,
        "--chunk-minutes",
        str(chunk_minutes),
    ]
    if not draft_clips:
        command.append("--no-draft-clips")
    if force:
        command.append("--force")

    job_id = uuid.uuid4().hex[:12]
    web_job = Job(
        id=job_id,
        video_name=Path(source).name if not source_url.strip() else source_url.strip(),
        status="queued",
        started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        command=command,
    )
    with jobs_lock:
        jobs[job_id] = web_job
    background_tasks.add_task(_run_job, job_id)
    return {"web_job_id": job_id, "status": web_job.status}


@app.get("/api/workbench/jobs/{job_id}")
def get_workbench_job(job_id: str) -> dict[str, object]:
    return WorkbenchStore(_settings()).load_job(job_id).model_dump(mode="json")


@app.get("/api/workbench/jobs/{job_id}/analysis")
def get_workbench_analysis(job_id: str) -> dict[str, object]:
    try:
        return WorkbenchStore(_settings()).load_analysis(job_id).model_dump(mode="json")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/workbench/jobs/{job_id}/clips/{clip_id}/review")
async def update_workbench_clip_review(job_id: str, clip_id: str, decision: ReviewDecision) -> dict[str, object]:
    analysis = WorkbenchStore(_settings()).update_clip_review(job_id, clip_id, decision)
    return analysis.model_dump(mode="json")


@app.post("/api/workbench/jobs/{job_id}/prepare-review")
def prepare_workbench_review(job_id: str) -> dict[str, object]:
    python_exe = PYTHON_EXE if PYTHON_EXE.exists() else Path(os.sys.executable)
    command = [str(python_exe), "main.py", "prepare-review", job_id]
    run_id = uuid.uuid4().hex[:12]
    web_job = Job(
        id=run_id,
        video_name=job_id,
        status="queued",
        started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        command=command,
    )
    with jobs_lock:
        jobs[run_id] = web_job
    threading.Thread(target=_run_job, args=(run_id,), daemon=True).start()
    return {"web_job_id": run_id, "status": web_job.status}


@app.post("/api/workbench/jobs/{job_id}/export-clips")
def export_workbench_clips(job_id: str) -> dict[str, object]:
    python_exe = PYTHON_EXE if PYTHON_EXE.exists() else Path(os.sys.executable)
    command = [str(python_exe), "main.py", "export-clips", job_id]
    run_id = uuid.uuid4().hex[:12]
    web_job = Job(
        id=run_id,
        video_name=job_id,
        status="queued",
        started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        command=command,
    )
    with jobs_lock:
        jobs[run_id] = web_job
    threading.Thread(target=_run_job, args=(run_id,), daemon=True).start()
    return {"web_job_id": run_id, "status": web_job.status}


@app.get("/api/pro-workbench/jobs")
def list_pro_workbench_jobs() -> list[dict[str, object]]:
    store = WorkbenchStore(_settings())
    return [_pro_job_payload(store, job.id) for job in store.list_jobs()]


@app.post("/api/pro-workbench/analyze")
def pro_workbench_analyze(
    background_tasks: BackgroundTasks,
    video_name: str = Form(""),
    chunk_minutes: int = Form(10),
    draft_clips: bool = Form(True),
    force: bool = Form(False),
    confirm_paid: bool = Form(False),
) -> dict[str, object]:
    return workbench_analyze(
        background_tasks=background_tasks,
        video_name=video_name,
        source_url="",
        chunk_minutes=chunk_minutes,
        draft_clips=draft_clips,
        force=force,
        confirm_paid=confirm_paid,
    )


@app.get("/api/pro-workbench/jobs/{job_id}")
def get_pro_workbench_job(job_id: str) -> dict[str, object]:
    store = WorkbenchStore(_settings())
    return _pro_job_payload(store, job_id)


@app.get("/api/pro-workbench/jobs/{job_id}/analysis")
def get_pro_workbench_analysis(job_id: str) -> dict[str, object]:
    try:
        return WorkbenchStore(_settings()).load_analysis(job_id).model_dump(mode="json")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/pro-workbench/jobs/{job_id}/clips/{clip_id}")
async def update_pro_workbench_clip(job_id: str, clip_id: str, decision: ReviewDecision) -> dict[str, object]:
    analysis = WorkbenchStore(_settings()).update_clip_review(job_id, clip_id, decision)
    return analysis.model_dump(mode="json")


@app.post("/api/pro-workbench/jobs/{job_id}/export")
def export_pro_workbench(job_id: str, draft_clips: bool = Form(True)) -> dict[str, object]:
    python_exe = PYTHON_EXE if PYTHON_EXE.exists() else Path(os.sys.executable)
    command = [str(python_exe), "main.py", "export-clips" if draft_clips else "prepare-review", job_id]
    run_id = uuid.uuid4().hex[:12]
    web_job = Job(
        id=run_id,
        video_name=job_id,
        status="queued",
        started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        command=command,
    )
    with jobs_lock:
        jobs[run_id] = web_job
    threading.Thread(target=_run_job, args=(run_id,), daemon=True).start()
    return {"web_job_id": run_id, "status": web_job.status}


def _pro_job_payload(store: WorkbenchStore, job_id: str) -> dict[str, object]:
    job = store.load_job(job_id)
    payload = job.model_dump(mode="json")
    try:
        analysis = store.load_analysis(job_id)
    except FileNotFoundError:
        payload["has_analysis"] = False
        if job.status == "failed" and job.error:
            payload["analysis_error"] = job.error
        return payload
    payload["has_analysis"] = True
    has_no_results = (
        not analysis.transcript_segments
        and not analysis.timeline_events
        and not analysis.quote_candidates
        and not analysis.clip_candidates
        and bool(analysis.risk_notes)
    )
    if has_no_results:
        payload["status"] = "failed"
        payload["error"] = "所有片段皆分析失敗，沒有可用的 AI 分析結果。"
    return payload


@app.get("/workbench/outputs/{job_id}/{filename:path}")
def get_workbench_output(job_id: str, filename: str):
    base = (WorkbenchStore(_settings()).output_dir(job_id)).resolve()
    path = (base / filename).resolve()
    if base not in path.parents and path != base:
        raise HTTPException(status_code=400, detail="輸出檔路徑不合法。")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="找不到輸出檔。")
    media_type = mimetypes.guess_type(path.name)[0]
    if path.suffix.lower() == ".md":
        return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")
    return FileResponse(path, media_type=media_type or "application/octet-stream", filename=path.name)


@app.get("/outputs/{filename}")
def get_output(filename: str):
    path = _resolve_output_file(filename)
    media_type = mimetypes.guess_type(path.name)[0]
    if path.suffix.lower() == ".md":
        return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")
    return FileResponse(path, media_type=media_type or "application/json", filename=path.name)


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(FileNotFoundError)
async def file_not_found_handler(_, exc: FileNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


PRO_WORKBENCH_FALLBACK_HTML = r"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI 影音分析專業版 V1</title>
  <style>
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #f3f6f8;
      color: #17202a;
      font-family: "Segoe UI", "Noto Sans TC", system-ui, sans-serif;
    }
    main {
      width: min(760px, calc(100vw - 32px));
      border: 1px solid #d6dde7;
      border-radius: 8px;
      background: #fff;
      padding: 28px;
      box-shadow: 0 18px 50px rgba(15, 23, 42, .08);
    }
    h1 { margin: 0 0 10px; font-size: 24px; letter-spacing: 0; }
    p { line-height: 1.7; color: #526071; }
    code {
      display: block;
      margin: 14px 0;
      padding: 12px;
      border-radius: 6px;
      background: #111827;
      color: #e5e7eb;
      overflow-x: auto;
    }
    a { color: #0f766e; font-weight: 700; }
  </style>
</head>
<body>
  <main>
    <h1>AI 影音分析專業版 V1</h1>
    <p>後端 Pro API 已啟用，但尚未找到 Vite build 輸出。請在專案根目錄執行：</p>
    <code>cd webapp<br />npm install<br />npm run build</code>
    <p>完成後重新整理本頁。你也可以先使用 <a href="/workbench">舊版工作台</a> 或直接呼叫 <code>/api/pro-workbench/jobs</code> 檢查 API。</p>
  </main>
</body>
</html>
"""


GOLDMINE_HTML = r"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>長影片淘金工作台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fa;
      --panel: #ffffff;
      --text: #111827;
      --muted: #64748b;
      --line: #d7dde7;
      --accent: #0f766e;
      --accent-dark: #115e59;
      --blue: #1d4ed8;
      --amber: #b45309;
      --danger: #b42318;
      --ok: #027a48;
      --ink: #0f172a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-width: 320px;
      background: var(--bg);
      color: var(--text);
      font-family: "Segoe UI", "Noto Sans TC", system-ui, sans-serif;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 58px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 { margin: 0; font-size: 21px; line-height: 1.2; letter-spacing: 0; }
    h2 { margin: 0 0 10px; font-size: 15px; letter-spacing: 0; }
    h3 { margin: 0 0 6px; font-size: 15px; line-height: 1.35; letter-spacing: 0; }
    a { color: var(--accent); text-decoration: none; }
    main {
      display: grid;
      grid-template-columns: 320px minmax(420px, 1fr) 430px;
      gap: 12px;
      height: calc(100vh - 58px);
      padding: 12px;
    }
    section {
      overflow: auto;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }
    label {
      display: block;
      margin: 10px 0 5px;
      color: #344054;
      font-size: 13px;
    }
    input, select, button {
      width: 100%;
      min-height: 36px;
      border-radius: 6px;
      font: inherit;
    }
    input, select {
      border: 1px solid #cbd5e1;
      padding: 7px 9px;
      background: #fff;
      color: var(--text);
    }
    button {
      border: 1px solid transparent;
      padding: 7px 10px;
      background: var(--accent);
      color: #fff;
      font-weight: 650;
      cursor: pointer;
    }
    button:hover { background: var(--accent-dark); }
    button.secondary {
      border-color: #cbd5e1;
      background: #fff;
      color: #334155;
    }
    button.secondary:hover { background: #f8fafc; }
    button.danger {
      border-color: #f1b8b2;
      background: #fff;
      color: var(--danger);
    }
    button.danger:hover { background: #fff5f4; }
    button:disabled {
      cursor: not-allowed;
      opacity: .55;
    }
    video {
      display: block;
      width: 100%;
      max-height: 48vh;
      border-radius: 8px;
      background: var(--ink);
    }
    .row {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .row > * { flex: 1 1 auto; }
    .muted { color: var(--muted); font-size: 13px; }
    .small { font-size: 12px; }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 8px;
      border-radius: 999px;
      background: #eef4ff;
      color: var(--blue);
      font-size: 12px;
      font-weight: 700;
    }
    .pill.ok { background: #ecfdf3; color: var(--ok); }
    .pill.warn { background: #fff7ed; color: var(--amber); }
    .pill.bad { background: #fef3f2; color: var(--danger); }
    .list { display: grid; gap: 8px; }
    .item {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fff;
    }
    .job { cursor: pointer; }
    .job:hover, .job.active { border-color: var(--accent); }
    .score {
      display: inline-grid;
      place-items: center;
      width: 42px;
      height: 30px;
      border-radius: 6px;
      background: #ecfeff;
      color: #155e75;
      font-weight: 800;
      font-variant-numeric: tabular-nums;
    }
    .tabs {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 6px;
      margin-bottom: 10px;
    }
    .tabs button {
      min-width: 0;
      padding: 7px 6px;
      border-color: #cbd5e1;
      background: #fff;
      color: #334155;
      white-space: nowrap;
    }
    .tabs button.active {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }
    .summary {
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid var(--line);
      white-space: pre-wrap;
      line-height: 1.65;
    }
    .timeline {
      display: flex;
      gap: 6px;
      min-height: 42px;
      margin-top: 10px;
      padding: 8px;
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
    }
    .tick {
      flex: 0 0 92px;
      min-height: 28px;
      border: 1px solid #bae6fd;
      border-radius: 6px;
      background: #eff6ff;
      color: #1e3a8a;
      font-size: 12px;
      font-weight: 700;
    }
    .log {
      min-height: 100px;
      max-height: 180px;
      overflow: auto;
      border-radius: 6px;
      padding: 10px;
      background: #111827;
      color: #e5e7eb;
      white-space: pre-wrap;
      font: 12px/1.45 Consolas, "Cascadia Mono", monospace;
    }
    .actions {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 6px;
      margin-top: 8px;
    }
    .actions button { padding: 6px 5px; font-size: 13px; }
    .risk {
      margin-top: 7px;
      color: var(--danger);
      font-size: 13px;
      line-height: 1.45;
    }
    @media (max-width: 1200px) {
      main { grid-template-columns: 300px minmax(0, 1fr); height: auto; }
      section:last-child { grid-column: 1 / -1; }
    }
    @media (max-width: 780px) {
      header { align-items: flex-start; flex-direction: column; }
      main { grid-template-columns: 1fr; padding: 8px; }
      .tabs { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .actions { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>長影片淘金工作台</h1>
      <div class="muted">摘要、精彩片段、金句、社群標題與剪輯包</div>
    </div>
    <div class="row" style="justify-content:flex-end">
      <span id="health" class="pill warn">檢查中</span>
      <a href="/workbench" class="muted">舊工作台</a>
    </div>
  </header>
  <main>
    <section>
      <h2>影片導入</h2>
      <label for="upload">上傳影片</label>
      <input id="upload" type="file" accept="video/*" />
      <label for="videoSelect">本機影片</label>
      <select id="videoSelect"></select>
      <label for="sourceUrl">YouTube / 直播連結</label>
      <input id="sourceUrl" placeholder="https://..." />
      <label for="chunkMinutes">切段分鐘</label>
      <input id="chunkMinutes" type="number" min="1" value="10" />
      <div class="row" style="margin-top:10px">
        <button id="startBtn">開始淘金</button>
        <button class="secondary" id="refreshBtn">重新整理</button>
      </div>
      <div class="log" id="runLog" style="margin-top:10px">等待任務...</div>
      <h2 style="margin-top:14px">任務</h2>
      <div class="list" id="jobs"></div>
    </section>

    <section>
      <div class="row" style="justify-content:space-between">
        <div>
          <h2 id="currentTitle">尚未選擇任務</h2>
          <div class="muted" id="currentMeta">選擇左側任務後顯示影片與淘金結果</div>
        </div>
        <span class="pill" id="currentStatus">idle</span>
      </div>
      <video id="player" controls></video>
      <div class="timeline" id="timeline"></div>
      <div class="row" style="margin-top:10px">
        <button class="secondary" id="openReportBtn">完整報告</button>
        <button class="secondary" id="prepareReviewBtn">重建剪輯包</button>
        <button id="exportClipsBtn">輸出短片草稿</button>
      </div>
      <div class="summary" id="summary">尚無摘要。</div>
    </section>

    <section>
      <div class="tabs">
        <button class="active" data-tab="clips">片段</button>
        <button data-tab="quotes">金句</button>
        <button data-tab="titles">標題</button>
        <button data-tab="risks">風險</button>
      </div>
      <div id="panel"></div>
    </section>
  </main>

  <script>
    const state = { jobs: [], selectedJobId: null, analysis: null, tab: "clips", webJobId: null, poll: null, hasApiKey: false };
    const $ = (id) => document.getElementById(id);

    async function api(url, options = {}) {
      const res = await fetch(url, options);
      const contentType = res.headers.get("content-type") || "";
      const body = contentType.includes("application/json") ? await res.json() : await res.text();
      if (!res.ok) throw new Error(body.detail || body || `HTTP ${res.status}`);
      return body;
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
      })[ch]);
    }

    function seconds(value) {
      const parts = String(value || "0").split(":").map(Number);
      if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
      if (parts.length === 2) return parts[0] * 60 + parts[1];
      return Number(value) || 0;
    }

    function statusClass(status) {
      if (["exported", "ready_for_review", "completed", "kept", "revised"].includes(status)) return "pill ok";
      if (["failed", "rejected"].includes(status)) return "pill bad";
      if (["analyzing", "running", "queued", "imported", "preprocessed"].includes(status)) return "pill warn";
      return "pill";
    }

    async function loadHealth() {
      const health = await api("/api/health");
      state.hasApiKey = Boolean(health.has_api_key);
      $("health").className = state.hasApiKey ? "pill ok" : "pill warn";
      $("health").textContent = state.hasApiKey ? `API ${health.model}` : "未設定 API Key";
      $("startBtn").disabled = !state.hasApiKey;
    }

    async function uploadVideo() {
      const file = $("upload").files[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      const result = await api("/api/upload", { method: "POST", body: form });
      await loadVideos();
      $("videoSelect").value = result.name;
    }

    async function loadVideos() {
      const videos = await api("/api/videos");
      $("videoSelect").innerHTML = videos.length
        ? videos.map(v => `<option value="${escapeHtml(v.name)}">${escapeHtml(v.name)} (${v.size_mb} MB)</option>`).join("")
        : `<option value="">尚無影片</option>`;
    }

    async function loadJobs() {
      state.jobs = await api("/api/workbench/jobs");
      $("jobs").innerHTML = state.jobs.length ? state.jobs.map(job => `
        <div class="item job ${job.id === state.selectedJobId ? "active" : ""}" data-job="${escapeHtml(job.id)}">
          <div class="row" style="justify-content:space-between">
            <strong>${escapeHtml(job.title)}</strong>
            <span class="${statusClass(job.status)}">${escapeHtml(job.status)}</span>
          </div>
          <div class="muted small">${escapeHtml(job.id)}</div>
          <div class="muted small">${escapeHtml(job.updated_at)}</div>
        </div>
      `).join("") : `<div class="muted">目前沒有任務。</div>`;
      document.querySelectorAll(".job").forEach(el => {
        el.addEventListener("click", () => selectJob(el.dataset.job));
      });
    }

    async function selectJob(jobId) {
      state.selectedJobId = jobId;
      await loadJobs();
      const job = await api(`/api/workbench/jobs/${jobId}`);
      $("currentTitle").textContent = job.title;
      $("currentMeta").textContent = `${job.id}｜${job.updated_at}`;
      $("currentStatus").textContent = job.status;
      $("currentStatus").className = statusClass(job.status);
      $("player").src = `/workbench/outputs/${jobId}/proxy.mp4`;
      try {
        state.analysis = await api(`/api/workbench/jobs/${jobId}/analysis`);
        $("summary").textContent = state.analysis.overall_summary || "沒有摘要。";
      } catch (err) {
        state.analysis = null;
        $("summary").textContent = "這個任務尚未產生分析結果。";
      }
      renderTimeline();
      renderPanel();
    }

    function renderTimeline() {
      const source = state.analysis?.chapter_map?.length ? state.analysis.chapter_map : state.analysis?.timeline_events || [];
      $("timeline").innerHTML = source.length ? source.map(item => `
        <button class="tick" data-jump="${escapeHtml(item.start_time)}" title="${escapeHtml(item.title || item.summary)}">
          ${escapeHtml(item.start_time)}
        </button>
      `).join("") : `<span class="muted">尚無時間軸。</span>`;
      document.querySelectorAll("[data-jump]").forEach(btn => {
        btn.addEventListener("click", () => jump(btn.dataset.jump));
      });
    }

    function jump(timecode) {
      $("player").currentTime = seconds(timecode);
      $("player").play().catch(() => {});
    }

    function orderedClips() {
      const clips = state.analysis?.clip_candidates || [];
      const ranks = state.analysis?.highlight_rankings || [];
      if (!ranks.length) return [...clips].sort((a, b) => (b.score || 0) - (a.score || 0));
      const byId = new Map(clips.map(clip => [clip.id, clip]));
      const ranked = ranks.map(rank => byId.get(rank.clip_id)).filter(Boolean);
      const rankedIds = new Set(ranked.map(clip => clip.id));
      return [...ranked, ...clips.filter(clip => !rankedIds.has(clip.id))];
    }

    function renderPanel() {
      const panel = $("panel");
      if (!state.analysis) {
        panel.innerHTML = `<div class="muted">尚無淘金資料。</div>`;
        return;
      }
      if (state.tab === "clips") renderClips(panel);
      if (state.tab === "quotes") renderQuotes(panel);
      if (state.tab === "titles") renderTitles(panel);
      if (state.tab === "risks") renderRisks(panel);
    }

    function renderClips(panel) {
      const clips = orderedClips();
      panel.innerHTML = clips.length ? clips.map(clip => {
        const reviewStatus = clip.review_status || clip.review?.status || "pending";
        const risks = (clip.risk_notes || []).join("；");
        return `
          <div class="item">
            <div class="row" style="justify-content:space-between">
              <span class="score">${clip.score || 0}</span>
              <span class="${statusClass(reviewStatus)}">${escapeHtml(reviewStatus)}</span>
            </div>
            <h3>${escapeHtml(clip.review?.updated_title || clip.suggested_title)}</h3>
            <div class="muted">${escapeHtml(clip.start_time)} - ${escapeHtml(clip.end_time)}｜${escapeHtml(clip.platform)}</div>
            <div>${escapeHtml(clip.hook || clip.reason)}</div>
            <div class="muted">封面字：${escapeHtml(clip.cover_text || "")}</div>
            ${risks ? `<div class="risk">${escapeHtml(risks)}</div>` : ""}
            <div class="actions">
              <button class="secondary" data-jump="${escapeHtml(clip.start_time)}">跳轉</button>
              <button data-review="${escapeHtml(clip.id)}" data-status="kept">保留</button>
              <button class="secondary" data-revise="${escapeHtml(clip.id)}">修訂</button>
              <button class="danger" data-review="${escapeHtml(clip.id)}" data-status="rejected">刪除</button>
            </div>
          </div>
        `;
      }).join("") : `<div class="muted">沒有短影音候選。</div>`;
      bindPanelActions();
    }

    function renderQuotes(panel) {
      const quotes = state.analysis.quote_candidates || [];
      panel.innerHTML = quotes.length ? quotes.map(quote => `
        <div class="item">
          <h3>${escapeHtml(quote.quote)}</h3>
          <div class="muted">${escapeHtml(quote.timecode)}｜${escapeHtml(quote.speaker)}｜${escapeHtml(quote.emotion_tone)}</div>
          <div>${escapeHtml(quote.usage)}</div>
          <div class="actions" style="grid-template-columns:1fr">
            <button class="secondary" data-jump="${escapeHtml(quote.timecode)}">跳轉</button>
          </div>
        </div>
      `).join("") : `<div class="muted">沒有金句。</div>`;
      bindPanelActions();
    }

    function renderTitles(panel) {
      const titles = state.analysis.social_title_pack || [];
      panel.innerHTML = titles.length ? titles.map(item => `
        <div class="item">
          <h3>${escapeHtml(item.title)}</h3>
          <div class="muted">${escapeHtml(item.platform)}｜${escapeHtml(item.related_clip_id || "")}</div>
          <div>${escapeHtml(item.angle || "")}</div>
        </div>
      `).join("") : `<div class="muted">沒有社群標題包。</div>`;
    }

    function renderRisks(panel) {
      const risks = state.analysis.risk_notes || [];
      panel.innerHTML = risks.length ? risks.map(note => `
        <div class="item">
          <div class="risk">${escapeHtml(note)}</div>
        </div>
      `).join("") : `<div class="muted">目前沒有風險提醒。</div>`;
    }

    function bindPanelActions() {
      document.querySelectorAll("[data-jump]").forEach(btn => {
        btn.addEventListener("click", () => jump(btn.dataset.jump));
      });
      document.querySelectorAll("[data-review]").forEach(btn => {
        btn.addEventListener("click", () => reviewClip(btn.dataset.review, btn.dataset.status));
      });
      document.querySelectorAll("[data-revise]").forEach(btn => {
        btn.addEventListener("click", () => reviseClip(btn.dataset.revise));
      });
    }

    async function reviewClip(clipId, status, extra = {}) {
      if (!state.selectedJobId) return;
      await api(`/api/workbench/jobs/${state.selectedJobId}/clips/${clipId}/review`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, ...extra })
      });
      await selectJob(state.selectedJobId);
    }

    async function reviseClip(clipId) {
      const clip = (state.analysis?.clip_candidates || []).find(item => item.id === clipId);
      if (!clip) return;
      const title = prompt("新的短影音標題", clip.review?.updated_title || clip.suggested_title);
      if (!title) return;
      const hook = prompt("新的 Hook", clip.review?.updated_hook || clip.hook || "") || "";
      const start = prompt("新的開始時間 HH:MM:SS", clip.review?.updated_start_time || clip.start_time) || clip.start_time;
      const end = prompt("新的結束時間 HH:MM:SS", clip.review?.updated_end_time || clip.end_time) || clip.end_time;
      await reviewClip(clipId, "revised", {
        updated_title: title,
        updated_hook: hook,
        updated_start_time: start,
        updated_end_time: end
      });
    }

    async function startAnalyze() {
      if (!state.hasApiKey) {
        alert("請先設定 GEMINI_API_KEY。");
        return;
      }
      const form = new FormData();
      form.append("video_name", $("videoSelect").value || "");
      form.append("source_url", $("sourceUrl").value.trim());
      form.append("chunk_minutes", $("chunkMinutes").value);
      form.append("draft_clips", "false");
      form.append("confirm_paid", "true");
      if (!confirm("淘金分析會呼叫 Gemini API 並可能產生成本。確定要繼續？")) return;
      const result = await api("/api/workbench/analyze", { method: "POST", body: form });
      state.webJobId = result.web_job_id;
      $("runLog").textContent = "任務已送出...\n";
      if (state.poll) clearInterval(state.poll);
      state.poll = setInterval(pollRun, 1000);
      await pollRun();
    }

    async function pollRun() {
      if (!state.webJobId) return;
      const job = await api(`/api/jobs/${state.webJobId}`);
      $("runLog").textContent = job.log.join("");
      $("runLog").scrollTop = $("runLog").scrollHeight;
      if (["completed", "failed", "stopped"].includes(job.status)) {
        clearInterval(state.poll);
        state.poll = null;
        await loadJobs();
      }
    }

    async function runJobAction(action) {
      if (!state.selectedJobId) return;
      const result = await api(`/api/workbench/jobs/${state.selectedJobId}/${action}`, { method: "POST" });
      state.webJobId = result.web_job_id;
      $("runLog").textContent = "任務已送出...\n";
      if (state.poll) clearInterval(state.poll);
      state.poll = setInterval(pollRun, 1000);
      await pollRun();
    }

    document.querySelectorAll(".tabs button").forEach(btn => {
      btn.addEventListener("click", () => {
        state.tab = btn.dataset.tab;
        document.querySelectorAll(".tabs button").forEach(item => item.classList.toggle("active", item === btn));
        renderPanel();
      });
    });
    $("upload").addEventListener("change", () => uploadVideo().catch(err => alert(err.message)));
    $("startBtn").addEventListener("click", () => startAnalyze().catch(err => alert(err.message)));
    $("refreshBtn").addEventListener("click", () => Promise.all([loadVideos(), loadJobs()]).catch(err => alert(err.message)));
    $("prepareReviewBtn").addEventListener("click", () => runJobAction("prepare-review").catch(err => alert(err.message)));
    $("exportClipsBtn").addEventListener("click", () => runJobAction("export-clips").catch(err => alert(err.message)));
    $("openReportBtn").addEventListener("click", () => {
      if (state.selectedJobId) window.open(`/workbench/outputs/${state.selectedJobId}/full_report.md`, "_blank");
    });

    Promise.all([loadHealth(), loadVideos(), loadJobs()]).catch(err => {
      $("health").className = "pill bad";
      $("health").textContent = err.message;
    });
  </script>
</body>
</html>
"""


INDEX_HTML = r"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Video News Analyzer</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #667085;
      --line: #d9dee7;
      --primary: #0f766e;
      --primary-dark: #115e59;
      --danger: #b42318;
      --warn: #b54708;
      --ok: #027a48;
      --code: #101828;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "Noto Sans TC", system-ui, sans-serif;
      color: var(--text);
      background: var(--bg);
    }
    header {
      border-bottom: 1px solid var(--line);
      background: #ffffff;
      padding: 18px 24px;
    }
    h1 {
      margin: 0;
      font-size: 24px;
      line-height: 1.2;
      letter-spacing: 0;
    }
    .sub {
      margin-top: 6px;
      color: var(--muted);
      font-size: 14px;
    }
    main {
      display: grid;
      grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
      gap: 16px;
      padding: 16px;
      max-width: 1360px;
      margin: 0 auto;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    h2 {
      margin: 0 0 12px;
      font-size: 16px;
      line-height: 1.3;
    }
    label {
      display: block;
      font-size: 13px;
      color: #344054;
      margin: 12px 0 6px;
    }
    input[type="file"], select, input[type="number"], textarea {
      width: 100%;
      min-height: 38px;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      padding: 8px 10px;
      background: #fff;
      color: var(--text);
    }
    textarea {
      min-height: 180px;
      resize: vertical;
      line-height: 1.5;
      font-family: "Segoe UI", "Noto Sans TC", system-ui, sans-serif;
    }
    input[type="checkbox"] {
      width: 16px;
      height: 16px;
      vertical-align: middle;
    }
    button {
      min-height: 38px;
      border: 1px solid transparent;
      border-radius: 6px;
      padding: 8px 12px;
      background: var(--primary);
      color: #fff;
      cursor: pointer;
      font-weight: 600;
    }
    button:hover { background: var(--primary-dark); }
    button.secondary {
      background: #fff;
      color: #344054;
      border-color: #cbd5e1;
    }
    button.secondary:hover { background: #f8fafc; }
    button.danger {
      background: #fff;
      color: var(--danger);
      border-color: #f1b8b2;
    }
    button.danger:hover { background: #fff5f4; }
    .row {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    .row > button { flex: 1 1 auto; }
    .muted {
      color: var(--muted);
      font-size: 13px;
    }
    .status {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 13px;
      background: #eef4ff;
      color: #3538cd;
    }
    .status.completed { background: #ecfdf3; color: var(--ok); }
    .status.failed, .status.stopped { background: #fef3f2; color: var(--danger); }
    .status.running { background: #fff7ed; color: var(--warn); }
    .list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }
    .item {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #fff;
      display: grid;
      gap: 4px;
    }
    .item a { color: var(--primary-dark); text-decoration: none; font-weight: 600; }
    .item a:hover { text-decoration: underline; }
    .workspace {
      display: grid;
      grid-template-rows: auto minmax(320px, 1fr);
      gap: 16px;
      min-height: calc(100vh - 116px);
    }
    .log {
      min-height: 280px;
      max-height: 52vh;
      overflow: auto;
      border-radius: 8px;
      background: var(--code);
      color: #e4e7ec;
      padding: 14px;
      font: 13px/1.5 Consolas, "Cascadia Mono", monospace;
      white-space: pre-wrap;
    }
    .report-preview {
      min-height: 360px;
      max-height: 68vh;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: #fcfcfd;
      white-space: pre-wrap;
      line-height: 1.65;
    }
    .progress {
      width: 100%;
      height: 10px;
      appearance: none;
      border: 0;
      border-radius: 999px;
      overflow: hidden;
      background: #eaecf0;
      display: none;
      margin-top: 8px;
    }
    .progress::-webkit-progress-bar { background: #eaecf0; }
    .progress::-webkit-progress-value { background: var(--primary); }
    .progress::-moz-progress-bar { background: var(--primary); }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      .workspace { min-height: auto; }
    }
  </style>
</head>
<body>
  <header>
    <h1>AI Video News Analyzer</h1>
    <div class="sub">長影片新聞分析控制台</div>
  </header>
  <main>
    <section>
      <h2>影片與分析設定</h2>
      <div class="muted" id="health">檢查環境中...</div>

      <label for="sourceType">輸入類型</label>
      <select id="sourceType">
        <option value="video" selected>影片片段</option>
        <option value="transcript">訪談逐字稿</option>
      </select>

      <label for="runMode">執行模式</label>
      <select id="runMode">
        <option value="free" selected>免費模式：產生 Gemini Web/Gem 提示詞</option>
        <option value="paid">付費 API 模式：直接呼叫 Gemini API</option>
      </select>
      <div class="muted" id="modeNotice">免費模式不調用 API，不消耗 Gemini API 額度。</div>

      <div id="videoPanel">
        <label for="upload">上傳影片</label>
        <input id="upload" type="file" accept="video/*" />
        <div class="muted" id="uploadStatus">選擇影片後會自動匯入到 videos。</div>
        <progress class="progress" id="uploadProgress" value="0" max="100"></progress>
        <div class="row" style="margin-top:8px">
          <button class="secondary" id="uploadBtn">上傳到 videos</button>
          <button class="secondary" id="refreshBtn">重新整理</button>
        </div>

        <label for="videoSelect">選擇影片</label>
        <select id="videoSelect"></select>
      </div>

      <div id="transcriptPanel" style="display:none">
        <label for="transcriptText">貼上訪談逐字稿</label>
        <textarea id="transcriptText" placeholder="可貼含 timecode 或不含 timecode 的訪談逐字稿。"></textarea>
      </div>

      <label for="chunkMinutes">切段分鐘</label>
      <input id="chunkMinutes" type="number" min="1" max="120" value="10" />

      <input id="profileSelect" type="hidden" value="production_director" />
      <div class="muted" style="margin-top:10px">分析模板：影視後製導演與內容企劃</div>

      <div class="row" style="margin-top:14px">
        <button id="startBtn">產生免費版提示詞</button>
        <button class="danger" id="stopBtn" disabled>停止</button>
      </div>

      <div class="muted" id="videoList" style="margin-top:14px"></div>
    </section>

    <div class="workspace">
      <section>
        <div class="row" style="justify-content:space-between">
          <h2 style="margin:0">任務狀態</h2>
          <span class="status" id="jobStatus">尚未開始</span>
        </div>
        <div class="log" id="log">等待分析任務...</div>
      </section>

      <section>
        <div class="row" style="justify-content:space-between">
          <h2 style="margin:0">結果區</h2>
          <button class="secondary" id="copyResultBtn">複製結果</button>
        </div>
        <div class="report-preview" id="preview">產生提示詞或完成分析後，結果會顯示在這裡。</div>
      </section>
    </div>
  </main>

  <script>
    const state = { jobId: null, pollTimer: null, currentPreviewText: "", hasApiKey: false };
    const $ = (id) => document.getElementById(id);

    async function api(url, options = {}) {
      const res = await fetch(url, options);
      const contentType = res.headers.get("content-type") || "";
      const body = contentType.includes("application/json") ? await res.json() : await res.text();
      if (!res.ok) throw new Error(body.detail || body || `HTTP ${res.status}`);
      return body;
    }

    function setStatus(text, status = "") {
      const el = $("jobStatus");
      el.textContent = text;
      el.className = `status ${status}`;
    }

    async function loadHealth() {
      const health = await api("/api/health");
      state.hasApiKey = Boolean(health.has_api_key);
      $("health").textContent = `${state.hasApiKey ? "API Key 已設定" : "API Key 未設定"}｜目前預設免費模式`;
      updateModeUi();
    }

    async function loadVideos() {
      const videos = await api("/api/videos");
      const select = $("videoSelect");
      select.innerHTML = "";
      if (!videos.length) {
        select.innerHTML = `<option value="">尚無影片</option>`;
      } else {
        for (const video of videos) {
          const option = document.createElement("option");
          option.value = video.name;
          option.textContent = `${video.name} (${video.size_mb} MB)`;
          select.appendChild(option);
        }
      }
      $("videoList").textContent = videos.length ? `已匯入影片：${videos.length} 支` : "尚無影片。";
    }

    async function previewReport(url) {
      const text = await api(url);
      state.currentPreviewText = text;
      $("preview").textContent = text;
    }

    async function uploadVideo() {
      const input = $("upload");
      if (!input.files.length) {
        alert("請先選擇影片。");
        return;
      }
      const form = new FormData();
      form.append("file", input.files[0]);
      $("uploadBtn").disabled = true;
      $("uploadStatus").textContent = `匯入中：${input.files[0].name}`;
      $("uploadProgress").style.display = "block";
      $("uploadProgress").value = 0;
      try {
        const result = await uploadWithProgress(form);
        await loadVideos();
        $("videoSelect").value = result.name;
        $("uploadStatus").textContent = `已匯入：${result.name}`;
      } catch (err) {
        $("uploadStatus").textContent = `匯入失敗：${err.message}`;
        alert(err.message);
      } finally {
        $("uploadBtn").disabled = false;
        setTimeout(() => {
          $("uploadProgress").style.display = "none";
          $("uploadProgress").value = 0;
        }, 1200);
      }
    }

    function uploadWithProgress(form) {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/upload");
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            const percent = Math.round((event.loaded / event.total) * 100);
            $("uploadProgress").value = percent;
            $("uploadStatus").textContent = `匯入中：${percent}%`;
          } else {
            $("uploadStatus").textContent = "匯入中...";
          }
        };
        xhr.onload = () => {
          try {
            const body = JSON.parse(xhr.responseText || "{}");
            if (xhr.status >= 200 && xhr.status < 300) {
              $("uploadProgress").value = 100;
              resolve(body);
            } else {
              reject(new Error(body.detail || `HTTP ${xhr.status}`));
            }
          } catch (err) {
            reject(err);
          }
        };
        xhr.onerror = () => reject(new Error("上傳連線失敗。"));
        xhr.send(form);
      });
    }

    async function startAnalyze() {
      const sourceType = $("sourceType").value;
      const runMode = $("runMode").value;
      const form = new FormData();
      form.append("profile_id", $("profileSelect").value || "news_editor");
      let result;
      if (sourceType === "transcript") {
        const transcript = $("transcriptText").value.trim();
        if (!transcript) {
          alert("請先貼上逐字稿。");
          return;
        }
        form.append("transcript", transcript);
        if (runMode === "paid") {
          if (!state.hasApiKey) {
            alert("付費 API 模式需要有效 GEMINI_API_KEY。");
            return;
          }
          if (!confirm("付費 API 模式會消耗 Gemini API 額度，並可能產生成本。確定要繼續？")) {
            return;
          }
          form.append("confirm_paid", "true");
          const paid = await api("/api/transcript/analyze", { method: "POST", body: form });
          await previewReport(paid.markdown_url);
          setStatus("完成", "completed");
          return;
        }
        const manual = await api("/api/transcript/manual-prepare", { method: "POST", body: form });
        state.currentPreviewText = manual.prompt;
        $("preview").textContent = manual.prompt;
        setStatus("完成", "completed");
        return;
      }

      const video = $("videoSelect").value;
      if (!video) {
        alert("請先選擇影片。");
        return;
      }
      form.append("video_name", video);
      form.append("chunk_minutes", $("chunkMinutes").value);
      if (runMode === "paid") {
        if (!state.hasApiKey) {
          alert("付費 API 模式需要有效 GEMINI_API_KEY。");
          return;
        }
        if (!confirm("付費 API 模式會消耗 Gemini API 額度，並可能產生成本。確定要繼續？")) {
          return;
        }
        form.append("force", "false");
        form.append("confirm_paid", "true");
        form.append("profile_id", $("profileSelect").value || "news_editor");
        result = await api("/api/analyze", { method: "POST", body: form });
      } else {
        result = await api("/api/manual-prepare", { method: "POST", body: form });
      }
      state.jobId = result.job_id;
      $("startBtn").disabled = true;
      $("stopBtn").disabled = false;
      $("log").textContent = `${runMode === "paid" ? "付費 API" : "免費模式"}任務已送出...\n`;
      setStatus("排隊中", "running");
      if (state.pollTimer) clearInterval(state.pollTimer);
      state.pollTimer = setInterval(pollJob, 1000);
      await pollJob();
    }

    async function pollJob() {
      if (!state.jobId) return;
      const job = await api(`/api/jobs/${state.jobId}`);
      $("log").textContent = job.log.join("");
      $("log").scrollTop = $("log").scrollHeight;
      const labels = { queued: "排隊中", running: "分析中", completed: "完成", failed: "失敗", stopped: "已停止" };
      setStatus(labels[job.status] || job.status, job.status);
      if (["completed", "failed", "stopped"].includes(job.status)) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
        $("startBtn").disabled = false;
        $("stopBtn").disabled = true;
        $("preview").textContent = job.log.join("");
        state.currentPreviewText = $("preview").textContent;
      }
    }

    async function stopJob() {
      if (!state.jobId) return;
      await api(`/api/jobs/${state.jobId}/stop`, { method: "POST" });
      await pollJob();
    }

    function updateModeUi() {
      const sourceType = $("sourceType").value;
      const runMode = $("runMode").value;
      $("videoPanel").style.display = sourceType === "video" ? "block" : "none";
      $("transcriptPanel").style.display = sourceType === "transcript" ? "block" : "none";
      if (runMode === "paid") {
        $("modeNotice").textContent = state.hasApiKey
          ? "付費 API 模式會消耗 Gemini API 額度，開始前會再次確認。"
          : "付費 API 模式需要有效 GEMINI_API_KEY；目前只能使用免費模式。";
        $("startBtn").textContent = sourceType === "video" ? "付費 API 分析影片" : "付費 API 分析逐字稿";
      } else {
        $("modeNotice").textContent = "免費模式不調用 API，不消耗 Gemini API 額度。";
        $("startBtn").textContent = sourceType === "video" ? "產生免費版影片提示詞" : "產生免費版逐字稿提示詞";
      }
      $("startBtn").disabled = runMode === "paid" && !state.hasApiKey;
    }

    $("uploadBtn").addEventListener("click", uploadVideo);
    $("upload").addEventListener("change", () => {
      if ($("upload").files.length) {
        uploadVideo().catch(err => alert(err.message));
      }
    });
    $("refreshBtn").addEventListener("click", loadVideos);
    $("startBtn").addEventListener("click", () => startAnalyze().catch(err => alert(err.message)));
    $("stopBtn").addEventListener("click", () => stopJob().catch(err => alert(err.message)));
    $("sourceType").addEventListener("change", updateModeUi);
    $("runMode").addEventListener("change", updateModeUi);
    $("copyResultBtn").addEventListener("click", async () => {
      const text = state.currentPreviewText || $("preview").textContent;
      await navigator.clipboard.writeText(text);
      $("copyResultBtn").textContent = "已複製";
      setTimeout(() => $("copyResultBtn").textContent = "複製結果", 1200);
    });

    Promise.all([loadHealth(), loadVideos()]).catch(err => {
      $("health").textContent = err.message;
    });
  </script>
</body>
</html>
"""


WORKBENCH_HTML = r"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI 影片工作台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --text: #111827;
      --muted: #667085;
      --line: #d0d7e2;
      --accent: #0f766e;
      --accent-dark: #115e59;
      --danger: #b42318;
      --ok: #027a48;
      --warn: #b54708;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "Noto Sans TC", system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }
    h1 { margin: 0; font-size: 22px; letter-spacing: 0; }
    h2 { margin: 0 0 10px; font-size: 15px; }
    button, input, select, textarea {
      font: inherit;
      border-radius: 6px;
    }
    button {
      min-height: 34px;
      border: 1px solid transparent;
      padding: 7px 10px;
      background: var(--accent);
      color: #fff;
      font-weight: 600;
      cursor: pointer;
    }
    button:hover { background: var(--accent-dark); }
    button.secondary {
      background: #fff;
      color: #344054;
      border-color: #cbd5e1;
    }
    button.secondary:hover { background: #f8fafc; }
    button.danger {
      background: #fff;
      color: var(--danger);
      border-color: #f1b8b2;
    }
    input, select, textarea {
      width: 100%;
      min-height: 36px;
      border: 1px solid #cbd5e1;
      padding: 7px 9px;
      background: #fff;
    }
    label {
      display: block;
      margin: 10px 0 5px;
      color: #344054;
      font-size: 13px;
    }
    main {
      display: grid;
      grid-template-columns: 320px minmax(420px, 1fr) 420px;
      gap: 12px;
      padding: 12px;
      height: calc(100vh - 62px);
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      overflow: auto;
    }
    .row {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .row > button { flex: 1 1 auto; }
    .muted { color: var(--muted); font-size: 13px; }
    .jobs { display: grid; gap: 8px; margin-top: 10px; }
    .job {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px;
      background: #fff;
      cursor: pointer;
    }
    .job:hover, .job.active { border-color: var(--accent); }
    .status {
      display: inline-flex;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 12px;
      background: #eef4ff;
      color: #3538cd;
    }
    .status.exported, .status.ready_for_review, .status.completed { background: #ecfdf3; color: var(--ok); }
    .status.failed, .status.rejected { background: #fef3f2; color: var(--danger); }
    .status.analyzing, .status.running { background: #fff7ed; color: var(--warn); }
    video {
      width: 100%;
      max-height: 48vh;
      background: #111827;
      border-radius: 8px;
    }
    .summary {
      white-space: pre-wrap;
      line-height: 1.6;
      border-top: 1px solid var(--line);
      margin-top: 12px;
      padding-top: 12px;
    }
    .tabs {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 6px;
      margin-bottom: 10px;
    }
    .tabs button {
      background: #fff;
      color: #344054;
      border-color: #cbd5e1;
    }
    .tabs button.active {
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      margin-bottom: 8px;
      background: #fff;
    }
    .card h3 { margin: 0 0 6px; font-size: 15px; line-height: 1.35; }
    .meta {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }
    .log {
      min-height: 90px;
      max-height: 180px;
      overflow: auto;
      border-radius: 6px;
      padding: 10px;
      background: #101828;
      color: #e4e7ec;
      white-space: pre-wrap;
      font: 12px/1.45 Consolas, "Cascadia Mono", monospace;
    }
    @media (max-width: 1180px) {
      main { grid-template-columns: 300px minmax(0, 1fr); height: auto; }
      section:last-child { grid-column: 1 / -1; }
    }
    @media (max-width: 760px) {
      main { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>AI 影片分析與短影音企劃工作台</h1>
      <div class="muted">Job-based pipeline｜AI 建議，人審定稿</div>
    </div>
    <a href="/" class="muted">舊版控制台</a>
  </header>
  <main>
    <section>
      <h2>導入與任務</h2>
      <label for="videoSelect">本機影片</label>
      <select id="videoSelect"></select>
      <label for="sourceUrl">YouTube / 直播連結</label>
      <input id="sourceUrl" placeholder="https://..." />
      <label for="chunkMinutes">切段分鐘</label>
      <input id="chunkMinutes" type="number" min="1" value="10" />
      <div class="row" style="margin-top:10px">
        <label class="row" style="margin:0; flex:1 1 100%">
          <input id="draftClips" type="checkbox" checked style="width:16px; min-height:16px" />
          <span>輸出短片草稿</span>
        </label>
        <button id="startBtn">開始分析</button>
        <button class="secondary" id="refreshBtn">重新整理</button>
      </div>
      <div class="log" id="runLog" style="margin-top:10px">等待任務...</div>
      <h2 style="margin-top:14px">Jobs</h2>
      <div class="jobs" id="jobs"></div>
    </section>

    <section>
      <div class="row" style="justify-content:space-between">
        <h2 id="currentTitle">尚未選擇 job</h2>
        <span class="status" id="currentStatus">idle</span>
      </div>
      <video id="player" controls></video>
      <div class="row" style="margin-top:10px">
        <button class="secondary" id="openReportBtn">完整報告</button>
        <button class="secondary" id="prepareReviewBtn">重建審稿包</button>
        <button class="secondary" id="exportClipsBtn">輸出短片</button>
      </div>
      <div class="summary" id="summary">選擇左側 job 後會顯示摘要、timecode 與短影音候選。</div>
    </section>

    <section>
      <div class="tabs">
        <button class="active" data-tab="clips">短片</button>
        <button data-tab="quotes">金句</button>
        <button data-tab="timeline">Timecode</button>
      </div>
      <div id="panel"></div>
    </section>
  </main>

  <script>
    const state = { jobs: [], selectedJobId: null, analysis: null, tab: "clips", webJobId: null, poll: null };
    const $ = (id) => document.getElementById(id);

    async function api(url, options = {}) {
      const res = await fetch(url, options);
      const contentType = res.headers.get("content-type") || "";
      const body = contentType.includes("application/json") ? await res.json() : await res.text();
      if (!res.ok) throw new Error(body.detail || body || `HTTP ${res.status}`);
      return body;
    }

    function seconds(value) {
      const parts = String(value || "0").split(":").map(Number);
      if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
      if (parts.length === 2) return parts[0] * 60 + parts[1];
      return Number(value) || 0;
    }

    async function loadVideos() {
      const videos = await api("/api/videos");
      $("videoSelect").innerHTML = videos.length
        ? videos.map(v => `<option value="${escapeHtml(v.name)}">${escapeHtml(v.name)} (${v.size_mb} MB)</option>`).join("")
        : `<option value="">尚無影片</option>`;
    }

    async function loadJobs() {
      state.jobs = await api("/api/workbench/jobs");
      $("jobs").innerHTML = state.jobs.length ? state.jobs.map(job => `
        <div class="job ${job.id === state.selectedJobId ? "active" : ""}" data-job="${escapeHtml(job.id)}">
          <div class="row" style="justify-content:space-between">
            <strong>${escapeHtml(job.title)}</strong>
            <span class="status ${job.status}">${job.status}</span>
          </div>
          <div class="muted">${escapeHtml(job.id)}</div>
          <div class="muted">${escapeHtml(job.updated_at)}</div>
        </div>
      `).join("") : `<div class="muted">目前沒有 job。</div>`;
      document.querySelectorAll(".job").forEach(el => {
        el.addEventListener("click", () => selectJob(el.dataset.job));
      });
    }

    async function selectJob(jobId) {
      state.selectedJobId = jobId;
      await loadJobs();
      const job = await api(`/api/workbench/jobs/${jobId}`);
      $("currentTitle").textContent = job.title;
      $("currentStatus").textContent = job.status;
      $("currentStatus").className = `status ${job.status}`;
      $("player").src = `/workbench/outputs/${jobId}/proxy.mp4`;
      try {
        state.analysis = await api(`/api/workbench/jobs/${jobId}/analysis`);
        $("summary").textContent = state.analysis.overall_summary || "沒有摘要。";
      } catch (err) {
        state.analysis = null;
        $("summary").textContent = "這個 job 尚未產生分析結果。";
      }
      renderPanel();
    }

    function renderPanel() {
      const panel = $("panel");
      if (!state.analysis) {
        panel.innerHTML = `<div class="muted">尚無分析資料。</div>`;
        return;
      }
      if (state.tab === "clips") {
        panel.innerHTML = state.analysis.clip_candidates.map(clip => `
          <div class="card">
            <h3>${escapeHtml(clip.suggested_title)}</h3>
            <div class="meta">${escapeHtml(clip.start_time)} - ${escapeHtml(clip.end_time)}｜${escapeHtml(clip.platform)}｜${escapeHtml(clip.review.status)}</div>
            <div>${escapeHtml(clip.hook || clip.reason)}</div>
            <div class="muted">封面字：${escapeHtml(clip.cover_text || "")}</div>
            <div class="row" style="margin-top:8px">
              <button class="secondary" data-jump="${escapeHtml(clip.start_time)}">跳轉</button>
              <button data-review="${escapeHtml(clip.id)}" data-status="kept">保留</button>
              <button class="secondary" data-revise="${escapeHtml(clip.id)}">改標題</button>
              <button class="danger" data-review="${escapeHtml(clip.id)}" data-status="rejected">刪除</button>
            </div>
          </div>
        `).join("") || `<div class="muted">沒有短片候選。</div>`;
      } else if (state.tab === "quotes") {
        panel.innerHTML = state.analysis.quote_candidates.map(quote => `
          <div class="card">
            <h3>${escapeHtml(quote.quote)}</h3>
            <div class="meta">${escapeHtml(quote.timecode)}｜${escapeHtml(quote.speaker)}｜${escapeHtml(quote.emotion_tone)}</div>
            <div>${escapeHtml(quote.usage)}</div>
            <div class="row" style="margin-top:8px">
              <button class="secondary" data-jump="${escapeHtml(quote.timecode)}">跳轉</button>
            </div>
          </div>
        `).join("") || `<div class="muted">沒有金句。</div>`;
      } else {
        panel.innerHTML = state.analysis.timeline_events.map(event => `
          <div class="card">
            <h3>${escapeHtml(event.title)}</h3>
            <div class="meta">${escapeHtml(event.start_time)}${event.end_time ? " - " + escapeHtml(event.end_time) : ""}</div>
            <div>${escapeHtml(event.summary)}</div>
            <div class="muted">${escapeHtml(event.news_value || "")}</div>
            <div class="row" style="margin-top:8px">
              <button class="secondary" data-jump="${escapeHtml(event.start_time)}">跳轉</button>
            </div>
          </div>
        `).join("") || `<div class="muted">沒有 timecode。</div>`;
      }
      bindPanelActions();
    }

    function bindPanelActions() {
      document.querySelectorAll("[data-jump]").forEach(btn => {
        btn.addEventListener("click", () => {
          $("player").currentTime = seconds(btn.dataset.jump);
          $("player").play().catch(() => {});
        });
      });
      document.querySelectorAll("[data-review]").forEach(btn => {
        btn.addEventListener("click", () => reviewClip(btn.dataset.review, btn.dataset.status));
      });
      document.querySelectorAll("[data-revise]").forEach(btn => {
        btn.addEventListener("click", () => reviseClip(btn.dataset.revise));
      });
    }

    async function reviewClip(clipId, status, extra = {}) {
      if (!state.selectedJobId) return;
      await api(`/api/workbench/jobs/${state.selectedJobId}/clips/${clipId}/review`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, ...extra })
      });
      await selectJob(state.selectedJobId);
    }

    async function reviseClip(clipId) {
      const title = prompt("新的短影音標題");
      if (!title) return;
      const hook = prompt("新的 Hook", "") || "";
      await reviewClip(clipId, "revised", { updated_title: title, updated_hook: hook });
    }

    async function startAnalyze() {
      const form = new FormData();
      form.append("video_name", $("videoSelect").value || "");
      form.append("source_url", $("sourceUrl").value.trim());
      form.append("chunk_minutes", $("chunkMinutes").value);
      form.append("draft_clips", $("draftClips").checked ? "true" : "false");
      form.append("confirm_paid", "true");
      if (!confirm("工作台分析會呼叫 Gemini API 並可能產生成本。確定要繼續？")) return;
      const result = await api("/api/workbench/analyze", { method: "POST", body: form });
      state.webJobId = result.web_job_id;
      $("runLog").textContent = "任務已送出...\n";
      if (state.poll) clearInterval(state.poll);
      state.poll = setInterval(pollRun, 1000);
      await pollRun();
    }

    async function pollRun() {
      if (!state.webJobId) return;
      const job = await api(`/api/jobs/${state.webJobId}`);
      $("runLog").textContent = job.log.join("");
      $("runLog").scrollTop = $("runLog").scrollHeight;
      if (["completed", "failed", "stopped"].includes(job.status)) {
        clearInterval(state.poll);
        state.poll = null;
        await loadJobs();
      }
    }

    async function runJobAction(action) {
      if (!state.selectedJobId) return;
      const result = await api(`/api/workbench/jobs/${state.selectedJobId}/${action}`, { method: "POST" });
      state.webJobId = result.web_job_id;
      $("runLog").textContent = "任務已送出...\n";
      if (state.poll) clearInterval(state.poll);
      state.poll = setInterval(pollRun, 1000);
      await pollRun();
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
      })[ch]);
    }

    document.querySelectorAll(".tabs button").forEach(btn => {
      btn.addEventListener("click", () => {
        state.tab = btn.dataset.tab;
        document.querySelectorAll(".tabs button").forEach(item => item.classList.toggle("active", item === btn));
        renderPanel();
      });
    });
    $("startBtn").addEventListener("click", () => startAnalyze().catch(err => alert(err.message)));
    $("refreshBtn").addEventListener("click", () => Promise.all([loadVideos(), loadJobs()]).catch(err => alert(err.message)));
    $("prepareReviewBtn").addEventListener("click", () => runJobAction("prepare-review").catch(err => alert(err.message)));
    $("exportClipsBtn").addEventListener("click", () => runJobAction("export-clips").catch(err => alert(err.message)));
    $("openReportBtn").addEventListener("click", () => {
      if (state.selectedJobId) window.open(`/workbench/outputs/${state.selectedJobId}/full_report.md`, "_blank");
    });

    Promise.all([loadVideos(), loadJobs()]).catch(err => alert(err.message));
  </script>
</body>
</html>
"""
