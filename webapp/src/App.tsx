import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import {
  BadgeCheck,
  BarChart3,
  Copy,
  Download,
  FileAudio2,
  FileText,
  FileVideo2,
  KeyRound,
  ListChecks,
  Loader2,
  Pencil,
  Play,
  Quote,
  RefreshCw,
  Save,
  Scissors,
  Sparkles,
  Upload,
  X,
} from 'lucide-react';
import {
  ApiError,
  analyzeVideo,
  clearVideos,
  exportJob,
  getAnalysis,
  getHealth,
  getRunJob,
  listJobs,
  listVideos,
  outputUrl,
  saveApiKey,
  updateClip,
  uploadVideo,
} from './api';
import type { ClipCandidate, ReviewDecision, VideoJob, WebRunJob, WorkbenchAnalysis } from './types';

type PanelTab = 'summary' | 'quotes' | 'clips' | 'titles' | 'timeline' | 'transcript';
const AUDIO_ACCEPT = 'audio/*,.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,.wma';
const MEDIA_ACCEPT = 'video/*,audio/*,.mp4,.mov,.mkv,.avi,.m4v,.webm,.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,.wma';
const AUDIO_EXTENSION_PATTERN = /\.(mp3|wav|m4a|aac|flac|ogg|opus|wma)$/i;
const VIDEO_EXTENSION_PATTERN = /\.(mp4|mov|mkv|avi|m4v|webm)$/i;

function seconds(value?: number | null, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function pct(value?: number | null): string {
  return value === null || value === undefined ? '-' : `${Math.round(value * 100)}%`;
}

function statusLabel(value: string): string {
  return {
    created: '已建立',
    imported: '已匯入',
    preprocessed: '已前處理',
    analyzing: '分析中',
    ready_for_review: '待審稿',
    exported: '已匯出',
    failed: '失敗',
    queued: '排隊中',
    running: '執行中',
    completed: '完成',
    stopped: '已停止',
  }[value] || value;
}

export default function App() {
  const [videos, setVideos] = useState<Array<{ name: string; type?: 'audio' | 'video'; size_mb: number }>>([]);
  const [jobs, setJobs] = useState<VideoJob[]>([]);
  const [selectedVideo, setSelectedVideo] = useState('');
  const [selectedJobId, setSelectedJobId] = useState('');
  const [analysis, setAnalysis] = useState<WorkbenchAnalysis | null>(null);
  const [runJobId, setRunJobId] = useState('');
  const [runJob, setRunJob] = useState<WebRunJob | null>(null);
  const [chunkMinutes, setChunkMinutes] = useState(10);
  const [draftClips, setDraftClips] = useState(true);
  const [force, setForce] = useState(false);
  const [tab, setTab] = useState<PanelTab>('summary');
  const [isDragging, setIsDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [hasApiKey, setHasApiKey] = useState(false);
  const [envPath, setEnvPath] = useState('');
  const [apiKeyInput, setApiKeyInput] = useState('');
  const [showApiKeyEditor, setShowApiKeyEditor] = useState(false);
  const [savingApiKey, setSavingApiKey] = useState(false);
  const [displayName, setDisplayName] = useState('AI 影音分析專業版 V2');
  const [audioOnly, setAudioOnly] = useState(false);
  const baselineJobIdsRef = useRef<Set<string>>(new Set());
  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement | null>(null);

  const selectedJob = useMemo(() => jobs.find((job) => job.id === selectedJobId), [jobs, selectedJobId]);
  const approvedCount = analysis?.clip_candidates.filter((clip) => ['kept', 'revised'].includes(clip.review.status || clip.review_status)).length || 0;
  const analysisFailed = !!analysis
    && !analysis.transcript_segments.length
    && !analysis.timeline_events.length
    && !analysis.quote_candidates.length
    && !analysis.clip_candidates.length
    && analysis.risk_notes.length > 0;
  const progress = useMemo(() => buildProgress(runJob, busy), [runJob, busy]);
  const fullTranscript = useMemo(() => {
    if (!analysis?.transcript_segments.length) return '';
    return analysis.transcript_segments.map((segment) => {
      const speaker = segment.speaker && segment.speaker !== 'unknown' ? `${segment.speaker}：` : '';
      return `[${segment.start_time} - ${segment.end_time}] ${speaker}${segment.text}`;
    }).join('\n');
  }, [analysis]);

  async function refreshVideos() {
    const videoItems = await listVideos();
    setVideos(videoItems);
    setSelectedVideo((current) => current || videoItems[0]?.name || '');
  }

  async function refreshHealth() {
    const health = await getHealth();
    setHasApiKey(health.has_api_key);
    setEnvPath(health.env_file);
    setDisplayName(health.display_name || 'AI 影音分析專業版 V2');
    setAudioOnly(!!health.audio_only);
    return health;
  }

  async function refreshSessionJobs() {
    const jobItems = await listJobs();
    const visible = jobItems.filter((job) => !baselineJobIdsRef.current.has(job.id));
    setJobs(visible);
    return visible;
  }

  async function selectCompletedWorkbenchJob(jobId: string, visibleJobs: VideoJob[]) {
    if (!jobId) return false;
    const visibleJob = visibleJobs.find((job) => job.id === jobId);
    if (visibleJob?.has_analysis) {
      setSelectedJobId(jobId);
      setError('');
      return true;
    }
    try {
      const loaded = await getAnalysis(jobId);
      setAnalysis(loaded);
      setJobs((current) => {
        const merged = visibleJobs.length ? visibleJobs : current;
        const withoutDuplicate = merged.filter((job) => job.id !== loaded.job.id);
        return [{ ...loaded.job, has_analysis: true }, ...withoutDuplicate];
      });
      setSelectedJobId(jobId);
      setError('');
      return true;
    } catch {
      return false;
    }
  }

  async function initializeLists() {
    await clearVideos();
    const [videoItems, jobItems, health] = await Promise.all([listVideos(), listJobs(), getHealth()]);
    baselineJobIdsRef.current = new Set(jobItems.map((job) => job.id));
    setVideos(videoItems);
    setJobs([]);
    setHasApiKey(health.has_api_key);
    setEnvPath(health.env_file);
    setDisplayName(health.display_name || 'AI 影音分析專業版 V2');
    setAudioOnly(!!health.audio_only);
    setSelectedVideo('');
  }

  useEffect(() => {
    initializeLists().catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!selectedJobId) {
      setAnalysis(null);
      return;
    }
    getAnalysis(selectedJobId)
      .then((next) => {
        setAnalysis(next);
        setError('');
      })
      .catch((err) => {
        setAnalysis(null);
        if (err instanceof ApiError && err.status === 404) {
          setError('這個 job 已建立 proxy 影音，但尚未產生分析結果。若任務仍在分析中請等待；若任務已失敗，請查看右側執行紀錄。');
          return;
        }
        setError(err instanceof Error ? err.message : String(err));
      });
  }, [selectedJobId]);

  useEffect(() => {
    if (!runJobId) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await getRunJob(runJobId);
        const logText = next.log.join('');
        const completedWorkbenchId = parseWorkbenchJobId(logText);
        const normalizedNext = next.status === 'failed' && completedWorkbenchId && logText.includes('新版工作台分析完成')
          ? { ...next, status: 'completed' as WebRunJob['status'] }
          : next;
        setRunJob(normalizedNext);
        if (['completed', 'failed', 'stopped'].includes(normalizedNext.status)) {
          window.clearInterval(timer);
          const visible = await refreshSessionJobs();
          const selectedCompletedJob = await selectCompletedWorkbenchJob(completedWorkbenchId, visible);
          if (selectedCompletedJob) {
            setBusy(false);
            return;
          }
          const readyJob = visible.find((job) => job.has_analysis);
          if (readyJob) {
            setSelectedJobId(readyJob.id);
            setError('');
          } else if (visible[0]) {
            const lastLog = next.log.slice(-6).join('').trim();
            setSelectedJobId('');
            setError(
              visible[0].analysis_error
              || visible[0].error
              || (normalizedNext.status === 'completed' ? '任務已完成，但分析結果尚未載入。請按重新整理或點選左側最新 job。' : lastLog)
              || '任務結束，但尚未產生 analysis.json。請查看執行紀錄或確認 API Key、配額與模型設定。'
            );
          }
          setBusy(false);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        setBusy(false);
        window.clearInterval(timer);
      }
    }, 1200);
    return () => window.clearInterval(timer);
  }, [runJobId]);

  function seekTo(time: number) {
    if (!mediaRef.current) return;
    mediaRef.current.currentTime = time;
    mediaRef.current.play().catch(() => undefined);
  }

  function clearWorkspaceState() {
    setSelectedJobId('');
    setAnalysis(null);
    setRunJobId('');
    setRunJob(null);
    setError('');
    setJobs([]);
    if (mediaRef.current) {
      mediaRef.current.pause();
      mediaRef.current.currentTime = 0;
    }
  }

  async function handleUpload(file?: File) {
    if (!file) return;
    const health = await refreshHealth();
    if (health.audio_only && !isAudioFile(file)) {
      setError(`純音檔版本只接受音訊檔案，已拒絕：${file.name}。請使用 mp3, wav, m4a, aac, flac, ogg, opus, wma。`);
      return;
    }
    clearWorkspaceState();
    setBusy(true);
    try {
      const uploaded = await uploadVideo(file);
      await refreshVideos();
      setSelectedVideo(uploaded.name);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleAnalyze() {
    if (!selectedVideo) return;
    const health = await refreshHealth();
    if (!health.has_api_key) {
      setError(`請先在左側「Gemini API Key」欄位貼上 Key 並儲存，才可開始分析。設定檔位置：${health.env_file}`);
      return;
    }
    const accepted = window.confirm(`即將呼叫 Gemini API 分析此${audioOnly ? '音訊' : '影音'}檔，可能消耗 API 配額或產生成本。確定要開始嗎？`);
    if (!accepted) return;
    clearWorkspaceState();
    setBusy(true);
    try {
      const result = await analyzeVideo({ videoName: selectedVideo, chunkMinutes, draftClips, force });
      setRunJobId(result.web_job_id);
      setRunJob(null);
      setError('');
    } catch (err) {
      setBusy(false);
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleSaveApiKey() {
    const key = apiKeyInput.trim();
    if (!key) {
      setError('請先貼上 Gemini API Key。');
      return;
    }
    setSavingApiKey(true);
    try {
      const health = await saveApiKey(key);
      setHasApiKey(health.has_api_key);
      setEnvPath(health.env_file);
      setApiKeyInput('');
      setShowApiKeyEditor(false);
      setError(health.has_api_key ? 'API Key 已儲存到本機 .env。' : 'API Key 尚未生效，請確認格式。');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingApiKey(false);
    }
  }

  async function patchClip(clip: ClipCandidate, decision: ReviewDecision) {
    if (!analysis) return;
    const next = await updateClip(analysis.job.id, clip.id, decision);
    setAnalysis(next);
  }

  async function reviseClip(clip: ClipCandidate) {
    const title = window.prompt('新的短影音標題', clip.review.updated_title || clip.suggested_title);
    if (!title) return;
    const hook = window.prompt('新的 Hook', clip.review.updated_hook || clip.hook || '') || '';
    const start = window.prompt('新的開始時間', clip.review.updated_start_time || clip.start_time) || clip.start_time;
    const end = window.prompt('新的結束時間', clip.review.updated_end_time || clip.end_time) || clip.end_time;
    await patchClip(clip, {
      status: 'revised',
      updated_title: title,
      updated_hook: hook,
      updated_start_time: start,
      updated_end_time: end,
    });
  }

  async function handleExport() {
    if (!analysis) return;
    if (analysisFailed) {
      setError('這個 job 沒有成功的 AI 分析結果，無法匯出。請等 API 配額恢復後重新分析。');
      return;
    }
    setBusy(true);
    try {
      const result = await exportJob(analysis.job.id, draftClips);
      setRunJobId(result.web_job_id);
      setRunJob(null);
      setError('');
    } catch (err) {
      setBusy(false);
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function copyTranscript() {
    if (!fullTranscript) return;
    await navigator.clipboard.writeText(fullTranscript);
  }

  function downloadTranscript() {
    if (!fullTranscript || !analysis) return;
    const blob = new Blob([fullTranscript], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${analysis.job.title || 'transcript'}_full_transcript.txt`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDragging(false);
    handleUpload(event.dataTransfer.files?.[0]);
  }

  function handleVideoSelect(name: string) {
    clearWorkspaceState();
    setSelectedVideo(name);
  }

  function isAudioName(name?: string | null): boolean {
    return !!name && AUDIO_EXTENSION_PATTERN.test(name);
  }

  function isAudioFile(file: File): boolean {
    if (file.type.startsWith('video/') || VIDEO_EXTENSION_PATTERN.test(file.name)) return false;
    if (file.type.startsWith('audio/')) return true;
    return isAudioName(file.name);
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="brandIcon">{audioOnly ? <FileAudio2 size={18} /> : <Scissors size={18} />}</div>
          <div>
            <div className="brandTitle">
              <h1>{displayName}</h1>
              {audioOnly && <span className="titleBadge">僅限音檔</span>}
            </div>
            <p>{audioOnly ? '純音檔限定｜只接受音訊檔案｜精準 Timecode、摘要、金句' : '影片優先｜精準 Timecode｜金句、標題、切片審稿'}</p>
          </div>
        </div>
        <div className="topActions">
          <button className="ghost" onClick={() => Promise.all([refreshVideos(), refreshHealth()])}><RefreshCw size={16} />重新整理</button>
          <button className="primary" disabled={!analysis || analysisFailed || busy} onClick={handleExport}><Download size={16} />匯出</button>
        </div>
      </header>
      {audioOnly && (
        <div className="audioOnlyNotice">
          <strong>純音檔版本：只接受音訊檔案</strong>
          <span>可上傳 mp3 / wav / m4a / aac / flac / ogg / opus / wma；影片格式會被拒絕。</span>
        </div>
      )}

      <main className="workspace">
        <aside className="leftRail">
          <section className="panel">
            <div className="sectionTitle"><Upload size={15} />{audioOnly ? '導入音訊' : '導入影音'}</div>
            <label
              className={`fileDrop ${isDragging ? 'dragging' : ''}`}
              onDragOver={(event) => {
                event.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
            >
              {audioOnly ? <FileAudio2 size={22} /> : <FileVideo2 size={22} />}
              <span>{audioOnly ? '拖拉音訊檔案到這裡' : '拖拉影片或音頻到這裡'}</span>
              <small>{audioOnly ? '只支援 mp3 / wav / m4a / aac / flac / ogg / opus / wma' : '支援 mp4 / mov / webm / mp3 / wav / m4a / flac'}</small>
              <input type="file" accept={audioOnly ? AUDIO_ACCEPT : MEDIA_ACCEPT} onChange={(event) => handleUpload(event.target.files?.[0])} />
            </label>
            <label>{audioOnly ? '本機音訊' : '本機影音'}</label>
            <select value={selectedVideo} onChange={(event) => handleVideoSelect(event.target.value)}>
              {!videos.length && <option value="">{audioOnly ? '尚無音訊，請先拖拉或選擇音檔' : '尚無影音，請先拖拉或選擇檔案'}</option>}
              {videos.map((video) => <option key={video.name} value={video.name}>{video.type === 'audio' ? '音頻' : '影片'} · {video.name} ({video.size_mb} MB)</option>)}
            </select>
            <div className={`apiKeyBox ${hasApiKey ? 'ready' : 'missing'}`}>
              <div className="apiKeyTitle">
                <KeyRound size={15} />
                <strong>{hasApiKey ? 'Gemini API Key 已設定' : 'Gemini API Key 未設定'}</strong>
              </div>
              <p>
                {hasApiKey && !showApiKeyEditor
                  ? 'Key 已儲存在本機 .env，畫面不顯示明文。只有按下開始影音分析時才會呼叫 Gemini API。'
                  : '貼上 Google AI Studio 的 Key，會儲存在本機 .env。儲存後欄位會清空並隱藏。'}
              </p>
              {hasApiKey && !showApiKeyEditor ? (
                <button className="ghost apiKeyUpdate" onClick={() => setShowApiKeyEditor(true)}>
                  <KeyRound size={14} />
                  更新 Key
                </button>
              ) : (
                <div className="apiKeyRow">
                  <input
                    type="password"
                    value={apiKeyInput}
                    placeholder="AIza..."
                    autoComplete="new-password"
                    spellCheck={false}
                    onCopy={(event) => event.preventDefault()}
                    onCut={(event) => event.preventDefault()}
                    onContextMenu={(event) => event.preventDefault()}
                    onChange={(event) => setApiKeyInput(event.target.value)}
                  />
                  <button className="ghost" disabled={savingApiKey || !apiKeyInput.trim()} onClick={handleSaveApiKey}>
                    {savingApiKey ? <Loader2 className="spin" size={14} /> : <Save size={14} />}
                    儲存
                  </button>
                  {hasApiKey && (
                    <button
                      className="ghost"
                      disabled={savingApiKey}
                      onClick={() => {
                        setApiKeyInput('');
                        setShowApiKeyEditor(false);
                      }}
                    >
                      取消
                    </button>
                  )}
                </div>
              )}
              {envPath && <small>{envPath}</small>}
            </div>
            <div className="formGrid">
              <label>
                切段分鐘
                <select value={chunkMinutes} onChange={(event) => setChunkMinutes(Number(event.target.value))}>
                  {[1, 3, 5, 10].map((value) => <option key={value} value={value}>{value} 分鐘</option>)}
                </select>
              </label>
              <label className="checkLine">
                <input type="checkbox" checked={draftClips} onChange={(event) => setDraftClips(event.target.checked)} />
                產生短片
              </label>
              <label className="checkLine">
                <input type="checkbox" checked={force} onChange={(event) => setForce(event.target.checked)} />
                重跑分析
              </label>
            </div>
            <button className="primary full" disabled={!selectedVideo || busy} onClick={handleAnalyze}>
              {busy ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />}
              {audioOnly ? '開始音訊分析' : '開始影音分析'}
            </button>
            {busy && (
              <div className="progressBox">
                <div className="progressHeader">
                  <span>{progress.label}</span>
                  <strong>{progress.percent}%</strong>
                </div>
                <div className="progressTrack"><div style={{ width: `${progress.percent}%` }} /></div>
                <p>{progress.detail}</p>
              </div>
            )}
          </section>

          <section className="panel grow">
            <div className="sectionTitle"><ListChecks size={15} />Jobs</div>
            <div className="jobList">
              {jobs.map((job) => (
                <button
                  key={job.id}
                  className={`jobItem ${job.id === selectedJobId ? 'active' : ''}`}
                  onClick={() => setSelectedJobId(job.id)}
                >
                  <strong>{job.title}</strong>
                  <span>{job.id}</span>
                  <em className={`status ${job.status}`}>{statusLabel(job.status)}</em>
                  {job.has_analysis === false && <small>尚未產生分析結果</small>}
                </button>
              ))}
              {!jobs.length && <div className="empty">本次尚無 job。開始新分析後才會顯示。</div>}
            </div>
          </section>
        </aside>

        <section className="centerStage">
          <div className="videoPanel">
            <div className="videoHeader">
              <div>
                <h2>{selectedJob?.title || '尚未選擇 job'}</h2>
                <span>{selectedJob ? `${selectedJob.id} · ${analysisFailed ? '分析失敗' : statusLabel(selectedJob.status)}` : '等待分析結果'}</span>
              </div>
              {analysis && <a className="ghostLink" href={outputUrl(analysis.job.id, 'full_report.md')} target="_blank">完整報告</a>}
            </div>
            {selectedJobId ? (
              isAudioName(selectedJob?.source || selectedJob?.video_path || selectedJob?.title) ? (
                <div className="audioPlayerShell">
                  <FileAudio2 size={32} />
                  <audio ref={(node) => { mediaRef.current = node; }} className="mediaPlayer" controls src={outputUrl(selectedJobId, 'proxy.mp4')} />
                </div>
              ) : (
                <video ref={(node) => { mediaRef.current = node; }} controls src={outputUrl(selectedJobId, 'proxy.mp4')} />
              )
            ) : (
              <div className="videoEmpty"><Play size={24} />選擇 job 後載入 proxy 影音</div>
            )}
          </div>

          <div className="transcriptPanel">
            <div className="sectionTitle"><BarChart3 size={15} />逐字稿與 Timecode</div>
            <div className="transcriptList">
              {analysis?.transcript_segments.map((segment, index) => (
                <button key={`${segment.start_time}-${index}`} className="transcriptRow" onClick={() => seekTo(seconds(segment.start_seconds))}>
                  <time>{segment.start_time}</time>
                  <p>{segment.speaker !== 'unknown' ? `${segment.speaker}：` : ''}{segment.text}</p>
                  <span>{pct(segment.confidence)}</span>
                </button>
              ))}
              {!analysis?.transcript_segments.length && (
                <div className={`empty ${analysisFailed ? 'failedEmpty' : ''}`}>
                  {analysisFailed ? 'AI 分析失敗，沒有產生逐字稿。請查看右側錯誤原因。' : '尚無逐字稿資料'}
                </div>
              )}
            </div>
          </div>
        </section>

        <aside className="rightRail">
          <div className="metricStrip">
            <div><strong>{analysis?.quote_candidates.length || 0}</strong><span>金句</span></div>
            <div><strong>{analysis?.clip_candidates.length || 0}</strong><span>切片</span></div>
            <div><strong>{approvedCount}</strong><span>已選</span></div>
          </div>

          <nav className="tabs">
            {[
              ['summary', Sparkles, '摘要'],
              ['quotes', Quote, '金句'],
              ['clips', Scissors, '切片'],
              ['titles', Pencil, '標題'],
              ['timeline', ListChecks, 'Timecode'],
              ['transcript', FileText, '逐字稿'],
            ].map(([id, Icon, label]) => (
              <button key={id as string} className={tab === id ? 'active' : ''} onClick={() => setTab(id as PanelTab)}>
                <Icon size={14} />{label as string}
              </button>
            ))}
          </nav>

          {error && <div className="alert">{error}</div>}
          {analysisFailed && (
            <div className="alert">
              <strong>AI 分析失敗</strong>
              <p>Gemini API 未回傳可用片段，因此沒有摘要、金句、切片或逐字稿。</p>
              <pre>{analysis.risk_notes.slice(0, 2).join('\n\n')}</pre>
            </div>
          )}
          {runJob && (
            <div className={`runBox ${runJob.status}`}>
              <strong>{statusLabel(runJob.status)} · {progress.label} · {progress.percent}%</strong>
              <div className="progressTrack"><div style={{ width: `${progress.percent}%` }} /></div>
              <pre>{runJob.log.slice(-16).join('')}</pre>
            </div>
          )}

          <div className="resultPanel">
            {tab === 'summary' && (
              <div className="stack">
                <article className="resultCard highlight">
                  <h3>全片摘要</h3>
                  <p>{analysisFailed ? '所有片段分析失敗，無法產生摘要。通常是 API 配額不足、模型不可用或網路限制造成。' : analysis?.overall_summary || '尚無分析結果'}</p>
                </article>
                {analysis?.chapter_map.map((chapter, index) => (
                  <article className="resultCard" key={`${chapter.start_time}-${index}`} onClick={() => seekTo(seconds(chapter.start_seconds))}>
                    <div className="cardMeta"><time>{chapter.start_time} - {chapter.end_time}</time><span>{pct(chapter.confidence)}</span></div>
                    <h3>{chapter.title}</h3>
                    <p>{chapter.summary}</p>
                  </article>
                ))}
              </div>
            )}

            {tab === 'quotes' && (
              <div className="stack">
                {analysis?.quote_candidates.map((quote, index) => (
                  <article className="resultCard quoteCard" key={quote.id} onClick={() => seekTo(seconds(quote.start_seconds))}>
                    <div className="cardMeta"><time>{quote.timecode}</time><span>#{index + 1} · {quote.score}</span></div>
                    <h3>「{quote.quote}」</h3>
                    <p>{quote.reason || quote.usage || quote.source_text}</p>
                  </article>
                ))}
              </div>
            )}

            {tab === 'clips' && (
              <div className="stack">
                {analysis?.clip_candidates.map((clip) => (
                  <article className="resultCard clipCard" key={clip.id}>
                    <div className="cardMeta"><time>{clip.start_time} - {clip.end_time}</time><span>{clip.score} · {pct(clip.confidence)}</span></div>
                    <h3>{clip.review.updated_title || clip.suggested_title}</h3>
                    <p>{clip.hook || clip.reason}</p>
                    <div className="risk">{clip.risk_notes.join(' ')}</div>
                    <div className="clipActions">
                      <button onClick={() => seekTo(seconds(clip.start_seconds))}><Play size={13} />跳轉</button>
                      <button onClick={() => patchClip(clip, { status: 'kept' })}><BadgeCheck size={13} />保留</button>
                      <button onClick={() => reviseClip(clip)}><Pencil size={13} />修改</button>
                      <button className="danger" onClick={() => patchClip(clip, { status: 'rejected' })}><X size={13} />刪除</button>
                    </div>
                  </article>
                ))}
              </div>
            )}

            {tab === 'titles' && (
              <div className="stack">
                {analysis?.social_title_pack.map((title, index) => (
                  <article className="resultCard" key={`${title.title}-${index}`}>
                    <div className="cardMeta"><span>{title.platform}</span><span>{title.score} · {pct(title.confidence)}</span></div>
                    <h3>{title.title}</h3>
                    <p>{title.angle || title.reason}</p>
                  </article>
                ))}
              </div>
            )}

            {tab === 'timeline' && (
              <div className="stack">
                {analysis?.timeline_events.map((event, index) => (
                  <article className="resultCard" key={`${event.start_time}-${index}`} onClick={() => seekTo(seconds(event.start_seconds))}>
                    <div className="cardMeta"><time>{event.start_time}</time><span>{pct(event.confidence)}</span></div>
                    <h3>{event.title}</h3>
                    <p>{event.summary}</p>
                  </article>
                ))}
              </div>
            )}

            {tab === 'transcript' && (
              <div className="stack">
                <article className="resultCard">
                  <div className="cardMeta">
                    <span>完整逐字稿</span>
                    <span>{analysis?.transcript_segments.length || 0} 段</span>
                  </div>
                  <div className="textActions">
                    <button disabled={!fullTranscript} onClick={copyTranscript}><Copy size={13} />複製全文</button>
                    <button disabled={!fullTranscript} onClick={downloadTranscript}><Download size={13} />下載 TXT</button>
                  </div>
                  {fullTranscript ? (
                    <pre className="transcriptFull">{fullTranscript}</pre>
                  ) : (
                    <div className={`empty ${analysisFailed ? 'failedEmpty' : ''}`}>
                      {analysisFailed ? 'AI 分析失敗，沒有可顯示的完整逐字稿。' : '尚無完整逐字稿。'}
                    </div>
                  )}
                </article>
              </div>
            )}
          </div>
        </aside>
      </main>
    </div>
  );
}

function parseWorkbenchJobId(logText: string): string {
  const match = logText.match(/Job ID[:：]\s*([A-Za-z0-9_-]+)/);
  return match?.[1] || '';
}

function buildProgress(runJob: WebRunJob | null, busy: boolean): { percent: number; label: string; detail: string } {
  if (!busy && !runJob) {
    return { percent: 0, label: '等待開始', detail: '尚未送出分析任務。' };
  }
  if (!runJob) {
    return { percent: 8, label: '送出任務', detail: '正在建立工作與前處理影音。' };
  }
  const log = runJob.log.join('');
  const matches = [...log.matchAll(/workbench 分析片段\s+(\d+)\/(\d+)/g)];
  if (runJob.status === 'failed') {
    return { percent: 100, label: '分析失敗', detail: '請查看錯誤訊息。' };
  }
  if (runJob.status === 'completed') {
    return { percent: 100, label: '完成', detail: '分析與輸出已完成。' };
  }
  if (log.includes('產生淘金彙整與社群包裝')) {
    return { percent: 88, label: '彙整結果', detail: '正在產生全片摘要、標題包與排行榜。' };
  }
  if (matches.length) {
    const last = matches[matches.length - 1];
    const current = Number(last[1]);
    const total = Math.max(1, Number(last[2]));
    const percent = Math.min(85, Math.max(18, Math.round(15 + (current / total) * 65)));
    return {
      percent,
      label: `分析片段 ${current}/${total}`,
      detail: '正在呼叫 Gemini 分析影音片段，長音頻可能需要數分鐘。',
    };
  }
  if (runJob.status === 'queued') {
    return { percent: 10, label: '排隊中', detail: '任務已建立，等待背景程序開始。' };
  }
  return { percent: 15, label: '前處理', detail: '正在切段、產生 proxy，接著會分析片段。' };
}
