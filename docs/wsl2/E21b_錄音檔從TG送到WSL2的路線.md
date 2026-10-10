# E21b｜Mac 決定：錄音檔從 TG 到 WSL2 的路線（2026-10-10）

## 結論
照「TG 收檔 → 雲端只記排隊 → WSL2 自己下載轉文字」做。**錄音檔不經過、也不存在雲端**，只記一筆排隊工作（TG 的 file_id）。

## 路線
1. **收檔（recruit worker 的 TG webhook，已經會收到所有 TG 訊息）**：在顧問助理那個主題（或你判斷更合適的主題），**Jacky／Phoebe**（沿用 BD_APPROVERS 的身分判斷）傳來 voice／audio，或 document 且 mime 是 audio/*（iPhone 錄音多半 m4a）→ 只寫一筆 `ai_jobs`：`kind='call_audio'`，payload＝`{file_id, file_size, mime, caption（Jacky 打的人選名字）, chat_id, thread_id, from}`，並回一則「收到錄音，轉文字中」。
   - 檔案 > 20MB（TG Bot 下載上限）：直接回覆請 Jacky 改傳較短的段落，不排隊。
   - 這段是 Cloudflare Worker 程式：**你寫好 patch 放 docs/wsl2/patches/，Mac 審過再部署**（你那台的 src 可能不是最新，不要自己 deploy）。
2. **WSL2 `ai_worker` 處理 `call_audio`**：用 bot token 呼叫 getFile → 下載到暫存 → faster-whisper（initial_prompt 帶這位人選的履歷公司名／職稱＋職缺名稱與必備技能）→ 用 caption 解析人選（同 `consultant_ops.py` 的 resolve；同名就在 TG 回清單請 Jacky 選）→ 走 `consultant_ops summary` 的**確認卡**（按「寫進卡片」才寫）→ **刪錄音暫存檔**。
3. **有阿財面談進行中**：`call_audio` 先等（跟找人那套 wait idle 一樣），不要拖慢阿財。

## 模型下載
Jacky 若核准（Mac 會轉告），下載 faster-whisper large-v3（或你評估中文更好的同級模型）到這台本機；先不要用雲端轉錄服務。

## 回報
照 E21 原本的回報要求，另外附上：worker patch 檔名、測試用的假 TG update（不要用真錄音）、20MB 以上的處理。
