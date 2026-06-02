# AI 影音分析專業版 V1 / V2 交接文件

## 來源工作

- Thread title: 【AI 影音分析專業版 V1】
- Thread ID: 019e681f-edf7-7973-9244-d6d1d313ad57
- 原工作目錄: C:\Users\proedit005\Documents\老馬的日常專案\ai-video-news-analyzer

## 這個 repo 包含什麼

這是乾淨的搬運 repo，保留可重建專案需要的原始碼與設定：

- `app/`: Python 後端與分析流程
- `webapp/src/`: React 前端原始碼
- `packaging/`: V2 / V2 純音檔啟動器與打包入口
- `package-v2.ps1`: 一般 V2 可攜包打包腳本
- `package-v2-audio-only.ps1`: 純音檔 V2 可攜包打包腳本
- `.env.example`: API key 設定範例，不含真實 key

這個 repo 不包含 `.env`、虛擬環境、node_modules、分析輸出、影片/音訊暫存、PyInstaller build cache。

## 最新可攜包

最新版「AI 影音分析專業版 V2 (純音檔)」ZIP 不放進 Git commit，因為單檔約 208MB，超過 GitHub 一般檔案 100MB 限制。它會以 GitHub Release asset 方式提供下載。

原公司電腦路徑：

```text
C:\Users\proedit005\Documents\老馬的日常專案\ai-video-news-analyzer\dist-v2-audio-only-latest\AI_AudioVideo_Pro_V2_AudioOnly.zip
```

## 家中電腦重啟方式

### 直接使用可攜 ZIP

1. 到 GitHub Release 下載 `AI_AudioVideo_Pro_V2_AudioOnly.zip`。
2. 解壓縮。
3. 執行 `AI 影音分析專業版 V2 (純音檔).bat`。
4. 若需要呼叫 Gemini API，請在解壓後的 `.env` 填入 `GEMINI_API_KEY`。

### 從原始碼啟動

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd webapp
npm install
npm run build
cd ..
.\start_web.ps1
```

### 重新打純音檔可攜包

```powershell
.\package-v2-audio-only.ps1
```

## 注意

- 不要提交 `.env`。
- 不要把影片、音訊、分析輸出、node_modules 或 `.venv` 放進 Git。
- 純音檔版應顯示 `AI 影音分析專業版 V2 (純音檔)`，並拒絕影片檔。
