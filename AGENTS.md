# AGENTS.md

本專案是「AI Video News Analyzer」，目標是服務新聞、製播與短影音剪輯工作流。系統要能分析長影片、記者會、訪談與直播回放，輸出新聞工作者可用的摘要、時間軸、短影音剪輯建議、新聞稿初稿與旁白稿初稿。

## 開發原則

- 優先保持程式可執行。任何新功能都不能破壞既有 CLI。
- `python main.py analyze ./videos/input.mp4` 是第一版核心入口，修改前後都要確認可用。
- 新功能要保持模組化，優先放在 `app/` 中對應模組，不要把邏輯塞進 `main.py`。
- API Key 絕對不可寫死在程式碼、README 範例以外的設定或測試資料中。
- 使用 `.env` 管理 `GEMINI_API_KEY` 與 `GEMINI_MODEL`。
- 所有輸出與 prompt 預設支援繁體中文。
- 報告格式要適合新聞工作者閱讀，重視時間點、新聞價值、可剪片段、查證風險。
- 不要編造影片中看不到或聽不到的內容；不確定時標記「需要人工確認」。
- 單一片段失敗時，不要讓整個分析任務中斷。保留錯誤並繼續下一段。
- Cache 機制是核心需求。不要移除 `outputs/cache/{part}.json` 的可重跑流程。

## 重要模組

- `app/config.py`：環境變數、路徑與 API Key 檢查。
- `app/video_utils.py`：FFmpeg / ffprobe、影片資訊讀取與切段。
- `app/gemini_client.py`：Gemini SDK 呼叫封裝，未來模型切換應從這裡擴充。
- `app/prompts.py`：所有 Gemini prompt 集中管理，必須使用繁體中文。
- `app/schemas.py`：Pydantic 輸出格式。
- `app/analyzer.py`：分析 orchestration、cache、錯誤處理。
- `app/report_writer.py`：Markdown 與 JSON 輸出。
- `main.py`：Typer CLI，應保持精簡。

## 驗證建議

至少確認：

```powershell
python main.py --help
python -m py_compile main.py app\*.py
```

如果有測試影片與有效 `.env`，再確認：

```powershell
python main.py analyze .\videos\test.mp4
```

