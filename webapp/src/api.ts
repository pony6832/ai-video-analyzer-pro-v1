import type { ReviewDecision, VideoJob, WebRunJob, WorkbenchAnalysis } from './types';

export interface HealthStatus {
  ok: boolean;
  project_root: string;
  env_file: string;
  has_env: boolean;
  has_api_key: boolean;
  model: string;
  mode: string;
  display_name?: string;
  audio_only?: boolean;
  allowed_extensions?: string[];
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, options);
  } catch (err) {
    const origin = window.location.origin || '目前頁面';
    const message = err instanceof Error ? err.message : String(err);
    throw new Error(`無法連線到本機後端 API：${url}。請確認是用 V2 BAT/EXE 開啟，網址為 http://127.0.0.1:8001-8010/pro-workbench，不要直接開啟 dist/index.html。來源：${origin}。原始錯誤：${message}`);
  }
  const type = response.headers.get('content-type') || '';
  const body = type.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof body === 'object' && body && 'detail' in body ? String(body.detail) : String(body);
    throw new ApiError(detail || `HTTP ${response.status}`, response.status);
  }
  return body as T;
}

export function listVideos(): Promise<Array<{ name: string; type?: 'audio' | 'video'; size_mb: number; modified_at: string }>> {
  return request('/api/videos');
}

export function clearVideos(): Promise<{ removed: number; failed: string[] }> {
  return request('/api/videos/clear', { method: 'POST' });
}

export function getHealth(): Promise<HealthStatus> {
  return request('/api/health');
}

export function saveApiKey(apiKey: string): Promise<HealthStatus> {
  return request('/api/settings/api-key', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey }),
  });
}

export function uploadVideo(file: File): Promise<{ name: string; path: string; size_mb: number }> {
  const form = new FormData();
  form.append('file', file);
  return request('/api/upload', { method: 'POST', body: form });
}

export function listJobs(): Promise<VideoJob[]> {
  return request('/api/pro-workbench/jobs');
}

export function analyzeVideo(input: {
  videoName: string;
  chunkMinutes: number;
  draftClips: boolean;
  force: boolean;
}): Promise<{ web_job_id: string; status: string }> {
  const form = new FormData();
  form.append('video_name', input.videoName);
  form.append('chunk_minutes', String(input.chunkMinutes));
  form.append('draft_clips', String(input.draftClips));
  form.append('force', String(input.force));
  form.append('confirm_paid', 'true');
  return request('/api/pro-workbench/analyze', { method: 'POST', body: form });
}

export function getRunJob(id: string): Promise<WebRunJob> {
  return request(`/api/jobs/${encodeURIComponent(id)}`);
}

export function getAnalysis(jobId: string): Promise<WorkbenchAnalysis> {
  return request(`/api/pro-workbench/jobs/${encodeURIComponent(jobId)}/analysis`);
}

export function updateClip(jobId: string, clipId: string, decision: ReviewDecision): Promise<WorkbenchAnalysis> {
  return request(`/api/pro-workbench/jobs/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(clipId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(decision),
  });
}

export function exportJob(jobId: string, draftClips: boolean): Promise<{ web_job_id: string; status: string }> {
  const form = new FormData();
  form.append('draft_clips', String(draftClips));
  return request(`/api/pro-workbench/jobs/${encodeURIComponent(jobId)}/export`, { method: 'POST', body: form });
}

export function outputUrl(jobId: string, filename: string): string {
  return `/workbench/outputs/${encodeURIComponent(jobId)}/${filename}`;
}
