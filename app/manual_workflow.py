import json
from datetime import datetime
from pathlib import Path

from rich.console import Console

from app.config import Settings
from app.gem_profiles import get_profile
from app.prompts import PRODUCTION_SCHEMA, SEGMENT_SCHEMA, production_prompt, segment_prompt
from app.video_utils import get_video_info, split_video


class ManualWorkflow:
    def __init__(self, settings: Settings, console: Console | None = None) -> None:
        self.settings = settings
        self.console = console or Console()

    def prepare(self, video_path: Path, chunk_minutes: int = 10, profile_id: str = "news_editor") -> dict[str, Path]:
        if not video_path.exists():
            raise FileNotFoundError(f"找不到影片檔：{video_path}")
        if chunk_minutes <= 0:
            raise ValueError("--chunk-minutes 必須大於 0")

        profile = get_profile(profile_id)
        video_info = get_video_info(video_path)
        segment_dir = self.settings.temp_dir / video_path.stem
        self.console.print(f"[cyan]免費模式：切段中，每 {chunk_minutes} 分鐘一段[/cyan]")
        segments = split_video(video_path, segment_dir, chunk_minutes, video_info.duration_seconds)

        prompts_path = self.settings.outputs_dir / f"{video_path.stem}_manual_prompts.md"
        manifest_path = self.settings.outputs_dir / f"{video_path.stem}_manual_manifest.json"

        manifest = {
            "mode": "manual_web_free",
            "video": video_info.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "segments": [
                {
                    "segment_index": segment.index,
                    "file": str(segment.path.resolve()),
                    "start_time": segment.start_time,
                    "end_time": segment.end_time,
                }
                for segment in segments
            ],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        prompts_path.write_text(self._build_prompts_markdown(video_path, profile, manifest), encoding="utf-8")

        self.console.print("[green]免費模式工作包已產生[/green]")
        self.console.print(f"- 提示詞：{prompts_path}")
        self.console.print(f"- 片段 manifest：{manifest_path}")
        return {"prompts": prompts_path, "manifest": manifest_path}

    def _build_prompts_markdown(self, video_path: Path, profile, manifest: dict) -> str:
        lines = [
            "# AI Video News Analyzer Free Mode Prompts",
            "",
            "## 使用方式",
            "",
            "1. 打開 Gemini Web 或你的公司帳號 Gem。",
            "2. 逐一上傳下方列出的影片片段檔。",
            "3. 複製對應片段的 prompt 貼到 Gemini/Gem。",
            "4. 要求 Gemini 只回 JSON。",
            "5. 目前這版不呼叫 Gemini API，因此不會消耗 API 額度。",
            "",
            "## Gem 模板",
            "",
            f"- 名稱：{profile.name}",
            f"- 指令：{profile.instructions}",
            "",
            "## 原始影片",
            "",
            f"- 檔名：{video_path.name}",
            f"- 影片長度：{manifest['video']['duration']}",
            f"- 解析度：{manifest['video'].get('width')}x{manifest['video'].get('height')}",
            "",
        ]

        for segment in manifest["segments"]:
            if profile.id == "production_director":
                context = "\n".join(
                    [
                        f"- segment_index: {segment['segment_index']}",
                        f"- start_time: {segment['start_time']}",
                        f"- end_time: {segment['end_time']}",
                        f"- 片段檔案：{segment['file']}",
                        "",
                        "請直接分析已上傳的影片片段。",
                        "JSON schema 參考如下：",
                        json.dumps(PRODUCTION_SCHEMA, ensure_ascii=False, indent=2),
                    ]
                )
                prompt = production_prompt(profile.instructions, "影片片段", context)
                schema_text = json.dumps(PRODUCTION_SCHEMA, ensure_ascii=False, indent=2)
            else:
                prompt = segment_prompt(segment["segment_index"], segment["start_time"], segment["end_time"])
                schema_text = json.dumps(SEGMENT_SCHEMA, ensure_ascii=False, indent=2)
            lines.extend(
                [
                    f"## Part {segment['segment_index']:03d}",
                    "",
                    f"- 片段檔案：`{segment['file']}`",
                    f"- 起訖時間：{segment['start_time']} - {segment['end_time']}",
                    "",
                    "### 請貼到 Gemini/Gem 的 prompt",
                    "",
                    "```text",
                    f"{profile.instructions}",
                    "",
                    prompt,
                    "",
                    "請再次確認：只輸出 JSON，不要輸出 Markdown，不要加入解釋文字。",
                    "JSON schema 參考如下：",
                    schema_text,
                    "```",
                    "",
                ]
            )

        lines.extend(
            [
                "## 全片彙整提示",
                "",
                "當你取得所有片段 JSON 後，把它們貼給 Gemini/Gem，並使用以下提示：",
                "",
                "```text",
                "你是資深新聞主編與短影音策略編輯。請根據我貼上的所有片段 JSON 做全片總整理。",
                "請輸出：全片摘要、10 個重點、5 個短影音片段、新聞標題 10 個、YouTube 標題 10 個、短影音 Hook 10 個、新聞稿初稿、旁白稿初稿、需要人工查證清單。",
                "所有內容使用繁體中文。不要編造片段 JSON 沒有的內容。不確定就標記需要人工確認。",
                "```",
                "",
            ]
        )
        return "\n".join(lines)
