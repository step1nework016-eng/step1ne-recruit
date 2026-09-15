# Step1ne Agent 共用規則

這份檔案放在 `step1ne-recruit/`，是因為這裡是所有 Step1ne 常駐 AI agent（`launchctl list | grep step1ne` 看得到的 17 支）用 `subprocess.run(['claude','-p',...], cwd=HERE)` 呼叫 claude CLI 時的工作目錄——放在這裡等於自動套用到**所有** agent，不是只有某一支。改這份檔案影響範圍是全部 17 支，動手前想清楚。

## 候選人文字三條硬規則（絕對不能破）

1. 不用「客戶」當候選人看得到的敘事主詞，也不用顧問/我們當主詞去描述候選人的處境。
2. 婉拒訊息、任何候選人會看到的文字，絕對不提性別、年齡、外貌——就算用人單位原始要求裡有講，也只能記在內部備註欄位，絕對不寫進任何候選人可見的輸出或阿財的面談/評分邏輯。
3. 內部備註（顧問寫的、系統判斷的）絕對不外流給候選人——任何要送到候選人手上的內容，出稿前都要想一次「這句話是不是內部才看得到的東西」。

真實事故：2026-08-19 結案訊息直接寫出「傾向尋找男性人選」送到候選人手上。這條規則就是那次事故換來的。

## 資料清洗：呼叫 claude CLI 前一定要 sanitize

Prompt 是用命令列參數傳給 `claude -p`，字串裡只要有一個 `\x00`（PDF 擷取常見的雜訊），subprocess 就直接丟 `ValueError: embedded null character`，整個 job 失敗。

每一支會呼叫 claude CLI 的腳本都要有這段（或等價的）：
```python
def sanitize(t):
    if not t:
        return t
    return ''.join(c for c in str(t) if c in '\n\t' or ord(c) >= 32)
```
呼叫前務必 `sanitize(prompt)`。已知有這道防護的：`interview_daemon.py`、`ai_worker.py`。新增/複製一支新 agent 時要記得抄這段，2026-09-10 client_report_synthesize 就是漏了這道防護，候選人資料卡三天沒人發現。

## 多裝置協作保護鎖

任何會被多台裝置（本機＋同事的第二台/第三台裝置）同時輪詢處理的 D1 任務隊列，claim 一律用原子 UPDATE + 檢查 `meta.changes`，不能只查 `WHERE status='pending'` 再各自更新——兩台裝置搶到同一筆會被各自完整處理一次，重複寄信、重複發通知。

```python
claim = D.d1_raw(f"UPDATE some_table SET status='claimed', worker_id={D.q(WORKER_ID)} WHERE id={D.q(row_id)} AND status='pending'")
if not claim.get('meta', {}).get('changes'):
    continue  # 已經被別台裝置搶走
```

會 `git push` 或做 OAuth token 刷新的腳本（`close_sync.py`、`job_copy_loop.sh`、`refresh_threads_token.sh`）不適用這套——那些兩台裝置同時跑會撞車，維持單機執行。

## launchd 排程：RunAtLoad + KeepAlive，不要用 StartInterval

`StartInterval` 這個計時器機制會「安靜停止跳動」——已經在阿財面談、客戶履歷、JD重產、用人需求表匯入上撞過同一種病，其中 `com.step1ne.portalimport` 曾經停擺 3 天沒人發現。正確寫法（腳本自己是常駐迴圈，launchd 只負責「死了要重開」）：

```xml
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>ThrottleInterval</key><integer>30</integer>
```
腳本本身要支援 `--loop`（或本來就是 `while True` 常駐），不要依賴 launchd 定時戳它。

## D1 直連：SQL 手動轉義

`d1_http.py` 的 `query(sql)` 是直接打 Cloudflare D1 REST API，**沒有參數綁定**，字串要自己轉義：
```python
def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"
```
`meta.changes` 是判斷「這次 UPDATE 真的改到東西了嗎」的依據，多裝置搶鎖機制就是靠這個。

## 修改後要記得重啟

Python 常駐 daemon 不會自動 reload——改完程式碼一定要 `launchctl kickstart -k gui/$(id -u)/<label>`，不然運作中的行程還是用舊代碼。

## 驗證 JS/Worker 語法別信 `node --check`

Cloudflare Worker 用 ESM，`node --check` 對這類檔案不可靠。要驗證語法用 `npx wrangler deploy --dry-run --outdir /tmp/xxx`，那是真正的 esbuild 打包器，才會抓到真的語法錯誤。
