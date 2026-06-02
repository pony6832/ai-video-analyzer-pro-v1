from pathlib import Path

import typer
from rich.console import Console

from app.analyzer import Analyzer
from app.config import get_settings
from app.manual_workflow import ManualWorkflow
from app.production_video_workflow import ProductionVideoWorkflow
from app.transcript_workflow import TranscriptWorkflow
from app.video_utils import FFmpegNotFoundError, VideoProcessingError
from app.workbench import WorkbenchPipeline, WorkbenchStore


app = typer.Typer(help="AI Video News Analyzer CLI")
console = Console()


@app.callback()
def cli() -> None:
    """長影片新聞分析工具。"""


@app.command()
def analyze(
    video_path: Path = typer.Argument(..., help="要分析的影片檔路徑"),
    chunk_minutes: int = typer.Option(10, "--chunk-minutes", "-c", help="每段影片長度，單位為分鐘"),
    force: bool = typer.Option(False, "--force", help="忽略 cache，重新分析所有片段"),
) -> None:
    """分析長影片並輸出新聞摘要、時間軸、短影音建議與新聞稿初稿。"""
    try:
        settings = get_settings()
        result = Analyzer(settings=settings, console=console).analyze(
            video_path=video_path,
            chunk_minutes=chunk_minutes,
            force=force,
        )
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except FFmpegNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except VideoProcessingError as exc:
        console.print(f"[red]影片處理失敗：{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt as exc:
        console.print("[yellow]使用者中止。已完成片段的 cache 會保留，下次可接續執行。[/yellow]")
        raise typer.Exit(code=130) from exc
    except Exception as exc:
        console.print(f"[red]分析失敗：{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print("[green]分析完成[/green]")
    console.print(f"- 成功片段：{len(result.segments)}")
    console.print(f"- 失敗片段：{len(result.errors)}")
    console.print(f"- 輸出資料夾：{settings.outputs_dir}")


@app.command("analyze-video")
def analyze_video(
    source: str = typer.Argument(..., help="本機影片路徑或 YouTube/直播連結"),
    chunk_minutes: int = typer.Option(10, "--chunk-minutes", "-c", help="每段影片長度，單位為分鐘"),
    profile_id: str = typer.Option("production_director", "--profile-id", help="分析模板 ID"),
    draft_clips: bool = typer.Option(True, "--draft-clips/--no-draft-clips", help="是否輸出短片草稿 MP4"),
    force: bool = typer.Option(False, "--force", help="忽略 workbench cache，重新分析所有片段"),
) -> None:
    """新版工作台：建立 job，分析影片，輸出審稿包、字幕、短片候選與草稿。"""
    try:
        settings = get_settings()
        analysis = WorkbenchPipeline(settings=settings, console=console).analyze_source(
            source=source,
            chunk_minutes=chunk_minutes,
            profile_id=profile_id,
            create_draft_clips=draft_clips,
            force=force,
        )
    except (FileNotFoundError, ValueError, FFmpegNotFoundError, VideoProcessingError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"[red]新版工作台分析失敗：{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print("[green]新版工作台分析完成[/green]")
    console.print(f"- Job ID：{analysis.job.id}")
    console.print(f"- 短片候選：{len(analysis.clip_candidates)}")
    console.print(f"- 金句：{len(analysis.quote_candidates)}")
    console.print(f"- 輸出資料夾：{settings.outputs_dir / 'jobs' / analysis.job.id}")


@app.command("prepare-review")
def prepare_review(
    job_id: str = typer.Argument(..., help="要準備審稿包的 Job ID"),
) -> None:
    """依既有 job/analysis 重新輸出審稿所需 JSON、Markdown、SRT 與 EDL。"""
    try:
        settings = get_settings()
        outputs = WorkbenchPipeline(settings=settings, console=console).export_review_package(
            job_id,
            create_draft_clips=False,
        )
    except Exception as exc:
        console.print(f"[red]審稿包輸出失敗：{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print("[green]審稿包已準備完成[/green]")
    for name, path in outputs.items():
        console.print(f"- {name}：{path}")


@app.command("export-clips")
def export_clips(
    job_id: str = typer.Argument(..., help="要輸出短片草稿的 Job ID"),
) -> None:
    """依短影音候選輸出 FFmpeg 短片草稿。"""
    try:
        settings = get_settings()
        outputs = WorkbenchPipeline(settings=settings, console=console).export_review_package(
            job_id,
            create_draft_clips=True,
        )
    except Exception as exc:
        console.print(f"[red]短片草稿輸出失敗：{exc}[/red]")
        raise typer.Exit(code=1) from exc
    draft_outputs = {name: path for name, path in outputs.items() if name.startswith("draft_clip_")}
    console.print("[green]短片草稿輸出完成[/green]")
    if not draft_outputs:
        console.print("[yellow]沒有可輸出的短片候選，或短片輸出失敗。[/yellow]")
    for name, path in draft_outputs.items():
        console.print(f"- {name}：{path}")


@app.command("export-report")
def export_report(
    job_id: str = typer.Argument(..., help="要輸出完整報告的 Job ID"),
) -> None:
    """重新輸出完整 Markdown 報告。"""
    try:
        settings = get_settings()
        outputs = WorkbenchPipeline(settings=settings, console=console).export_review_package(
            job_id,
            create_draft_clips=False,
        )
    except Exception as exc:
        console.print(f"[red]報告輸出失敗：{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]完整報告：{outputs['full_report']}[/green]")


@app.command("list-video-jobs")
def list_video_jobs() -> None:
    """列出新版工作台 job。"""
    settings = get_settings()
    jobs = WorkbenchStore(settings).list_jobs()
    if not jobs:
        console.print("[yellow]目前沒有工作台 job。[/yellow]")
        return
    for job in jobs:
        console.print(f"{job.id}｜{job.status}｜{job.title}｜{job.updated_at}")


@app.command("prepare-manual")
def prepare_manual(
    video_path: Path = typer.Argument(..., help="要準備免費模式工作包的影片檔路徑"),
    chunk_minutes: int = typer.Option(10, "--chunk-minutes", "-c", help="每段影片長度，單位為分鐘"),
    profile_id: str = typer.Option("news_editor", "--profile-id", help="Gem-like 分析模板 ID"),
) -> None:
    """免費模式：只做影片資訊、切段與 Gemini Web/Gem 提示詞，不呼叫 API。"""
    try:
        settings = get_settings()
        outputs = ManualWorkflow(settings=settings, console=console).prepare(
            video_path=video_path,
            chunk_minutes=chunk_minutes,
            profile_id=profile_id,
        )
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except FFmpegNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except VideoProcessingError as exc:
        console.print(f"[red]影片處理失敗：{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"[red]免費模式準備失敗：{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print("[green]免費模式工作包完成[/green]")
    console.print(f"- 提示詞：{outputs['prompts']}")
    console.print(f"- Manifest：{outputs['manifest']}")


@app.command("prepare-transcript")
def prepare_transcript(
    transcript_path: Path = typer.Argument(..., help="逐字稿文字檔路徑"),
    profile_id: str = typer.Option("production_director", "--profile-id", help="Gem-like 分析模板 ID"),
) -> None:
    """免費模式：產生可貼到 Gemini Web/Gem 的逐字稿分析 prompt。"""
    try:
        settings = get_settings()
        outputs = TranscriptWorkflow(settings=settings, console=console).prepare_file(
            transcript_path=transcript_path,
            profile_id=profile_id,
        )
    except Exception as exc:
        console.print(f"[red]逐字稿 prompt 產生失敗：{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]完成：{outputs['prompt']}[/green]")


@app.command("analyze-transcript")
def analyze_transcript(
    transcript_path: Path = typer.Argument(..., help="逐字稿文字檔路徑"),
    profile_id: str = typer.Option("production_director", "--profile-id", help="Gem-like 分析模板 ID"),
) -> None:
    """付費 API 模式：直接分析逐字稿並輸出 production highlight report。"""
    try:
        settings = get_settings()
        TranscriptWorkflow(settings=settings, console=console).analyze_file_paid(
            transcript_path=transcript_path,
            profile_id=profile_id,
        )
    except Exception as exc:
        console.print(f"[red]逐字稿 API 分析失敗：{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command("analyze-production-video")
def analyze_production_video(
    video_path: Path = typer.Argument(..., help="要用 production schema 分析的影片檔路徑"),
    chunk_minutes: int = typer.Option(10, "--chunk-minutes", "-c", help="每段影片長度，單位為分鐘"),
    profile_id: str = typer.Option("production_director", "--profile-id", help="Gem-like 分析模板 ID"),
    force: bool = typer.Option(False, "--force", help="忽略 production cache，重新分析所有片段"),
) -> None:
    """付費 API 模式：用影視後製 schema 分析影片並輸出 Golden Quotes / 章節 / 剪輯建議。"""
    try:
        settings = get_settings()
        outputs = ProductionVideoWorkflow(settings=settings, console=console).analyze(
            video_path=video_path,
            chunk_minutes=chunk_minutes,
            profile_id=profile_id,
            force=force,
        )
    except Exception as exc:
        console.print(f"[red]production 影片 API 分析失敗：{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print("[green]production 影片分析完成[/green]")
    console.print(f"- Markdown：{outputs['md_path']}")
    console.print(f"- JSON：{outputs['json_path']}")


if __name__ == "__main__":
    app()
