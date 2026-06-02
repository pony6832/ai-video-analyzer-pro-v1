from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from app.config import Settings
from app.gem_profiles import get_profile
from app.gemini_client import GeminiClient, GeminiClientError
from app.production_writer import write_production_json, write_production_markdown
from app.prompts import PRODUCTION_SCHEMA, production_prompt
from app.report_writer import write_json
from app.schemas import ProductionHighlightReport
from app.video_utils import get_video_info, split_video


class ProductionVideoWorkflow:
    def __init__(self, settings: Settings, console: Console | None = None) -> None:
        self.settings = settings
        self.console = console or Console()

    def analyze(
        self,
        video_path: Path,
        chunk_minutes: int = 10,
        profile_id: str = "production_director",
        force: bool = False,
    ) -> dict:
        api_key = self.settings.require_api_key()
        client = GeminiClient(
            api_key=api_key,
            model=self.settings.gemini_model,
            fallback_models=self.settings.fallback_models_list(),
        )
        profile = get_profile(profile_id)
        video_info = get_video_info(video_path)
        segment_dir = self.settings.temp_dir / video_path.stem
        segments = split_video(video_path, segment_dir, chunk_minutes, video_info.duration_seconds)
        reports: list[ProductionHighlightReport] = []
        risk_notes: list[str] = []

        for segment in segments:
            cache_path = self.settings.outputs_dir / "cache" / f"{segment.path.stem}_production.json"
            if cache_path.exists() and not force:
                try:
                    reports.append(ProductionHighlightReport.model_validate_json(cache_path.read_text(encoding="utf-8")))
                    self.console.print(f"[green]使用 production cache：{cache_path.name}[/green]")
                    continue
                except Exception as exc:
                    self.console.print(f"[yellow]production cache 讀取失敗，重新分析：{exc}[/yellow]")

            context = "\n".join(
                [
                    f"- segment_index: {segment.index}",
                    f"- start_time: {segment.start_time}",
                    f"- end_time: {segment.end_time}",
                    f"- 片段檔案：{segment.path}",
                    "",
                    "請直接分析已上傳的影片片段。",
                    "JSON schema：",
                    json.dumps(PRODUCTION_SCHEMA, ensure_ascii=False, indent=2),
                ]
            )
            try:
                report = client.analyze_video_file(
                    segment.path,
                    production_prompt(profile.instructions, "影片片段", context),
                    ProductionHighlightReport,
                )
                write_json(cache_path, report)
                reports.append(report)
            except (GeminiClientError, json.JSONDecodeError, OSError) as exc:
                risk_notes.append(f"part {segment.index} ({segment.start_time}-{segment.end_time}) 分析失敗：{exc}")
                self.console.print(f"[red]production 片段 {segment.index} 失敗，繼續下一段：{exc}[/red]")

        combined = self._combine_reports(reports, risk_notes)
        base_name = f"{video_path.stem}_production_highlights"
        json_path = self.settings.outputs_dir / f"{base_name}.json"
        md_path = self.settings.outputs_dir / f"{base_name}.md"
        write_production_json(json_path, combined)
        write_production_markdown(md_path, combined, "Production Video Highlight Report")
        return {"report": combined, "json_path": json_path, "md_path": md_path, "segments": len(reports)}

    def _combine_reports(self, reports: list[ProductionHighlightReport], risk_notes: list[str]) -> ProductionHighlightReport:
        if not reports:
            return ProductionHighlightReport(
                core_summary=["所有片段皆分析失敗，無法產生 production highlight report。"],
                risk_notes=risk_notes,
            )
        core_summary: list[str] = []
        golden_quotes = []
        chapter_suggestions = []
        editing_suggestions = []
        timecode_notes = []
        combined_risk_notes = list(risk_notes)
        for report in reports:
            core_summary.extend(report.core_summary)
            golden_quotes.extend(report.golden_quotes)
            chapter_suggestions.extend(report.chapter_suggestions)
            editing_suggestions.extend(report.editing_suggestions)
            timecode_notes.extend(report.timecode_notes)
            combined_risk_notes.extend(report.risk_notes)
        return ProductionHighlightReport(
            core_summary=core_summary[:3] or ["需人工確認：沒有足夠摘要內容。"],
            golden_quotes=golden_quotes[:12],
            chapter_suggestions=chapter_suggestions[:12],
            editing_suggestions=editing_suggestions[:16],
            timecode_notes=timecode_notes,
            risk_notes=combined_risk_notes,
        )

