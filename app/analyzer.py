import json
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from rich.console import Console

from app.config import Settings
from app.gemini_client import GeminiClient, GeminiClientError
from app.prompts import full_report_prompt, segment_prompt
from app.report_writer import read_segment_cache, write_outputs, write_segment_cache
from app.schemas import AnalysisResult, FullReport, SegmentAnalysis, SegmentError
from app.video_utils import VideoSegment, get_video_info, split_video


class Analyzer:
    def __init__(self, settings: Settings, console: Console | None = None) -> None:
        self.settings = settings
        self.console = console or Console()

    def analyze(self, video_path: Path, chunk_minutes: int = 10, force: bool = False) -> AnalysisResult:
        if not video_path.exists():
            raise FileNotFoundError(f"找不到影片檔：{video_path}")

        api_key = self.settings.require_api_key()
        client = GeminiClient(
            api_key=api_key,
            model=self.settings.gemini_model,
            fallback_models=self.settings.fallback_models_list(),
        )

        video_info = get_video_info(video_path)
        self._print_video_info(video_info)

        segment_dir = self.settings.temp_dir / video_path.stem
        self.console.print(f"[cyan]切段中：每 {chunk_minutes} 分鐘一段，輸出到 {segment_dir}[/cyan]")
        chunks = split_video(video_path, segment_dir, chunk_minutes, video_info.duration_seconds)

        analyses: list[SegmentAnalysis] = []
        errors: list[SegmentError] = []

        for segment in chunks:
            cache_path = self.settings.outputs_dir / "cache" / f"{segment.path.stem}.json"
            if cache_path.exists() and not force:
                try:
                    cached = read_segment_cache(cache_path)
                    analyses.append(cached)
                    self.console.print(f"[green]使用快取：{cache_path.name}[/green]")
                    continue
                except (ValidationError, json.JSONDecodeError, OSError) as exc:
                    self.console.print(f"[yellow]快取讀取失敗，重新分析 {segment.path.name}：{exc}[/yellow]")

            try:
                self.console.print(f"[cyan]分析片段 {segment.index}/{len(chunks)}：{segment.path.name}[/cyan]")
                analysis = client.analyze_video_file(
                    segment.path,
                    segment_prompt(segment.index, segment.start_time, segment.end_time),
                    SegmentAnalysis,
                )
                write_segment_cache(cache_path, analysis)
                analyses.append(analysis)
            except (GeminiClientError, ValidationError, json.JSONDecodeError, OSError) as exc:
                errors.append(self._segment_error(segment, exc))
                self.console.print(f"[red]片段 {segment.index} 失敗，繼續下一段：{exc}[/red]")

        full_report = self._build_full_report(client, analyses, errors)
        result = AnalysisResult(
            video_info=video_info,
            model=client.last_model_used or self.settings.gemini_model,
            analyzed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            segments=analyses,
            errors=errors,
            full_report=full_report,
        )
        write_outputs(result, self.settings.outputs_dir, video_path.stem)
        return result

    def _build_full_report(
        self,
        client: GeminiClient,
        analyses: list[SegmentAnalysis],
        errors: list[SegmentError],
    ) -> FullReport:
        if not analyses:
            return FullReport(
                overall_summary_300_words="所有片段皆分析失敗，無法產生全片摘要。",
                top_10_points=[],
                best_5_short_video_segments=[],
                news_title_suggestions=[],
                youtube_title_suggestions=[],
                short_video_hooks=[],
                news_draft="所有片段皆分析失敗，無法產生新聞稿初稿。",
                voiceover_draft="所有片段皆分析失敗，無法產生旁白稿初稿。",
                fact_check_items=[error.error for error in errors],
            )

        segments_json = json.dumps([item.model_dump(mode="json") for item in analyses], ensure_ascii=False, indent=2)
        try:
            self.console.print("[cyan]彙整全片報告[/cyan]")
            report = client.generate_json_from_text(full_report_prompt(segments_json), FullReport)
            if errors:
                report.fact_check_items.extend([f"片段 {error.segment_index} 分析失敗，需要人工確認：{error.error}" for error in errors])
            return report
        except (GeminiClientError, ValidationError, json.JSONDecodeError) as exc:
            self.console.print(f"[yellow]全片彙整失敗，改用片段結果產生基本報告：{exc}[/yellow]")
            return self._fallback_report(analyses, errors, exc)

    def _fallback_report(
        self,
        analyses: list[SegmentAnalysis],
        errors: list[SegmentError],
        exc: Exception,
    ) -> FullReport:
        candidates = [candidate for segment in analyses for candidate in segment.short_video_candidates][:5]
        points = [event.event for segment in analyses for event in segment.key_events][:10]
        risk_notes = [note for segment in analyses for note in segment.risk_notes]
        risk_notes.extend([f"片段 {error.segment_index} 分析失敗，需要人工確認：{error.error}" for error in errors])
        risk_notes.append(f"全片彙整失敗，需要人工確認：{exc}")
        summary = "\n\n".join(segment.summary for segment in analyses)
        return FullReport(
            overall_summary_300_words=summary[:900],
            top_10_points=points,
            best_5_short_video_segments=candidates,
            news_title_suggestions=["需要人工編輯依片段摘要補上新聞標題"],
            youtube_title_suggestions=["需要人工編輯依片段摘要補上 YouTube 標題"],
            short_video_hooks=["需要人工編輯依片段摘要補上短影音 Hook"],
            news_draft=summary,
            voiceover_draft=summary,
            fact_check_items=risk_notes,
        )

    def _segment_error(self, segment: VideoSegment, exc: Exception) -> SegmentError:
        return SegmentError(
            segment_index=segment.index,
            file=str(segment.path),
            start_time=segment.start_time,
            end_time=segment.end_time,
            error=str(exc),
        )

    def _print_video_info(self, video_info) -> None:
        self.console.print("[bold]影片基本資訊[/bold]")
        self.console.print(f"- 檔名：{video_info.file_name}")
        self.console.print(f"- 長度：{video_info.duration}")
        self.console.print(f"- 大小：{video_info.file_size_mb} MB")
        self.console.print(f"- 解析度：{video_info.width}x{video_info.height}")
        self.console.print(f"- FPS：{video_info.fps}")
        self.console.print(f"- 音訊：{video_info.audio or '無音訊資訊'}")
