export type JobStatus = 'created' | 'imported' | 'preprocessed' | 'analyzing' | 'ready_for_review' | 'exported' | 'failed';

export interface VideoJob {
  id: string;
  source_type: 'file' | 'url';
  source: string;
  video_path?: string | null;
  title: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  outputs: Record<string, string>;
  error?: string | null;
  has_analysis?: boolean;
  analysis_error?: string | null;
}

export interface WebRunJob {
  id: string;
  video_name: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'stopped';
  log: string[];
}

export interface ReviewDecision {
  status: 'pending' | 'kept' | 'rejected' | 'revised' | 'regenerate_requested';
  notes?: string;
  updated_title?: string | null;
  updated_hook?: string | null;
  updated_start_time?: string | null;
  updated_end_time?: string | null;
}

export interface TranscriptSegment {
  start_time: string;
  end_time: string;
  start_seconds?: number | null;
  end_seconds?: number | null;
  speaker: string;
  text: string;
  confidence?: number | null;
}

export interface TimelineEvent {
  start_time: string;
  end_time?: string | null;
  start_seconds?: number | null;
  title: string;
  summary: string;
  news_value?: string;
  confidence?: number | null;
  source_text?: string;
  risk_notes: string[];
}

export interface QuoteCandidate {
  id: string;
  timecode: string;
  start_seconds?: number | null;
  speaker: string;
  quote: string;
  emotion_tone?: string;
  usage?: string;
  score: number;
  confidence?: number | null;
  reason?: string;
  source_text?: string;
}

export interface ClipCandidate {
  id: string;
  start_time: string;
  end_time: string;
  start_seconds?: number | null;
  end_seconds?: number | null;
  duration_seconds?: number | null;
  reason: string;
  suggested_title: string;
  hook?: string;
  cover_text?: string;
  platform: string;
  score: number;
  confidence?: number | null;
  source_text?: string;
  risk_notes: string[];
  review_status: ReviewDecision['status'];
  review: ReviewDecision;
}

export interface ChapterMapItem {
  start_time: string;
  end_time: string;
  start_seconds?: number | null;
  end_seconds?: number | null;
  title: string;
  summary: string;
  short_video_value?: string;
  confidence?: number | null;
}

export interface SocialTitleCandidate {
  title: string;
  platform: string;
  angle?: string;
  related_clip_id?: string | null;
  score: number;
  confidence?: number | null;
  reason?: string;
}

export interface WorkbenchAnalysis {
  job: VideoJob;
  model: string;
  analyzed_at: string;
  overall_summary: string;
  top_points: string[];
  title_suggestions: string[];
  youtube_title_suggestions: string[];
  short_video_hooks: string[];
  transcript_segments: TranscriptSegment[];
  timeline_events: TimelineEvent[];
  quote_candidates: QuoteCandidate[];
  clip_candidates: ClipCandidate[];
  chapter_map: ChapterMapItem[];
  highlight_rankings: Array<{
    rank: number;
    clip_id: string;
    start_time: string;
    end_time: string;
    suggested_title: string;
    score: number;
    reason: string;
    risk_notes: string[];
  }>;
  social_title_pack: SocialTitleCandidate[];
  risk_notes: string[];
}
