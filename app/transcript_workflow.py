from __future__ import annotations

import re
import json
from datetime import datetime
from pathlib import Path

from rich.console import Console

from app.config import Settings
from app.gem_profiles import get_profile
from app.gemini_client import GeminiClient
from app.production_writer import write_production_json, write_production_markdown
from app.prompts import PRODUCTION_SCHEMA, production_prompt
from app.schemas import ProductionHighlightReport


TIMECODE_PATTERN = re.compile(r"(\d{1,2}:)?\d{1,2}:\d{2}")


class TranscriptWorkflow:
    def __init__(self, settings: Settings, console: Console | None = None) -> None:
        self.settings = settings
        self.console = console or Console()

    def prepare_prompt_text(self, transcript: str, profile_id: str = "production_director") -> str:
        profile = get_profile(profile_id)
        has_timecode = bool(TIMECODE_PATTERN.search(transcript))
        context = "\n".join(
            [
                f"- 來源：訪談逐字稿",
                f"- 是否偵測到 timecode：{'是' if has_timecode else '否'}",
                "",
                "逐字稿：",
                transcript,
                "",
                "JSON schema：",
                json.dumps(PRODUCTION_SCHEMA, ensure_ascii=False, indent=2),
            ]
        )
        return production_prompt(profile.instructions, "訪談逐字稿", context)

    def prepare_file(self, transcript_path: Path, profile_id: str = "production_director") -> dict[str, Path]:
        transcript = transcript_path.read_text(encoding="utf-8")
        prompt = self.prepare_prompt_text(transcript, profile_id=profile_id)
        output_path = self.settings.outputs_dir / f"{transcript_path.stem}_transcript_prompt.md"
        output_path.write_text(
            "\n".join(
                [
                    "# Transcript Free Mode Prompt",
                    "",
                    "以下內容可貼到 Gemini Web/Gem。這個流程不調用 Gemini API。",
                    "",
                    "```text",
                    prompt,
                    "```",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.console.print(f"[green]逐字稿免費模式 prompt 已產生：{output_path}[/green]")
        return {"prompt": output_path}

    def analyze_text_paid(self, transcript: str, profile_id: str = "production_director", base_name: str = "transcript") -> ProductionHighlightReport:
        outputs = self.analyze_text_paid_outputs(transcript, profile_id=profile_id, base_name=base_name)
        return outputs["report"]

    def analyze_text_paid_outputs(
        self,
        transcript: str,
        profile_id: str = "production_director",
        base_name: str = "transcript",
    ) -> dict:
        api_key = self.settings.require_api_key()
        client = GeminiClient(
            api_key=api_key,
            model=self.settings.gemini_model,
            fallback_models=self.settings.fallback_models_list(),
        )
        prompt = self.prepare_prompt_text(transcript, profile_id=profile_id)
        report = client.generate_json_from_text(prompt, ProductionHighlightReport)
        json_path = self.settings.outputs_dir / f"{base_name}_production_highlights.json"
        md_path = self.settings.outputs_dir / f"{base_name}_production_highlights.md"
        write_production_json(json_path, report)
        write_production_markdown(md_path, report, "Production Highlight Report")
        self.console.print(f"[green]逐字稿付費 API 分析完成：{md_path}[/green]")
        return {"report": report, "json_path": json_path, "md_path": md_path}

    def analyze_file_paid(self, transcript_path: Path, profile_id: str = "production_director") -> ProductionHighlightReport:
        transcript = transcript_path.read_text(encoding="utf-8")
        return self.analyze_text_paid(
            transcript,
            profile_id=profile_id,
            base_name=f"{transcript_path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        )
