# ai_jobs 卡住工作回收機制｜實作報告

> 日期：2026-09-18
> 對應：`ACAI_P3_PREDEPLOY_QA_REPORT.md` 的 BUG 10
> 結論：**READY_FOR_STAGE1_CANARY**（但見下方「現況修正」——Stage 1 其實已經跑完）
> 判斷標準不是「功能會不會跑」，而是：**即使 AI Worker 半夜掛掉、重啟、自動更新，工作也不會永遠消失或一直重複燒額度。**

---

## ⚠️ 現況修正：Stage 1 已經跑完，目前在 Stage 2

原始需求寫的是「在正式開啟 P3 Stage 1 Canary 前要先修這個」。實際上：

- **Stage 1 Canary 已於 2026-09-18 19:00–19:50 執行完畢**（指定三位：陳旻婕／王仁君／林巧昀）
- **目前運行在 Stage 2**：`P3_REMATCH_CREATED_AFTER=2026-09-18T19:49:47`，只處理該時點之後新產生的面談報告，不補掃 22 位歷史人選

所以本輪要回答的不是「能不能開 Stage 1」，而是**「現在跑著的 Stage 2 夠不夠穩，能不能往 Stage 3 走」**。

---

## 1｜Root Cause

`ai_worker.py` 認領工作時把 `status` 改成 `running`（`tick()`）。若 daemon 在工作執行期間：

- 被 `launchctl bootout` / `kickstart` 重啟
- 自動更新機制偵測到 git 新版 → `git pull` + `os.execv` 重啟自己
- crash / 被 kill / 機器重開

那筆工作就**永遠停在 `running`**，沒有任何程式會再碰它。

**今天實際發生**：陳旻婕的工作在 19:17 標記 `running`，daemon 之後因部署與設定調整重啟多次，該工作卡住約 30 分鐘，最後靠人工 `UPDATE ... SET status='pending'` 救回。

**不是 P3 的問題**：`ai_jobs` 是全系統共用佇列（7 種 job kind），任何一種都會中獎。而且 `ai_worker.py` 本身就有「每 5 分鐘檢查 git、有新版就自動重啟自己」的機制——**重啟不是意外，是設計好的常態行為**，所以這個洞遲早會被踩到。

---

## 2｜現行 lifecycle 與 status 稽核

`ai_jobs` 欄位（實測 `PRAGMA table_info`）：
`id, kind, payload_json, status, result_text, error, created_at, started_at, done_at, attempts, worker_id`

**✅ 不需要任何 migration** —— 判定「跑多久了」所需的 `started_at`、`attempts`、`worker_id` 全部已存在。

| status | 誰寫入 | 何時 | source |
|---|---|---|---|
| `pending` | Cloudflare Worker（`queueAiJob`） | 顧問在後台按下產生按鈕時 | `step1ne-backoffice-worker/src/index.js:630` |
| `pending` | `ai_worker.py` 自己 | 本機輪詢排入（P3 rematch） | `ai_worker.py` `scan_rematch_candidates()` |
| `pending`（退回） | `ai_worker.py` | 執行失敗但還有重試次數 | `tick()` 例外處理 |
| `running` | `ai_worker.py` | 認領成功的當下 | `tick()` 的認領 UPDATE |
| `done` | `ai_worker.py` | 執行成功 | `tick()` |
| `failed` | `ai_worker.py` | 重試次數用盡 | `tick()` 例外處理 |

消費端：`promote_writebacks()` 負責把 `done` 的結果寫回 `applications`（只處理 `call_notes_summary`／`call_prep`／`precall_card`／`postcall_result` 四種）；`client_report_tick.py` 另外消費客戶報告類的結果。

目前狀態分佈：`done` 39／`failed` 22／`running` 1（就是那筆卡住的）。

---

## 3｜Stale Threshold：依真實資料決定，不是憑感覺

Production 實測各 kind 執行時間（有 `started_at`＋`done_at` 的 39 筆）：

| kind | 筆數 | 平均 | 最短 | **最長** |
|---|---|---|---|---|
| `client_report_synthesize` | 25 | 210s | 60s | **437s（7.3 分）** |
| `post_interview_rematch` | 14 | 97s | 50s | 158s（2.6 分） |

另外 `claude` CLI 本身的 `TIMEOUT = 480s`（8 分鐘）——這是單次執行的硬上限。

**採用門檻：20 分鐘**（`AI_JOBS_STALE_MINUTES`，可用環境變數覆寫）

理由：最長合法執行時間約 8 分鐘（CLI 逾時），門檻取約 2.5 倍餘裕。**絕不會把還在正常跑的長工作搶回去重跑**——那會變成同一份工作跑兩次、付兩次 AI 費用，比卡住更糟。

---

## 4｜Retry Safety：逐一分類（這是本輪最關鍵的一步）

**回收＝重跑。重跑安不安全，取決於「寫回」是覆蓋還是新增。**

| kind | 寫回方式 | 判定 | 說明 |
|---|---|---|---|
| `post_interview_rematch` | INSERT，但有 `(source_report_id, recommended_job_slug)` **唯一索引** | ✅ safe | 已實測：同一人重跑新增 0 筆 |
| `precall_card` | `UPDATE applications.call_prep_md` | ✅ safe | 覆蓋，無副作用 |
| `postcall_result` | `UPDATE applications.post_call_result_json` | ✅ safe | 同上 |
| `call_prep` | `UPDATE applications.call_prep_md` | ✅ safe | 同上 |
| `call_notes_summary` | `UPDATE call_summary_md` **＋ `INSERT INTO candidate_notes`**（當 `also_note` 為真） | ❌ **unsafe** | 重跑會讓**同一次電洽在候選人時間軸出現兩次**，顧問會誤以為真的聯絡過兩次 |
| `client_report_synthesize` | 由 `client_report_tick.py` 另一支 daemon 消費 | ⚠️ partial | 寫回路徑不在本檔，且單次成本最高（最長 437s），盲目重跑代價大 |
| `sourced_client_report_synthesize` | 同上 | ⚠️ partial | 同上 |
| `call_summary_client` | 同上 | ⚠️ partial | 同上 |

---

## 5｜設計決定：白名單制（刻意與原始建議相反）

原始建議是「**全部回收，有不安全的再排除**」。本實作**反過來：預設誰都不救，只有驗證過安全的才進白名單**。

```python
RECOVERABLE_KINDS = (
    'post_interview_rematch',  # 唯一索引擋重複，已實測
    'precall_card',            # UPDATE 單一欄位
    'postcall_result',         # UPDATE 單一欄位
    'call_prep',               # UPDATE 單一欄位
)
```

**為什麼反過來**：`call_notes_summary` 就是那個會產生重複資料的例子。「先開放再排除」與「先關閉再開放」在**正常情況下結果一樣，但出錯時差很多**——前者的失敗模式是「顧問看到假的重複聯絡紀錄」，後者的失敗模式只是「這筆要人工處理」。基礎設施改動選後者。

不在白名單的 kind 卡住時：**維持 `running` 不動**，由人工在 `ai_jobs` 看到並決定。

---

## 6｜狀態流轉與重試上限

```
pending ──claim──> running ──成功──> done
                      │
                      └─ 卡超過 20 分鐘 ─┬─ attempts < 3 ──> pending（attempts 不變，由下次認領 +1）
                                          └─ attempts >= 3 ──> failed（寫入 error 說明原因）
```

沿用既有的 `MAX_ATTEMPTS = 3`，**不新增任何欄位、不重構佇列**。

達上限時寫入的 error 訊息：`卡在 running 超過 20 分鐘，且已達重試上限 3 次` —— 讓事後查 `ai_jobs` 的人一眼看懂發生什麼事。

**防競態**：退回 pending 用的是帶條件的更新
`UPDATE ... SET status='pending' WHERE id=? AND status='running'`
萬一原本的 worker 其實還活著、剛好這一刻寫完了，這個 UPDATE 會改到 0 筆，**不會把人家做好的結果蓋掉**。

---

## 7｜Atomic Claim 驗證：**已經是原子操作，沒有 race condition**

原始需求擔心「SELECT pending 然後 UPDATE running」兩段式操作會有競態。**實際上不是這樣寫的。**

認領用的是帶條件的單一 UPDATE，再用「這次真的改到幾筆」判斷有沒有搶到：

```sql
UPDATE ai_jobs SET status='running', started_at=..., attempts=attempts+1, worker_id=?
 WHERE id=? AND status='pending'
```
→ 檢查 `meta.changes`，為 0 就是被別台搶走，跳過。

程式碼裡有 2026-09-10 的註解專門說明這個設計（當時就是為了多台機器共用佇列才改成這樣）。

**實測驗證**（CASE 7）：模擬兩台機器對同一筆 pending 同時認領 → `changes` 分別是 `[1, 0]`，只有一台成功。
**生產環境實證**：今天的 log 出現過「⏭️ 已被其他裝置搶走，跳過」，機制確實在運作。

---

## 8｜回收機制放哪裡

**選 B：每次 tick 開頭執行**（`ai_worker.py` 的 `tick()`）。

理由：
- Worker 自己負責自己佇列的生命週期，符合既有架構
- 系統已經有 21 個 launchd 排程，**不需要為這件事再開第四個排程系統**
- 包在 try/except 裡，回收出錯不影響這一輪其他工作

---

## 9｜Logging

回收成功：
```
♻️ 回收卡住的工作 post_interview_rematch（1b621772），卡了超過 20 分鐘（原機器 MacBookPro），退回重做 attempt 1/3
```

永久失敗：
```
🚨 post_interview_rematch（1b621772）重試 3 次仍卡住，標為失敗不再重試
```

第一版**不發 Telegram**（避免例行噪音）。永久失敗的情況會留在 log 與 `ai_jobs.error`；若之後發現這類失敗變頻繁，再考慮加系統管理者告警——**絕不通知候選人**。

---

## 10｜測試結果：`tests/test_ai_jobs_recovery.py` → **11 項全過**

| Case | 驗證內容 | 結果 |
|---|---|---|
| 2 | 才跑 2 分鐘 → 不回收 | ✅ |
| 2b | 19 分鐘（門檻 20）→ 仍不回收 | ✅ |
| 3 | 卡 45 分鐘 → 退回 pending | ✅ |
| 5 | 達重試上限 → 標 failed 不再重試 | ✅ |
| 5b | 失敗時留下可讀的 error 原因 | ✅ |
| 6 | `call_notes_summary` 卡 2 小時也不自動回收 | ✅ |
| 6b | `client_report_synthesize` 不自動回收 | ✅ |
| 7 | **兩台機器同時認領 → 只有一台成功** | ✅ |
| 8 | 重啟後卡住的工作最終可被另一台救起執行 | ✅ |
| — | 門檻 > 實測最長執行時間 × 2 | ✅ |
| — | 白名單只含已驗證可安全重跑的 kind | ✅ |

測試資料全部用 `__test__` 前綴，跑完自動清除，不碰任何真實工作。

另：P3 原有測試 `tests/test_p3_rematch.py` **34 項仍全過**（無回歸）。

---

## 11｜順手修的 API 安全問題

`GET /admin/application/:id/recommendations` 原本對不存在的 application 也回 `200 + []`，導致「查詢失敗／id 打錯」與「真的沒有推薦」在畫面上完全無法區分——顧問會把一次查詢失敗讀成「AI 確定沒找到」。

**已修並實測**：
```
不存在的人選 → 404
真實人選（無推薦）→ 200 {"recommendations":[]}
```

---

## 12｜Rollback

| 要退什麼 | 動作 | 影響 |
|---|---|---|
| 停用回收機制 | 設 `AI_JOBS_STALE_MINUTES` 為極大值（例如 `999999`）+ 重啟 | 立即，等同關閉 |
| 完全移除 | `git revert` 該 commit + 重啟 daemon | 回到卡住就卡住的舊行為 |
| 404 修正回退 | `npx wrangler rollback`（backoffice worker） | 回到一律 200 |

**沒有資料庫變更，所以沒有資料層的回退需求。**

---

## 13｜READY_FOR_STAGE1_CANARY

✅ **是。** 但如開頭所述，Stage 1 已跑完，目前在 **Stage 2**，這個修正讓 Stage 2 更穩、也是進 Stage 3 的前提。

### 目前 production 生效中的設定（已驗證）

```
P3_REMATCH_ENABLED=1
P3_REMATCH_CREATED_AFTER=2026-09-18T19:49:47   ← 只跑這之後新面談的人
P3_REMATCH_MAX_PER_TICK=3
（AI_JOBS_STALE_MINUTES 未設 = 預設 20 分鐘）
```

### 若要回到 Stage 1 形式（指定名單）

```
P3_REMATCH_ENABLED=1
P3_REMATCH_ALLOW_APPLICATION_IDS=<完整 application id，逗號分隔>
P3_REMATCH_MAX_PER_TICK=1
```

### 🚨 改完設定的正確重啟與驗證方式

`launchctl kickstart -k` **不夠**（BUG 8：daemon 自動更新會 `os.execv` 重啟自己並沿用舊環境變數，新增的變數會被靜默漏掉）。

```bash
launchctl bootout   gui/$(id -u)/com.step1ne.aiworker
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.step1ne.aiworker.plist
launchctl kickstart gui/$(id -u)/com.step1ne.aiworker     # 沒起來時再補這行

# ⚠️ 一定要驗證實際帶到的變數，不要相信「plist 寫了就等於生效」
ps eww $(pgrep -f ai_worker.py | head -1) | tr ' ' '\n' | grep '^P3_'
```

⚠️ plist 的值**不可以含空白**（會被 PlistBuddy 截斷，BUG 11）。日期用 `T` 分隔，程式端會自動正規化。

---

## 附：這輪沒有做的事（依指示）

Candidate Master／P3-D／Vector DB／Embedding／Conversation State／Matching 重構／正式站 UI migration／Follow-up 新功能／自動聯絡候選人／Recommendation 採用後的動作／Job data normalization —— **一項都沒碰**，本輪只做 `ai_jobs` 基礎設施修復與一個小型 API 安全修正。
