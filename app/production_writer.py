import json
from pathlib import Path

from app.schemas import ProductionHighlightReport


def write_production_json(path: Path, report: ProductionHighlightReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")


def write_production_markdown(path: Path, report: ProductionHighlightReport, title: str) -> None:
    lines = [
        f"# {title}",
        "",
        "## 核心摘要",
        "",
    ]
    lines.extend(f"{index}. {item}" for index, item in enumerate(report.core_summary, start=1))

    lines.extend(["", "## Golden Quotes", "", "| Timecode | 金句 | 情緒語氣 | 用途 |", "|---|---|---|---|"])
    for quote in report.golden_quotes:
        lines.append(f"| {quote.timecode} | {quote.quote} | {quote.emotion_tone} | {quote.usage} |")

    lines.extend(["", "## 章節結構建議", "", "| 起訖時間 | 章節名稱 | 內容大綱 | 主標題 | 平台 |", "|---|---|---|---|---|"])
    for chapter in report.chapter_suggestions:
        lines.append(
            f"| {chapter.start_time} - {chapter.end_time} | {chapter.chapter_name} | "
            f"{chapter.outline} | {chapter.main_title} | {chapter.platform} |"
        )

    lines.extend(["", "## 剪輯建議", "", "| Timecode | 類型 | 建議 | 理由 |", "|---|---|---|---|"])
    for suggestion in report.editing_suggestions:
        lines.append(
            f"| {suggestion.timecode} | {suggestion.suggestion_type} | "
            f"{suggestion.description} | {suggestion.reason} |"
        )

    lines.extend(["", "## Timecode 備註", ""])
    lines.extend(f"- {note}" for note in report.timecode_notes)
    lines.extend(["", "## 需要人工確認", ""])
    lines.extend(f"- {note}" for note in report.risk_notes)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

