# AI Video News Analyzer

AI Video News Analyzer 是一個給新聞、製播、短影音剪輯工作流使用的長影片分析 CLI MVP。你可以把記者會、訪談、直播回放或長影片丟進系統，工具會切段、上傳 Gemini File API 分析，並輸出摘要、時間軸、短影音剪輯建議、新聞稿初稿與完整報告。

第一版以 CLI 為主，專案結構保留 `app/` 模組，方便後續加入 FastAPI 或網頁 UI。

## 功能

- 使用 `ffprobe` 讀取影片長度、大小、解析度、FPS 與音訊資訊
- 使用 `ffmpeg` 預設每 10 分鐘切段，可用參數調整
- 使用 Google Gemini File API 上傳影片片段並分析
- 每段輸出結構化 JSON 分析結果
- 支援 cache，可重跑失敗任務，不必每次重新分析全部片段
- 輸出 Markdown 與 JSON，預設支援繁體中文新聞工作流

## 安裝

建議使用 Python 3.11 以上。

```powershell
cd ai-video-news-analyzer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

如果你的系統使用 `py` 啟動 Python：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 安裝 FFmpeg

Windows 可用其中一種方式安裝：

```powershell
winget install Gyan.FFmpeg
```

或到 FFmpeg 官方網站下載建置版，將 `bin` 目錄加入 `PATH`。安裝後確認：

```powershell
ffmpeg -version
ffprobe -version
```

macOS 可用 Homebrew：

```bash
brew install ffmpeg
```

Ubuntu / Debian：

```bash
sudo apt update
sudo apt install ffmpeg
```

## 設定 Gemini API Key

1. 前往 [Google AI Studio](https://aistudio.google.com/app/apikey) 建立 Gemini API Key。
2. 複製 `.env.example` 為 `.env`。
3. 填入你的 API Key。

```env
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
```

API Key 不可寫死在程式碼中，也不要提交 `.env`。

## 使用方式

### Web 介面

Windows 可直接雙擊：

```text
AI Video News Analyzer Web.bat
```

或用 PowerShell 啟動：

```powershell
.\start_web.ps1
```

啟動後打開：

```text
http://127.0.0.1:8000
```

全新的長影片淘金工作台：

```text
http://127.0.0.1:8000/goldmine
```

新版 AI 影片分析與短影音企劃工作台：

```text
http://127.0.0.1:8000/workbench
```

Web 介面可上傳影片、選擇既有影片、設定切段分鐘、啟動分析、停止任務、查看即時 log、開啟或預覽輸出報告。

目前 Web 介面預設是免費模式：只使用本機 FFmpeg 做影片資訊與切段，並產生可貼到 Gemini Web/Gem 的 prompt 工作包，不會呼叫 Gemini API，也不會消耗 API 額度。Web 也提供付費 API 模式，但切換後會先提示確認，且必須有有效 `GEMINI_API_KEY`。

`/workbench` 是新版 job-based 工作台，支援本機影片與 YouTube/直播連結匯入，會產生摘要、重點 timecode、金句、短影音候選、SRT、EDL、完整報告與短片草稿，並提供「保留 / 刪除 / 改標題」的人審流程。

免費影片模式輸出檔名為：

- `outputs/input_manual_prompts.md`
- `outputs/input_manual_manifest.json`

逐字稿模式支援貼上訪談逐字稿，使用「影視後製導演與內容企劃」模板輸出：

- 核心摘要
- Golden Quotes
- 章節結構建議
- B-roll / Close-up / 剪輯建議
- Timecode 備註
- 需要人工確認事項

### 桌面啟動器

Windows 可直接雙擊：

```text
AI Video News Analyzer.bat
```

或用 PowerShell 啟動：

```powershell
.\launch_gui.ps1
```

啟動器可以選擇影片、設定切段分鐘、勾選是否忽略 cache，並在視窗中查看分析 log。分析完成後可按「開啟輸出資料夾」或「開啟完整報告」。

### 快速 CLI 腳本

使用 `run.ps1` 可少打一串 Python 路徑：

```powershell
.\run.ps1 .\videos\input.mp4
```

指定切段分鐘：

```powershell
.\run.ps1 .\videos\input.mp4 5
```

忽略 cache 重新分析：

```powershell
.\run.ps1 .\videos\input.mp4 10 -Force
```

### 原始 CLI

新版工作台流程：

```powershell
python main.py analyze-video .\videos\input.mp4 --chunk-minutes 10
python main.py analyze-video "https://www.youtube.com/watch?v=..." --chunk-minutes 10
python main.py list-video-jobs
python main.py prepare-review <job-id>
python main.py export-clips <job-id>
python main.py export-report <job-id>
```

免費模式：

```powershell
python main.py prepare-manual .\videos\input.mp4 --chunk-minutes 10 --profile-id news_editor
```

影視後製導演模板：

```powershell
python main.py prepare-manual .\videos\input.mp4 --chunk-minutes 10 --profile-id production_director
```

逐字稿免費模式：

```powershell
python main.py prepare-transcript .\transcripts\interview.txt --profile-id production_director
```

逐字稿付費 API 模式：

```powershell
python main.py analyze-transcript .\transcripts\interview.txt --profile-id production_director
```

影片付費 API production 模式：

```powershell
python main.py analyze-production-video .\videos\input.mp4 --chunk-minutes 10 --profile-id production_director
```

API 分析模式：

把影片放在 `videos/`，或直接指定任意影片路徑：

```powershell
python main.py analyze .\videos\input.mp4
```

調整切段長度：

```powershell
python main.py analyze .\videos\input.mp4 --chunk-minutes 5
```

忽略 cache，重新分析所有片段：

```powershell
python main.py analyze .\videos\input.mp4 --force
```

查看 CLI 說明：

```powershell
python main.py --help
```

## 輸出檔案

分析 `input.mp4` 後會產生：

- `outputs/input_summary.md`：全片摘要與 10 個重點
- `outputs/input_timeline.json`：時間軸事件 JSON
- `outputs/input_short_video_ideas.md`：短影音剪輯建議與 Hook
- `outputs/input_news_draft.md`：新聞稿初稿與標題建議
- `outputs/input_full_report.md`：完整 Markdown 報告
- `outputs/cache/input_part_001.json`：每段 Gemini 分析 cache

新版工作台 job 會輸出到：

- `outputs/jobs/<job-id>/analysis.json`
- `outputs/jobs/<job-id>/timeline.json`
- `outputs/jobs/<job-id>/clip_candidates.json`
- `outputs/jobs/<job-id>/quotes.json`
- `outputs/jobs/<job-id>/subtitles.srt`
- `outputs/jobs/<job-id>/edit_decision_list.json`
- `outputs/jobs/<job-id>/full_report.md`
- `outputs/jobs/<job-id>/draft_clips/*.mp4`

切段影片會輸出到：

- `temp/input/input_part_001.mp4`
- `temp/input/input_part_002.mp4`

## Cache 與重跑

每段分析成功後會寫入 `outputs/cache/{影片名}_part_001.json`。下次執行時如果 cache 存在，會直接讀取，不再呼叫 Gemini API。

如果某段分析失敗，任務會記錄錯誤並繼續下一段。已成功的片段會保留 cache，方便下次接續。

## 常見錯誤排除

### 找不到 GEMINI_API_KEY

請確認已建立 `.env`，且內容包含：

```env
GEMINI_API_KEY=你的_key
```

### 找不到 FFmpeg / ffprobe

請先安裝 FFmpeg，並確認 `ffmpeg` 與 `ffprobe` 可在命令列直接執行。

### 找不到影片檔

請確認路徑正確。例如：

```powershell
python main.py analyze .\videos\test.mp4
```

### Gemini API 失敗或網路錯誤

請檢查 API Key、網路連線、Google AI Studio 專案配額，以及影片格式是否支援。已成功分析的片段會保留在 cache。

### JSON 解析失敗

工具會把錯誤記錄到完整報告的「片段分析錯誤」或「需要人工查證事項」。可使用 `--force` 重跑。

## 專案結構

```text
ai-video-news-analyzer/
├── README.md
├── requirements.txt
├── .env.example
├── AGENTS.md
├── main.py
├── launcher.py
├── start_web.ps1
├── launch_gui.ps1
├── run.ps1
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── video_utils.py
│   ├── gemini_client.py
│   ├── analyzer.py
│   ├── schemas.py
│   ├── report_writer.py
│   └── prompts.py
├── videos/
├── outputs/
└── temp/
```

## 未來功能規劃

- FastAPI 後端與任務佇列
- 網頁上傳與分析進度 UI
- 支援 YouTube URL 匯入
- 自動抽音訊與語音轉錄備援流程
- 多模型切換與成本估算
- 片段級人工校稿與再生成
- 短影音 EDL / 剪輯清單輸出
- 多語言字幕與 SRT 輸出
