# AI 影音分析專業版 V2 (純音檔) 同仁使用說明

## 啟動方式

1. 解壓縮整個 `AI_AudioVideo_Pro_V2_AudioOnly` 資料夾。
2. 雙擊 `AI 影音分析專業版 V2 (純音檔).bat`。
3. 系統會自動啟動本機服務，等服務就緒後打開瀏覽器。
4. 若瀏覽器出現 `ERR_CONNECTION_REFUSED`，請查看啟動視窗，通常代表防毒軟體阻擋 exe 或 8001-8010 連接埠被占用。

## 重要限制

此版本只接受音訊檔案，不接受影片檔。

支援音訊格式：

- mp3
- wav
- m4a
- aac
- flac
- ogg
- opus
- wma

影片格式如 mp4、mov、webm、mkv、avi 會被網頁與後端拒絕。

## API Key

此交付包預設不包含製作者的 Gemini API Key。

可以直接在網頁左側「Gemini API Key」欄位貼上 Key 並按「儲存」。系統會寫入本資料夾的 `.env`。

也可以用記事本編輯 `.env`：

```env
GEMINI_API_KEY=你的_Gemini_API_Key
GEMINI_MODEL=gemini-2.5-flash
GEMINI_FALLBACK_MODELS=gemini-2.0-flash
APP_DISPLAY_NAME=AI 影音分析專業版 V2 (純音檔)
APP_AUDIO_ONLY=true
```

只有按下「開始音訊分析」後才會呼叫 Gemini API。上傳、播放、切段、匯出與查看舊結果都在本機進行。

## 注意事項

- 請保留整個資料夾結構，不要只複製 BAT 檔。
- 若 Windows 跳出安全提示，請選擇仍要執行。
- 若啟動失敗，請確認資料夾沒有放在需要系統管理員權限的位置。
- 若同仁電腦已有服務占用 8001，啟動器會自動嘗試 8002-8010。
