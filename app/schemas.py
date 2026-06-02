from typing import Any, Literal

from pydantic import BaseModel, Field


class KeyEvent(BaseModel):
    timestamp: str = Field(description="HH:MM:SS")
    event: str
    news_value: str


class ImportantQuote(BaseModel):
    timestamp: str = Field(description="HH:MM:SS")
    speaker: str = "unknown"
    quote: str


class VisualNote(BaseModel):
    timestamp: str = Field(description="HH:MM:SS")
    description: str
    usefulness: str


class ShortVideoCandidate(BaseModel):
    start_time: str = Field(description="HH:MM:SS")
    end_time: str = Field(description="HH:MM:SS")
    reason: str
    suggested_title: str
    platform: str


class SegmentAnalysis(BaseModel):
    segment_index: int
    start_time: str
    end_time: str
    summary: str
    key_events: list[KeyEvent] = Field(default_factory=list)
    important_quotes: list[ImportantQuote] = Field(default_factory=list)
    visual_notes: list[VisualNote] = Field(default_factory=list)
    short_video_candidates: list[ShortVideoCandidate] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class SegmentError(BaseModel):
    segment_index: int
    file: str
    start_time: str
    end_time: str
    error: str


class VideoInfo(BaseModel):
    file_name: str
    file_path: str
    duration_seconds: float
    duration: str
    file_size_bytes: int
    file_size_mb: float
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    audio: dict[str, Any] = Field(default_factory=dict)


class FullReport(BaseModel):
    overall_summary_300_words: str
    top_10_points: list[str] = Field(default_factory=list)
    best_5_short_video_segments: list[ShortVideoCandidate] = Field(default_factory=list)
    news_title_suggestions: list[str] = Field(default_factory=list)
    youtube_title_suggestions: list[str] = Field(default_factory=list)
    short_video_hooks: list[str] = Field(default_factory=list)
    news_draft: str
    voiceover_draft: str
    fact_check_items: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    video_info: VideoInfo
    model: str
    analyzed_at: str
    segments: list[SegmentAnalysis] = Field(default_factory=list)
    errors: list[SegmentError] = Field(default_factory=list)
    full_report: FullReport


class GoldenQuote(BaseModel):
    timecode: str
    quote: str
    emotion_tone: str
    usage: str


class ChapterSuggestion(BaseModel):
    start_time: str
    end_time: str
    chapter_name: str
    outline: str
    main_title: str
    platform: str


class EditingSuggestion(BaseModel):
    timecode: str
    suggestion_type: str
    description: str
    reason: str


class ProductionHighlightReport(BaseModel):
    core_summary: list[str] = Field(default_factory=list)
    golden_quotes: list[GoldenQuote] = Field(default_factory=list)
    chapter_suggestions: list[ChapterSuggestion] = Field(default_factory=list)
    editing_suggestions: list[EditingSuggestion] = Field(default_factory=list)
    timecode_notes: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class PipelineSettings(BaseModel):
    chunk_minutes: int = 10
    profile_id: str = "production_director"
    create_draft_clips: bool = True
    max_draft_clips: int = 8


class ReviewDecision(BaseModel):
    status: Literal["pending", "kept", "rejected", "revised", "regenerate_requested"] = "pending"
    notes: str = ""
    updated_title: str | None = None
    updated_hook: str | None = None
    updated_start_time: str | None = None
    updated_end_time: str | None = None
    updated_at: str | None = None


class VideoJob(BaseModel):
    id: str
    source_type: Literal["file", "url"]
    source: str
    video_path: str | None = None
    title: str
    status: Literal["created", "imported", "preprocessed", "analyzing", "ready_for_review", "exported", "failed"] = "created"
    created_at: str
    updated_at: str
    settings: PipelineSettings = Field(default_factory=PipelineSettings)
    video_info: VideoInfo | None = None
    outputs: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


class TranscriptSegment(BaseModel):
    start_time: str
    end_time: str
    start_seconds: float | None = None
    end_seconds: float | None = None
    speaker: str = "unknown"
    text: str
    confidence: float | None = Field(default=None, ge=0, le=1)


class TimelineEvent(BaseModel):
    start_time: str
    end_time: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    title: str
    summary: str
    visual_description: str = ""
    news_value: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_text: str = ""
    source_segment_index: int | None = None
    risk_notes: list[str] = Field(default_factory=list)


class QuoteCandidate(BaseModel):
    id: str = ""
    timecode: str
    start_seconds: float | None = None
    speaker: str = "unknown"
    quote: str
    emotion_tone: str = ""
    usage: str = ""
    score: int = Field(default=0, ge=0, le=100)
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str = ""
    source_text: str = ""
    source_segment_index: int | None = None
    review: ReviewDecision = Field(default_factory=ReviewDecision)


class ClipCandidate(BaseModel):
    id: str = ""
    start_time: str
    end_time: str
    start_seconds: float | None = None
    end_seconds: float | None = None
    duration_seconds: float | None = None
    reason: str
    suggested_title: str
    hook: str = ""
    cover_text: str = ""
    platform: str = "Shorts/Reels/TikTok"
    score: int = Field(default=0, ge=0, le=100)
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_text: str = ""
    source_segment_index: int | None = None
    risk_notes: list[str] = Field(default_factory=list)
    review_status: Literal["pending", "kept", "rejected", "revised", "regenerate_requested"] = "pending"
    review: ReviewDecision = Field(default_factory=ReviewDecision)


class ChapterMapItem(BaseModel):
    start_time: str
    end_time: str
    start_seconds: float | None = None
    end_seconds: float | None = None
    title: str
    summary: str
    short_video_value: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_text: str = ""


class HighlightRankingItem(BaseModel):
    rank: int
    clip_id: str
    start_time: str
    end_time: str
    suggested_title: str
    score: int = Field(default=0, ge=0, le=100)
    reason: str
    risk_notes: list[str] = Field(default_factory=list)


class SocialTitleCandidate(BaseModel):
    title: str
    platform: str = "Shorts/Reels/TikTok"
    angle: str = ""
    related_clip_id: str | None = None
    score: int = Field(default=0, ge=0, le=100)
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str = ""


class GoldminePackage(BaseModel):
    refined_summary: str
    chapter_map: list[ChapterMapItem] = Field(default_factory=list)
    highlight_rankings: list[HighlightRankingItem] = Field(default_factory=list)
    social_title_pack: list[SocialTitleCandidate] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class WorkbenchSegmentAnalysis(BaseModel):
    segment_index: int
    start_time: str
    end_time: str
    summary: str
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list)
    timeline_events: list[TimelineEvent] = Field(default_factory=list)
    quote_candidates: list[QuoteCandidate] = Field(default_factory=list)
    clip_candidates: list[ClipCandidate] = Field(default_factory=list)
    chapter_suggestions: list[ChapterSuggestion] = Field(default_factory=list)
    editing_suggestions: list[EditingSuggestion] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class WorkbenchAnalysis(BaseModel):
    job: VideoJob
    model: str
    analyzed_at: str
    overall_summary: str
    top_points: list[str] = Field(default_factory=list)
    title_suggestions: list[str] = Field(default_factory=list)
    youtube_title_suggestions: list[str] = Field(default_factory=list)
    short_video_hooks: list[str] = Field(default_factory=list)
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list)
    timeline_events: list[TimelineEvent] = Field(default_factory=list)
    quote_candidates: list[QuoteCandidate] = Field(default_factory=list)
    clip_candidates: list[ClipCandidate] = Field(default_factory=list)
    chapter_map: list[ChapterMapItem] = Field(default_factory=list)
    highlight_rankings: list[HighlightRankingItem] = Field(default_factory=list)
    social_title_pack: list[SocialTitleCandidate] = Field(default_factory=list)
    chapter_suggestions: list[ChapterSuggestion] = Field(default_factory=list)
    editing_suggestions: list[EditingSuggestion] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
