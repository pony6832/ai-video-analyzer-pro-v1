import json
from pathlib import Path

from app.schemas import AnalysisResult, FullReport, SegmentAnalysis, VideoInfo


def to_jsonable(data: object) -> object:
    if hasattr(data, "model_dump"):
        return data.model_dump(mode="json")
    if isinstance(data, list):
        return [to_jsonable(item) for item in data]
    if isinstance(data, tuple):
        return [to_jsonable(item) for item in data]
    if isinstance(data, dict):
        return {key: to_jsonable(value) for key, value in data.items()}
    return data


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = to_jsonable(data)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_segment_cache(path: Path) -> SegmentAnalysis:
    return SegmentAnalysis.model_validate_json(path.read_text(encoding="utf-8"))


def write_segment_cache(path: Path, segment: SegmentAnalysis) -> None:
    write_json(path, segment)


def write_outputs(result: AnalysisResult, outputs_dir: Path, base_name: str) -> None:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    write_summary(outputs_dir / f"{base_name}_summary.md", result.full_report)
    write_timeline(outputs_dir / f"{base_name}_timeline.json", result.segments)
    write_short_video_ideas(outputs_dir / f"{base_name}_short_video_ideas.md", result.full_report)
    write_news_draft(outputs_dir / f"{base_name}_news_draft.md", result.full_report)
    write_full_report(outputs_dir / f"{base_name}_full_report.md", result)


def write_summary(path: Path, report: FullReport) -> None:
    lines = ["# 全片摘要", "", report.overall_summary_300_words, "", "## 10 個重點", ""]
    lines.extend(f"{index}. {point}" for index, point in enumerate(report.top_10_points, start=1))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_timeline(path: Path, segments: list[SegmentAnalysis]) -> None:
    timeline = []
    for segment in segments:
        for event in segment.key_events:
            timeline.append(
                {
                    "segment_index": segment.segment_index,
                    "timestamp": event.timestamp,
                    "event": event.event,
                    "news_value": event.news_value,
                }
            )
    write_json(path, timeline)


def write_short_video_ideas(path: Path, report: FullReport) -> None:
    lines = ["# 可剪短影音片段", ""]
    for index, item in enumerate(report.best_5_short_video_segments, start=1):
        lines.extend(
            [
                f"## {index}. {item.suggested_title}",
                "",
                f"- 起訖時間：{item.start_time} - {item.end_time}",
                f"- 適合平台：{item.platform}",
                f"- 原因：{item.reason}",
                "",
            ]
        )
    lines.extend(["## 短影音 Hook 建議", ""])
    lines.extend(f"{index}. {hook}" for index, hook in enumerate(report.short_video_hooks, start=1))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_news_draft(path: Path, report: FullReport) -> None:
    lines = [
        "# 新聞稿初稿",
        "",
        report.news_draft,
        "",
        "## 新聞標題建議",
        "",
    ]
    lines.extend(f"{index}. {title}" for index, title in enumerate(report.news_title_suggestions, start=1))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_full_report(path: Path, result: AnalysisResult) -> None:
    video = result.video_info
    report = result.full_report
    lines = [
        "# AI Video News Analyzer Report",
        "",
        "## 基本資訊",
        f"- 檔名：{video.file_name}",
        f"- 影片長度：{video.duration}",
        f"- 分析時間：{result.analyzed_at}",
        f"- 使用模型：{result.model}",
        "",
        "## 全片摘要",
        "",
        report.overall_summary_300_words,
        "",
        "## 時間軸重點",
        "",
        "| 時間 | 重點 | 新聞價值 |",
        "|---|---|---|",
    ]
    for segment in result.segments:
        for event in segment.key_events:
            lines.append(f"| {event.timestamp} | {event.event} | {event.news_value} |")

    lines.extend(["", "## 重要發言", "", "| 時間 | 說話者 | 內容 |", "|---|---|---|"])
    for segment in result.segments:
        for quote in segment.important_quotes:
            lines.append(f"| {quote.timestamp} | {quote.speaker} | {quote.quote} |")

    lines.extend(["", "## 可剪短影音片段", "", "| 起訖時間 | 建議標題 | 適合平台 | 原因 |", "|---|---|---|---|"])
    for item in report.best_5_short_video_segments:
        lines.append(f"| {item.start_time} - {item.end_time} | {item.suggested_title} | {item.platform} | {item.reason} |")

    lines.extend(["", "## 新聞標題建議", ""])
    lines.extend(f"{index}. {title}" for index, title in enumerate(report.news_title_suggestions, start=1))
    lines.extend(["", "## 短影音 Hook", ""])
    lines.extend(f"{index}. {hook}" for index, hook in enumerate(report.short_video_hooks, start=1))
    lines.extend(["", "## 新聞稿初稿", "", report.news_draft])
    lines.extend(["", "## 旁白稿初稿", "", report.voiceover_draft])
    lines.extend(["", "## 需要人工查證事項", ""])
    lines.extend(f"- {item}" for item in report.fact_check_items)

    if result.errors:
        lines.extend(["", "## 片段分析錯誤", ""])
        for error in result.errors:
            lines.append(f"- part {error.segment_index} ({error.start_time}-{error.end_time})：{error.error}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
