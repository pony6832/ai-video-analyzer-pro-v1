from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from pydantic import ValidationError
from rich.console import Console

from app.config import PROJECT_ROOT, Settings
from app.gemini_client import GeminiClient, GeminiClientError
from app.report_writer import write_json
from app.schemas import (
    ChapterMapItem,
    ClipCandidate,
    GoldminePackage,
    HighlightRankingItem,
    PipelineSettings,
    QuoteCandidate,
    ReviewDecision,
    SocialTitleCandidate,
    TimelineEvent,
    TranscriptSegment,
    VideoJob,
    WorkbenchAnalysis,
    WorkbenchSegmentAnalysis,
)
from app.video_utils import (
    VideoProcessingError,
    clamp_seconds,
    create_clip,
    create_proxy_video,
    extract_audio,
    get_video_info,
    hhmmss_to_seconds,
    seconds_to_timecode,
    split_video,
)


DATA_DIR = PROJECT_ROOT / "data"
JOBS_DIR = DATA_DIR / "jobs"
JOB_FILE = "job.json"
ANALYSIS_FILE = "analysis.json"


WORKBENCH_SEGMENT_PROMPT = """你是資深影片內容企劃、新聞編輯、短影音製作人與事實查核助理。

請分析這段影片片段，輸出必須是 JSON，且必須符合指定 schema。

片段資訊：
- segment_index: {segment_index}
- absolute_start_time: {start_time}
- absolute_end_time: {end_time}

任務：
1. 先辨識段落、說話者、語意轉折與情緒轉折，再輸出結構化結果。
2. 摘要這段影片的核心內容，避免泛泛而談。
3. 整理逐字稿段落，包含 speaker、start_time、end_time、text、confidence。
4. 找出重要事件、畫面資訊、新聞價值與需要人工確認的風險。
5. 找出可做短影音的候選段，包含 suggested_title、hook、cover_text、platform、reason、source_text。
6. 為每個短影音候選給 0-100 的 score，依金句強度、情緒張力、資訊密度、社群標題潛力、畫面可用性與風險評分。
7. 找出可當標題或預告使用的金句，必須附 reason、source_text、confidence 與 score。
8. 提供章節與剪輯建議，包含 B-roll、Close-up、Jump cut、字幕強調或音效轉場。

限制：
- 所有內容使用台灣繁體中文。
- 所有 timecode 必須盡量使用全片絕對時間 HH:MM:SS.mmm；無法精準到毫秒時仍用 HH:MM:SS.000。
- 若只能判斷片段內相對時間，仍需輸出最接近的時間點，並在 risk_notes 註記「需人工微調 timecode」。
- 每個 clip_candidates 與 quote_candidates 都要有唯一 id，例如 clip-001、quote-001。
- 每個 clip_candidates 都必須包含 score、confidence、source_text 與 risk_notes；沒有風險時 risk_notes 用空陣列。
- 短影音候選以 15-90 秒為優先；若超過範圍，必須在 reason 說明必要性。
- 不要編造看不到、聽不到或不能從影片判斷的內容。
- 不確定、需查證、可能斷章取義或可能誤導的內容，放入 risk_notes。
- 只輸出 JSON，不要加 Markdown，不要加說明文字。
"""


GOLDMINE_PACKAGE_PROMPT = """你是長影片短影音企劃總編輯。

以下是逐段影片分析 JSON。請做第二層全片彙整與第三層社群包裝，輸出必須符合 schema。

任務：
1. refined_summary：用台灣繁體中文寫出全片重點摘要，聚焦可製作短影音的內容。
2. chapter_map：依全片主題整理章節地圖，標示 start_time、end_time、title、summary、short_video_value。
3. highlight_rankings：從 clip_candidates 中挑出最值得剪的片段並排序，保留 clip_id、起訖、標題、score、reason、risk_notes。
4. social_title_pack：產生可直接給社群小編使用的標題包，包含 platform、angle、related_clip_id。
5. 不要編造片段分析沒有根據的內容；不確定與可能斷章取義的內容放入 risk_notes。
6. 只輸出 JSON，不要 Markdown。

逐段分析 JSON：
{segments_json}
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def safe_stem(value: str) -> str:
    stem = Path(value).stem if not is_url(value) else urlparse(value).netloc
    stem = re.sub(r"[^\w.\-()\u4e00-\u9fff]+", "_", stem, flags=re.UNICODE).strip("._")
    return stem or "video"


class WorkbenchStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.jobs_dir = JOBS_DIR
        self.outputs_dir = settings.outputs_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def output_dir(self, job_id: str) -> Path:
        path = self.outputs_dir / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_job(self, source: str, settings: PipelineSettings) -> VideoJob:
        job_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        job = VideoJob(
            id=job_id,
            source_type="url" if is_url(source) else "file",
            source=source,
            title=safe_stem(source),
            created_at=now_iso(),
            updated_at=now_iso(),
            settings=settings,
        )
        self.save_job(job)
        return job

    def save_job(self, job: VideoJob) -> None:
        job.updated_at = now_iso()
        path = self.job_dir(job.id) / JOB_FILE
        write_json(path, job)

    def load_job(self, job_id: str) -> VideoJob:
        path = self.job_dir(job_id) / JOB_FILE
        if not path.exists():
            raise FileNotFoundError(f"找不到 job：{job_id}")
        return VideoJob.model_validate_json(path.read_text(encoding="utf-8"))

    def list_jobs(self) -> list[VideoJob]:
        jobs: list[VideoJob] = []
        for path in sorted(self.jobs_dir.glob(f"*/{JOB_FILE}"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                jobs.append(VideoJob.model_validate_json(path.read_text(encoding="utf-8")))
            except (ValidationError, OSError):
                continue
        return jobs

    def analysis_path(self, job_id: str) -> Path:
        return self.output_dir(job_id) / ANALYSIS_FILE

    def save_analysis(self, analysis: WorkbenchAnalysis) -> None:
        write_json(self.analysis_path(analysis.job.id), analysis)

    def load_analysis(self, job_id: str) -> WorkbenchAnalysis:
        path = self.analysis_path(job_id)
        if not path.exists():
            raise FileNotFoundError(f"找不到分析結果：{job_id}")
        return WorkbenchAnalysis.model_validate_json(path.read_text(encoding="utf-8"))

    def update_clip_review(self, job_id: str, clip_id: str, decision: ReviewDecision) -> WorkbenchAnalysis:
        analysis = self.load_analysis(job_id)
        for clip in analysis.clip_candidates:
            if clip.id == clip_id:
                decision.updated_at = now_iso()
                clip.review = decision
                clip.review_status = decision.status
                self.save_analysis(analysis)
                self._write_edit_decision_list(analysis)
                return analysis
        raise FileNotFoundError(f"找不到短片候選：{clip_id}")

    def _write_edit_decision_list(self, analysis: WorkbenchAnalysis) -> Path:
        path = self.output_dir(analysis.job.id) / "edit_decision_list.json"
        items = []
        for clip in self._approved_clips(analysis):
            review = clip.review
            start_time = review.updated_start_time or clip.start_time
            end_time = review.updated_end_time or clip.end_time
            items.append(
                {
                    "id": clip.id,
                    "start_time": start_time,
                    "end_time": end_time,
                    "start_seconds": self._seconds_or_none(start_time),
                    "end_seconds": self._seconds_or_none(end_time),
                    "duration_seconds": clip.duration_seconds,
                    "title": review.updated_title or clip.suggested_title,
                    "hook": review.updated_hook or clip.hook,
                    "cover_text": clip.cover_text,
                    "platform": clip.platform,
                    "score": clip.score,
                    "confidence": clip.confidence,
                    "source_text": clip.source_text,
                    "risk_notes": clip.risk_notes,
                    "review_status": review.status,
                    "notes": review.notes,
                }
            )
        write_json(path, items)
        return path

    def _seconds_or_none(self, value: str) -> float | None:
        try:
            return hhmmss_to_seconds(value)
        except ValueError:
            return None

    def _approved_clips(self, analysis: WorkbenchAnalysis) -> list[ClipCandidate]:
        return [
            clip
            for clip in analysis.clip_candidates
            if clip.review.status in {"kept", "revised"} or clip.review_status in {"kept", "revised"}
        ]


class WorkbenchPipeline:
    def __init__(self, settings: Settings, console: Console | None = None) -> None:
        self.settings = settings
        self.console = console or Console()
        self.store = WorkbenchStore(settings)

    def analyze_source(
        self,
        source: str,
        chunk_minutes: int = 10,
        profile_id: str = "production_director",
        create_draft_clips: bool = True,
        force: bool = False,
    ) -> WorkbenchAnalysis:
        job = self.store.create_job(
            source=source,
            settings=PipelineSettings(
                chunk_minutes=chunk_minutes,
                profile_id=profile_id,
                create_draft_clips=create_draft_clips,
            ),
        )
        try:
            self.import_source(job)
            self.preprocess(job)
            analysis = self.analyze_job(job.id, force=force)
            if self._analysis_has_no_ai_results(analysis):
                message = "所有片段皆分析失敗，未產生逐字稿、金句或切片；請檢查 API 配額、模型或稍後重試。"
                analysis.job.status = "failed"
                analysis.job.error = message
                self.store.save_analysis(analysis)
                self.store.save_job(analysis.job)
                raise RuntimeError(message)
            self.export_review_package(job.id, create_draft_clips=create_draft_clips)
            return analysis
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            self.store.save_job(job)
            raise

    def import_source(self, job: VideoJob) -> VideoJob:
        self.settings.ensure_directories()
        if job.source_type == "url":
            video_path = self._download_url(job.source, job.id)
        else:
            video_path = self._import_file(Path(job.source))
        job.video_path = str(video_path.resolve())
        job.title = video_path.stem
        job.status = "imported"
        self.store.save_job(job)
        return job

    def preprocess(self, job: VideoJob) -> VideoJob:
        video_path = self._require_video_path(job)
        work_dir = self.store.job_dir(job.id)
        temp_dir = self.settings.temp_dir / "jobs" / job.id
        output_dir = self.store.output_dir(job.id)
        work_dir.mkdir(parents=True, exist_ok=True)
        temp_dir.mkdir(parents=True, exist_ok=True)

        video_info = get_video_info(video_path)
        job.video_info = video_info
        split_video(video_path, temp_dir / "segments", job.settings.chunk_minutes, video_info.duration_seconds)
        try:
            extract_audio(video_path, temp_dir / "audio.wav")
        except VideoProcessingError as exc:
            self.console.print(f"[yellow]音訊抽取失敗，繼續影片分析：{exc}[/yellow]")
        try:
            proxy = create_proxy_video(video_path, output_dir / "proxy.mp4")
            job.outputs["proxy_video"] = str(proxy)
        except VideoProcessingError as exc:
            self.console.print(f"[yellow]proxy 影片產生失敗，繼續影片分析：{exc}[/yellow]")
        job.status = "preprocessed"
        self.store.save_job(job)
        return job

    def analyze_job(self, job_id: str, force: bool = False) -> WorkbenchAnalysis:
        job = self.store.load_job(job_id)
        video_path = self._require_video_path(job)
        api_key = self.settings.require_api_key()
        client = GeminiClient(
            api_key=api_key,
            model=self.settings.gemini_model,
            fallback_models=self.settings.fallback_models_list(),
        )
        video_info = job.video_info or get_video_info(video_path)
        segment_dir = self.settings.temp_dir / "jobs" / job.id / "segments"
        segments = split_video(video_path, segment_dir, job.settings.chunk_minutes, video_info.duration_seconds)
        segment_reports: list[WorkbenchSegmentAnalysis] = []
        risk_notes: list[str] = []

        job.status = "analyzing"
        job.video_info = video_info
        self.store.save_job(job)

        for segment in segments:
            cache_path = self.store.job_dir(job.id) / "cache" / f"segment_{segment.index:03d}.json"
            if cache_path.exists() and not force:
                try:
                    segment_reports.append(WorkbenchSegmentAnalysis.model_validate_json(cache_path.read_text(encoding="utf-8")))
                    self.console.print(f"[green]使用 workbench cache：{cache_path.name}[/green]")
                    continue
                except (ValidationError, OSError) as exc:
                    self.console.print(f"[yellow]cache 讀取失敗，重新分析：{exc}[/yellow]")

            prompt = WORKBENCH_SEGMENT_PROMPT.format(
                segment_index=segment.index,
                start_time=segment.start_time,
                end_time=segment.end_time,
            )
            try:
                self.console.print(f"[cyan]workbench 分析片段 {segment.index}/{len(segments)}[/cyan]")
                report = client.analyze_video_file(segment.path, prompt, WorkbenchSegmentAnalysis)
                self._normalize_segment_report(
                    report,
                    segment.index,
                    segment_start_seconds=segment.start_seconds,
                    segment_end_seconds=segment.end_seconds,
                    video_duration_seconds=video_info.duration_seconds,
                )
                write_json(cache_path, report)
                segment_reports.append(report)
            except (GeminiClientError, ValidationError, json.JSONDecodeError, OSError) as exc:
                message = f"片段 {segment.index} ({segment.start_time}-{segment.end_time}) 分析失敗：{exc}"
                risk_notes.append(message)
                self.console.print(f"[red]{message}[/red]")

        job.status = "failed" if not segment_reports else "ready_for_review"
        job.outputs["analysis"] = str(self.store.analysis_path(job.id))
        analysis = self._combine(job, client, client.last_model_used or self.settings.gemini_model, segment_reports, risk_notes)
        analysis.job = job
        self.store.save_analysis(analysis)
        self.store.save_job(job)
        return analysis

    def export_review_package(self, job_id: str, create_draft_clips: bool | None = None) -> dict[str, Path]:
        analysis = self.store.load_analysis(job_id)
        job = analysis.job
        if self._analysis_has_no_ai_results(analysis):
            job.status = "failed"
            job.error = "所有片段皆分析失敗，沒有可匯出的 AI 分析結果。"
            analysis.job = job
            self.store.save_analysis(analysis)
            self.store.save_job(job)
            raise RuntimeError(job.error)
        video_path = self._require_video_path(job)
        output_dir = self.store.output_dir(job.id)
        outputs: dict[str, Path] = {}

        outputs["analysis"] = output_dir / "analysis.json"
        write_json(output_dir / "timeline.json", analysis.timeline_events)
        outputs["timeline"] = output_dir / "timeline.json"
        write_json(output_dir / "clip_candidates.json", analysis.clip_candidates)
        outputs["clip_candidates"] = output_dir / "clip_candidates.json"
        write_json(output_dir / "quotes.json", analysis.quote_candidates)
        outputs["quotes"] = output_dir / "quotes.json"
        write_json(output_dir / "chapter_map.json", analysis.chapter_map)
        outputs["chapter_map"] = output_dir / "chapter_map.json"
        write_json(output_dir / "highlight_rankings.json", analysis.highlight_rankings)
        outputs["highlight_rankings"] = output_dir / "highlight_rankings.json"
        write_json(output_dir / "social_title_pack.json", analysis.social_title_pack)
        outputs["social_title_pack"] = output_dir / "social_title_pack.json"
        outputs["subtitles"] = self._write_srt(output_dir / "subtitles.srt", analysis.transcript_segments)
        outputs["edit_decision_list"] = self.store._write_edit_decision_list(analysis)
        outputs["full_report"] = self._write_full_report(output_dir / "full_report.md", analysis)

        should_create_clips = job.settings.create_draft_clips if create_draft_clips is None else create_draft_clips
        if should_create_clips:
            draft_dir = output_dir / "draft_clips"
            draft_dir.mkdir(parents=True, exist_ok=True)
            approved_clips = self.store._approved_clips(analysis)
            for index, clip in enumerate(approved_clips[: job.settings.max_draft_clips], start=1):
                try:
                    review = clip.review
                    title = safe_stem(clip.review.updated_title or clip.suggested_title)[:48] or clip.id
                    path = create_clip(
                        video_path,
                        draft_dir / f"{index:02d}_{clip.id}_{title}.mp4",
                        review.updated_start_time or clip.start_time,
                        review.updated_end_time or clip.end_time,
                    )
                    outputs[f"draft_clip_{index:02d}"] = path
                except (ValueError, VideoProcessingError) as exc:
                    analysis.risk_notes.append(f"{clip.id} 短片草稿產生失敗，需要人工確認：{exc}")
            self.store.save_analysis(analysis)

        job.status = "exported"
        job.outputs.update({key: str(value) for key, value in outputs.items()})
        analysis.job = job
        self.store.save_analysis(analysis)
        self.store.save_job(job)
        return outputs

    def _analysis_has_no_ai_results(self, analysis: WorkbenchAnalysis) -> bool:
        return (
            not analysis.transcript_segments
            and not analysis.timeline_events
            and not analysis.quote_candidates
            and not analysis.clip_candidates
            and bool(analysis.risk_notes)
        )

    def _combine(
        self,
        job: VideoJob,
        client: GeminiClient,
        model: str,
        reports: list[WorkbenchSegmentAnalysis],
        risk_notes: list[str],
    ) -> WorkbenchAnalysis:
        transcript_segments: list[TranscriptSegment] = []
        timeline_events: list[TimelineEvent] = []
        quote_candidates: list[QuoteCandidate] = []
        clip_candidates: list[ClipCandidate] = []
        chapter_suggestions = []
        editing_suggestions = []

        for report in reports:
            transcript_segments.extend(report.transcript_segments)
            for event in report.timeline_events:
                event.source_segment_index = event.source_segment_index or report.segment_index
                timeline_events.append(event)
            for quote in report.quote_candidates:
                quote.source_segment_index = quote.source_segment_index or report.segment_index
                quote_candidates.append(quote)
            for clip in report.clip_candidates:
                clip.source_segment_index = clip.source_segment_index or report.segment_index
                clip.duration_seconds = clip.duration_seconds or self._clip_duration(clip)
                clip.score = clip.score or self._score_clip(clip)
                clip.review_status = clip.review.status
                clip_candidates.append(clip)
            chapter_suggestions.extend(report.chapter_suggestions)
            editing_suggestions.extend(report.editing_suggestions)
            risk_notes.extend(report.risk_notes)

        summary = "\n\n".join(report.summary for report in reports).strip()
        if not summary:
            summary = "所有片段皆分析失敗，無法產生摘要。"
        top_points = [event.title for event in timeline_events[:10]]
        title_suggestions = [clip.suggested_title for clip in clip_candidates[:8]]
        hooks = [clip.hook for clip in clip_candidates if clip.hook][:10]
        goldmine = self._build_goldmine_package(client, reports, clip_candidates, timeline_events, risk_notes)
        return WorkbenchAnalysis(
            job=job,
            model=model,
            analyzed_at=now_iso(),
            overall_summary=goldmine.refined_summary or summary,
            top_points=top_points,
            title_suggestions=title_suggestions,
            youtube_title_suggestions=title_suggestions[:5],
            short_video_hooks=hooks,
            transcript_segments=transcript_segments,
            timeline_events=timeline_events,
            quote_candidates=quote_candidates,
            clip_candidates=clip_candidates,
            chapter_map=goldmine.chapter_map,
            highlight_rankings=goldmine.highlight_rankings,
            social_title_pack=goldmine.social_title_pack,
            chapter_suggestions=chapter_suggestions,
            editing_suggestions=editing_suggestions,
            risk_notes=self._unique_notes([*risk_notes, *goldmine.risk_notes]),
        )

    def _unique_notes(self, notes: list[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for note in notes:
            if note and note not in seen:
                unique.append(note)
                seen.add(note)
        return unique

    def _normalize_segment_report(
        self,
        report: WorkbenchSegmentAnalysis,
        segment_index: int,
        segment_start_seconds: float = 0.0,
        segment_end_seconds: float | None = None,
        video_duration_seconds: float | None = None,
    ) -> None:
        for transcript in report.transcript_segments:
            transcript.start_seconds, transcript.start_time = self._normalize_timecode(
                transcript.start_time,
                segment_start_seconds,
                video_duration_seconds,
            )
            transcript.end_seconds, transcript.end_time = self._normalize_timecode(
                transcript.end_time,
                segment_start_seconds,
                video_duration_seconds,
            )
            if transcript.end_seconds <= transcript.start_seconds:
                fallback_end = segment_end_seconds or transcript.start_seconds + 5
                transcript.end_seconds = clamp_seconds(fallback_end, video_duration_seconds)
                transcript.end_time = seconds_to_timecode(transcript.end_seconds)
            transcript.confidence = transcript.confidence if transcript.confidence is not None else 0.75

        for event in report.timeline_events:
            event.start_seconds, event.start_time = self._normalize_timecode(
                event.start_time,
                segment_start_seconds,
                video_duration_seconds,
            )
            if event.end_time:
                event.end_seconds, event.end_time = self._normalize_timecode(
                    event.end_time,
                    segment_start_seconds,
                    video_duration_seconds,
                )
            event.confidence = event.confidence if event.confidence is not None else 0.75
            event.source_segment_index = event.source_segment_index or segment_index

        for index, quote in enumerate(report.quote_candidates, start=1):
            if not quote.id:
                quote.id = f"s{segment_index:03d}-quote-{index:03d}"
            quote.start_seconds, quote.timecode = self._normalize_timecode(
                quote.timecode,
                segment_start_seconds,
                video_duration_seconds,
            )
            quote.score = quote.score or self._score_quote(quote)
            quote.confidence = quote.confidence if quote.confidence is not None else 0.75
            quote.source_segment_index = quote.source_segment_index or segment_index
        for index, clip in enumerate(report.clip_candidates, start=1):
            if not clip.id:
                clip.id = f"s{segment_index:03d}-clip-{index:03d}"
            clip.start_seconds, clip.start_time = self._normalize_timecode(
                clip.start_time,
                segment_start_seconds,
                video_duration_seconds,
            )
            clip.end_seconds, clip.end_time = self._normalize_timecode(
                clip.end_time,
                segment_start_seconds,
                video_duration_seconds,
            )
            if clip.end_seconds <= clip.start_seconds:
                clip.end_seconds = clamp_seconds(clip.start_seconds + 30, video_duration_seconds)
                clip.end_time = seconds_to_timecode(clip.end_seconds)
                clip.risk_notes.append("需要人工確認：切片結束時間早於或等於開始時間，已自動補成 30 秒。")
            clip.duration_seconds = self._clip_duration(clip)
            clip.score = clip.score or self._score_clip(clip)
            clip.confidence = clip.confidence if clip.confidence is not None else 0.75
            self._flag_clip_timing_risks(clip)
            clip.review_status = clip.review.status
            clip.source_segment_index = clip.source_segment_index or segment_index

        for chapter in report.chapter_suggestions:
            if chapter.start_time:
                _, chapter.start_time = self._normalize_timecode(
                    chapter.start_time,
                    segment_start_seconds,
                    video_duration_seconds,
                )
            if chapter.end_time:
                _, chapter.end_time = self._normalize_timecode(
                    chapter.end_time,
                    segment_start_seconds,
                    video_duration_seconds,
                )

        for suggestion in report.editing_suggestions:
            if suggestion.timecode:
                _, suggestion.timecode = self._normalize_timecode(
                    suggestion.timecode,
                    segment_start_seconds,
                    video_duration_seconds,
                )

    def _normalize_timecode(
        self,
        value: str,
        segment_start_seconds: float = 0.0,
        video_duration_seconds: float | None = None,
    ) -> tuple[float, str]:
        try:
            seconds = hhmmss_to_seconds(value)
        except (TypeError, ValueError):
            seconds = segment_start_seconds
        if segment_start_seconds and seconds < segment_start_seconds:
            seconds += segment_start_seconds
        seconds = clamp_seconds(seconds, video_duration_seconds)
        return seconds, seconds_to_timecode(seconds)

    def _flag_clip_timing_risks(self, clip: ClipCandidate) -> None:
        if clip.duration_seconds is None:
            return
        if clip.duration_seconds < 15:
            note = "需要人工確認：切片短於 15 秒，可能不夠形成完整敘事。"
        elif clip.duration_seconds > 90:
            note = "需要人工確認：切片長於 90 秒，發布前建議再縮短。"
        else:
            return
        if note not in clip.risk_notes:
            clip.risk_notes.append(note)

    def _score_quote(self, quote: QuoteCandidate) -> int:
        score = 45
        if quote.quote:
            score += min(20, len(quote.quote) // 4)
        if quote.emotion_tone:
            score += 10
        if quote.usage:
            score += 10
        if quote.reason:
            score += 10
        return max(0, min(100, score))

    def _score_clip(self, clip: ClipCandidate) -> int:
        score = 45
        if clip.hook:
            score += 15
        if clip.cover_text:
            score += 10
        if clip.reason:
            score += 10
        if clip.duration_seconds is not None:
            if 15 <= clip.duration_seconds <= 90:
                score += 15
            elif clip.duration_seconds > 180:
                score -= 15
        if clip.risk_notes:
            score -= min(20, len(clip.risk_notes) * 8)
        return max(0, min(100, score))

    def _build_goldmine_package(
        self,
        client: GeminiClient,
        reports: list[WorkbenchSegmentAnalysis],
        clip_candidates: list[ClipCandidate],
        timeline_events: list[TimelineEvent],
        risk_notes: list[str],
    ) -> GoldminePackage:
        if not reports:
            return GoldminePackage(
                refined_summary="所有片段皆分析失敗，無法產生淘金摘要。",
                risk_notes=risk_notes,
            )

        segments_json = json.dumps([item.model_dump(mode="json") for item in reports], ensure_ascii=False, indent=2)
        try:
            self.console.print("[cyan]產生淘金彙整與社群包裝[/cyan]")
            package = client.generate_json_from_text(
                GOLDMINE_PACKAGE_PROMPT.format(segments_json=segments_json),
                GoldminePackage,
            )
        except (GeminiClientError, ValidationError, json.JSONDecodeError) as exc:
            self.console.print(f"[yellow]淘金彙整失敗，改用本機排序：{exc}[/yellow]")
            package = self._fallback_goldmine_package(reports, clip_candidates, timeline_events, [*risk_notes, str(exc)])
        return self._normalize_goldmine_package(package, clip_candidates, timeline_events)

    def _fallback_goldmine_package(
        self,
        reports: list[WorkbenchSegmentAnalysis],
        clip_candidates: list[ClipCandidate],
        timeline_events: list[TimelineEvent],
        risk_notes: list[str],
    ) -> GoldminePackage:
        ranked_clips = sorted(clip_candidates, key=lambda clip: clip.score, reverse=True)
        return GoldminePackage(
            refined_summary="\n\n".join(report.summary for report in reports).strip() or "沒有足夠資料產生摘要。",
            chapter_map=[
                ChapterMapItem(
                    start_time=event.start_time,
                    end_time=event.end_time or event.start_time,
                    title=event.title,
                    summary=event.summary,
                    short_video_value=event.news_value,
                )
                for event in timeline_events[:12]
            ],
            highlight_rankings=[
                HighlightRankingItem(
                    rank=index,
                    clip_id=clip.id,
                    start_time=clip.start_time,
                    end_time=clip.end_time,
                    suggested_title=clip.suggested_title,
                    score=clip.score,
                    reason=clip.reason,
                    risk_notes=clip.risk_notes,
                )
                for index, clip in enumerate(ranked_clips[:12], start=1)
            ],
            social_title_pack=[
                SocialTitleCandidate(
                    title=clip.suggested_title,
                    platform=clip.platform,
                    angle=clip.hook or clip.reason,
                    related_clip_id=clip.id,
                )
                for clip in ranked_clips[:12]
            ],
            risk_notes=risk_notes,
        )

    def _normalize_goldmine_package(
        self,
        package: GoldminePackage,
        clip_candidates: list[ClipCandidate],
        timeline_events: list[TimelineEvent],
    ) -> GoldminePackage:
        clips_by_id = {clip.id: clip for clip in clip_candidates}
        if not package.highlight_rankings:
            package.highlight_rankings = self._fallback_goldmine_package([], clip_candidates, timeline_events, []).highlight_rankings
        for index, item in enumerate(package.highlight_rankings, start=1):
            item.rank = item.rank or index
            clip = clips_by_id.get(item.clip_id)
            if clip:
                item.start_time = clip.start_time
                item.end_time = clip.end_time
                item.score = item.score or clip.score
                item.risk_notes = item.risk_notes or clip.risk_notes
        if not package.chapter_map:
            package.chapter_map = [
                ChapterMapItem(
                    start_time=event.start_time,
                    end_time=event.end_time or event.start_time,
                    title=event.title,
                    summary=event.summary,
                    short_video_value=event.news_value,
                )
                for event in timeline_events[:12]
            ]
        for chapter in package.chapter_map:
            try:
                chapter.start_seconds = hhmmss_to_seconds(chapter.start_time)
                chapter.start_time = seconds_to_timecode(chapter.start_seconds)
                chapter.end_seconds = hhmmss_to_seconds(chapter.end_time)
                chapter.end_time = seconds_to_timecode(chapter.end_seconds)
            except ValueError:
                chapter.confidence = min(chapter.confidence or 0.7, 0.4)
            chapter.confidence = chapter.confidence if chapter.confidence is not None else 0.75
        if not package.social_title_pack:
            package.social_title_pack = [
                SocialTitleCandidate(
                    title=clip.suggested_title,
                    platform=clip.platform,
                    angle=clip.hook or clip.reason,
                    related_clip_id=clip.id,
                )
                for clip in sorted(clip_candidates, key=lambda item: item.score, reverse=True)[:12]
            ]
        for title in package.social_title_pack:
            if title.related_clip_id and title.related_clip_id in clips_by_id:
                clip = clips_by_id[title.related_clip_id]
                title.score = title.score or clip.score
                title.confidence = title.confidence if title.confidence is not None else clip.confidence
                title.reason = title.reason or clip.reason
            title.confidence = title.confidence if title.confidence is not None else 0.75
        return package

    def _clip_duration(self, clip: ClipCandidate) -> float | None:
        try:
            return max(0.0, hhmmss_to_seconds(clip.end_time) - hhmmss_to_seconds(clip.start_time))
        except ValueError:
            return None

    def _write_srt(self, path: Path, segments: list[TranscriptSegment]) -> Path:
        lines: list[str] = []
        for index, segment in enumerate(segments, start=1):
            try:
                start = self._srt_time(segment.start_time)
                end = self._srt_time(segment.end_time)
            except ValueError:
                continue
            lines.extend(
                [
                    str(index),
                    f"{start} --> {end}",
                    f"{segment.speaker}: {segment.text}" if segment.speaker != "unknown" else segment.text,
                    "",
                ]
            )
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _srt_time(self, value: str) -> str:
        seconds = hhmmss_to_seconds(value)
        total_ms = int(round(seconds * 1000))
        hours = total_ms // 3_600_000
        minutes = (total_ms % 3_600_000) // 60_000
        secs = (total_ms % 60_000) // 1000
        ms = total_ms % 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    def _write_full_report(self, path: Path, analysis: WorkbenchAnalysis) -> Path:
        job = analysis.job
        lines = [
            "# AI 影片分析與短影音企劃報告",
            "",
            "## 基本資訊",
            f"- Job ID：{job.id}",
            f"- 來源：{job.source}",
            f"- 影片：{job.video_path or '未匯入'}",
            f"- 使用模型：{analysis.model}",
            f"- 分析時間：{analysis.analyzed_at}",
            "",
            "## 全片摘要",
            "",
            analysis.overall_summary,
            "",
            "## 重點 Timecode",
            "",
            "| 時間 | 重點 | 摘要 | 新聞價值 |",
            "|---|---|---|---|",
        ]
        for event in analysis.timeline_events:
            timecode = event.start_time if not event.end_time else f"{event.start_time}-{event.end_time}"
            lines.append(f"| {timecode} | {event.title} | {event.summary} | {event.news_value} |")

        lines.extend(["", "## 章節地圖", "", "| 起訖 | 章節 | 摘要 | 短影音價值 |", "|---|---|---|---|"])
        for chapter in analysis.chapter_map:
            lines.append(
                f"| {chapter.start_time}-{chapter.end_time} | {chapter.title} | "
                f"{chapter.summary} | {chapter.short_video_value} |"
            )

        lines.extend(["", "## 金句", "", "| 時間 | 分數 | 信心 | 說話者 | 金句 | 用途 | 依據 |", "|---|---:|---:|---|---|---|---|"])
        for quote in analysis.quote_candidates:
            confidence = "" if quote.confidence is None else f"{quote.confidence:.2f}"
            lines.append(
                f"| {quote.timecode} | {quote.score} | {confidence} | {quote.speaker} | "
                f"{quote.quote} | {quote.usage} | {quote.reason or quote.source_text} |"
            )

        lines.extend(["", "## 精彩片段排行榜", "", "| 排名 | 起訖 | 分數 | 標題 | 理由 | 風險 |", "|---|---|---:|---|---|---|"])
        for item in analysis.highlight_rankings:
            lines.append(
                f"| {item.rank} | {item.start_time}-{item.end_time} | {item.score} | "
                f"{item.suggested_title} | {item.reason} | {'；'.join(item.risk_notes)} |"
            )

        lines.extend(["", "## 短影音候選", "", "| 起訖 | 長度 | 分數 | 信心 | 標題 | Hook | 封面字 | 平台 | 審稿 | 依據 |", "|---|---:|---:|---:|---|---|---|---|---|---|"])
        for clip in analysis.clip_candidates:
            confidence = "" if clip.confidence is None else f"{clip.confidence:.2f}"
            duration = "" if clip.duration_seconds is None else f"{clip.duration_seconds:.1f}s"
            lines.append(
                f"| {clip.start_time}-{clip.end_time} | {duration} | {clip.score} | {confidence} | "
                f"{clip.suggested_title} | {clip.hook} | {clip.cover_text} | {clip.platform} | "
                f"{clip.review_status} | {clip.reason or clip.source_text} |"
            )

        lines.extend(["", "## 社群標題包", "", "| 平台 | 分數 | 信心 | 標題 | 角度 | 片段 | 理由 |", "|---|---:|---:|---|---|---|---|"])
        for title in analysis.social_title_pack:
            confidence = "" if title.confidence is None else f"{title.confidence:.2f}"
            lines.append(
                f"| {title.platform} | {title.score} | {confidence} | {title.title} | "
                f"{title.angle} | {title.related_clip_id or ''} | {title.reason} |"
            )

        lines.extend(["", "## 章節建議", ""])
        for index, chapter in enumerate(analysis.chapter_suggestions, start=1):
            lines.append(f"{index}. {chapter.start_time}-{chapter.end_time}｜{chapter.chapter_name}｜{chapter.main_title}")

        lines.extend(["", "## 剪輯建議", ""])
        for suggestion in analysis.editing_suggestions:
            lines.append(f"- {suggestion.timecode}｜{suggestion.suggestion_type}：{suggestion.description}（{suggestion.reason}）")

        lines.extend(["", "## 需要人工確認", ""])
        lines.extend(f"- {note}" for note in analysis.risk_notes)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _import_file(self, source: Path) -> Path:
        if not source.exists():
            raise FileNotFoundError(f"找不到影片檔：{source}")
        target = self.settings.videos_dir / source.name
        if source.resolve() == target.resolve():
            return source
        if target.exists():
            target = target.with_name(f"{target.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{target.suffix}")
        shutil.copy2(source, target)
        return target

    def _download_url(self, url: str, job_id: str) -> Path:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise RuntimeError("YouTube/直播連結匯入需要安裝 yt-dlp：python -m pip install yt-dlp") from exc

        target_template = str((self.settings.videos_dir / f"{job_id}_%(title).120s.%(ext)s").resolve())
        options = {
            "outtmpl": target_template,
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
        }
        before = set(self.settings.videos_dir.glob(f"{job_id}_*"))
        with YoutubeDL(options) as downloader:
            downloader.download([url])
        after = set(self.settings.videos_dir.glob(f"{job_id}_*"))
        created = sorted(after - before, key=lambda item: item.stat().st_mtime, reverse=True)
        if not created:
            created = sorted(self.settings.videos_dir.glob(f"{job_id}_*"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not created:
            raise RuntimeError("URL 匯入完成但找不到下載後的影片檔。")
        return created[0]

    def _require_video_path(self, job: VideoJob) -> Path:
        if not job.video_path:
            raise ValueError(f"job 尚未匯入影片：{job.id}")
        path = Path(job.video_path)
        if not path.exists():
            raise FileNotFoundError(f"找不到 job 影片檔：{path}")
        return path
