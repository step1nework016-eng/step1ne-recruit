# E35：客戶提供的職缺「AI 讀取」要更新（WSL2 這台）

**現況**：後台新增職缺時，貼文字／截圖／PDF 會排一個 `job_read` 工作給 ai_worker。這台 WSL2 的 ai_worker 還是舊版，
領到工作會報「未知的工作類型：job_read」而失敗（Mac 那台已更新，會成功）。

**請做**
1. `git pull` 拉到最新（要包含 `job_read.py`，ai_worker.py 裡有 `kind == 'job_read'`）。
2. 重啟 ai_worker（跟平常更新 ai_worker 一樣的做法）。
3. 回報：`python3 -c "import job_read; print('ok')"` 的結果、ai_worker 重啟後的第一行 log。
4. 這台需要有 `pdftoppm`（poppler）才能讀 PDF：`which pdftoppm`，沒有的話 `sudo apt install poppler-utils`。

**驗收**：後台客戶卡片 →「客戶提供的職缺」→ 新增職缺 → 貼一段文字或上傳一張職缺截圖 →「整理成清單」，約 1～3 分鐘會出現讀好的欄位。
