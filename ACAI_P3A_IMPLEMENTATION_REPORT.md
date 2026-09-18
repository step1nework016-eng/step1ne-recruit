# 阿財 P3｜實作交付報告（P3-A ＋ P3-B ＋ P3-C①② ）

> 日期：2026-09-18
> 範圍：選項 1（A＋B＋C①＋C②；C③「新職缺回頭找舊人選」延後）
> 狀態：**程式與測試完成，尚未部署。等人工確認後才上線。**
> 依據：`ACAI_P3_CURRENT_STATE_REPORT.md`（現況稽核）＋ 產品規格
> 對照組：本輪所有「AI 只建議、不執行」的界線，都在程式層有實際防線，不只是靠 prompt 約束

---

## 零、一句話總結

候選人跟阿財面談完，系統會自動分析「有沒有其他更適合的開放職缺」，產生**有佐證、可稽核**的結構化推薦，主動通知顧問；**AI 不會聯絡候選人、不會改任何狀態、不會送件**。另外補上兩條自動追蹤（面談中斷催一次、到期的追蹤約定提醒顧問）。

真實驗證（生產資料，非模擬）：候選人**陳旻婕**應徵 BIM 工程師，系統自動找出「資深工程設計工程師」與「資深半導體專案工程師」兩個可能更適合的職缺，理由全部附逐字稿／履歷佐證——**這正是規格裡 BIM→半導體的目標情境**。

---

## 一、修改了哪些檔案

| 檔案 | 動作 | 內容 |
|---|---|---|
| `step1ne-recruit/recommendation_service.py` | **新增** | 職缺推薦共用服務：`list_matchable_jobs()`／候選人快照／決定性安全閥／寫入／通知顧問 |
| `step1ne-recruit/ai_worker.py` | 修改 | 新增 `post_interview_rematch` handler、prompt、validator、`_run_rematch()` 主流程、`scan_rematch_candidates()` 輪詢；`tick()` 掛上輪詢 |
| `step1ne-recruit/interview_daemon.py` | 修改（小） | P3-B：新增 `P3B_SUGGEST_MODE` 開關與 shadow 模式的 prompt 分支 |
| `step1ne-recruit/src/index.js` | 修改 | C①面談中斷催一次、C②追蹤約定到期提醒；**另修好「每天提醒不準」的既有 bug** |
| `step1ne-backoffice-worker/src/index.js` | 修改 | 5 支 API（讀推薦／記錄決定／KPI／待辦清單／待辦完成） |
| `step1ne-backoffice-worker/frontend_v2/src/{views,app,adapters}.js`＋`styles.css` | 修改 | 顧問後台「AI 職缺推薦」分頁 |
| `step1ne-recruit/migrations/2026-09-18_p3_rematch.sql` | **新增** | DB migration |
| `step1ne-recruit/tests/test_p3_rematch.py` | **新增** | 20 項安全閥測試 |

**沒有動**：`interview_daemon.py` 的 `context_for()`／`build_prompt()` 主結構／`messages` 生命週期／`interview_state` 生命週期／`finish()` 主流程。

---

## 二、DB Migration（已執行，非破壞性）

`migrations/2026-09-18_p3_rematch.sql`，全部是新增：

1. **`candidate_job_recommendations`**（新表）— AI 推薦與顧問決定，含稽核欄位（`model`／`prompt_version`／`source_report_id`／當下的 `candidate_snapshot_json`／`job_snapshot_json`）
   - 唯一索引 `(source_report_id, recommended_job_slug)` → **idempotency 由資料庫保證**
2. **`candidate_followups`**（新表）— 有到期日的追蹤約定（系統過去完全沒有「未來要做的事」的資料模型）
3. **`applications.idle_nudged_at`**（新欄位）— 面談中斷催一次的防重複旗標

**沒有** DROP、沒有 ALTER COLUMN、沒有 UPDATE 任何既有資料。
**為什麼開新表而不用既有的 `candidate_job_match`**：那張是對外獵才 sourcing 專用（由 `matching_engine.py` 寫入），語意是「我們主動去外面找到的人 vs 某職缺」；這張是「已經進來面談過的人 vs 其他開放職缺」，生命週期與審核流程都不同，混用會讓兩邊查詢都要一直排除對方。

---

## 三、新增 API

| 方法 | 路徑 | 用途 |
|---|---|---|
| GET | `/admin/application/:id/recommendations` | 讀某人選的推薦（join `jobs` 帶職缺標題／地點／薪資） |
| POST | `/admin/recommendation/:recId/decision` | 記錄顧問決定（`approved`／`rejected`＋原因） |
| GET | `/admin/recommendations/metrics` | KPI：產生數／待審／採用／不採用／**採用率**／各職缺分布／人均推薦數 |
| GET | `/admin/followups?status=pending` | 追蹤約定清單 |
| POST | `/admin/followup/:id/done` | 標記追蹤完成 |

**經查證確認：這 5 支端點完全沒有寫入** `screen_decision`／`redirect_job_slug`／`manual_stage`／`reports.consultant_decision`／`placements` 任何一個欄位。

---

## 四、Matching 流程

```
（每 20 秒）ai_worker.tick()
   └─ scan_rematch_candidates()      ← 旗標關閉時整段跳過，連查都不查
        找「面談完成 + 有報告 + 還沒算過推薦」→ 排進 ai_jobs
             ↓
        _run_rematch()
          1. build_candidate_snapshot()   applications ＋ reports.content_json
          2. list_matchable_jobs()        ★ 全系統唯一的「可推薦職缺」定義
          3. LLM                          抽出明確拒絕條件 ＋ 薪資底線 ＋ 追蹤約定 ＋ 推薦
          4. _validate_rematch()          格式／職缺是否存在／有無佐證
          5. hard_safety_filter()         ★ 決定性安全閥（不信任 LLM 自己會排除）
          6. save_recommendations()       寫入，DB 唯一索引擋重複
          7. save_followup()              C②：把「下個月再聯絡我」變成有到期日的待辦
          8. notify_consultant()          Telegram 給顧問（**絕不給候選人**）
```

### 為什麼觸發點選「事後輪詢」而不是「報告產生時 queue」

規格原本寫「reports 建立後 queue job」，但建報告的程式碼就在 `interview_daemon.py` 的 `finish()` 裡，而稽核結論是那支不能動。改成事後輪詢有三個好處：

1. `interview_daemon.py` 的面談流程**一行都不用改**（風險最高的部分零接觸）
2. **idempotency 免費**——「有沒有推薦紀錄」本身就是判斷依據
3. 之前積的舊案子會自動被掃到，不用另外寫補跑腳本

### 對原始規格做的 5 處修正（都有實測依據）

| # | 原規格 | 問題 | 改成 |
|---|---|---|---|
| 1 | 安全閥要擋「候選人明確拒絕的地點／型態／薪資」 | **系統根本沒有這種結構化欄位**（`location_ok` 是一整句自由文字） | 先讓 LLM 把拒絕條件抽成結構化，程式再拿它過濾。**判斷歸 AI，執行歸程式** |
| 2 | `match_status` 用 `strong_match/possible_match/...` | 跟 `matching_engine.py` 既有的 `MATCH_CANDIDATE/...` 只差一個值，等於又開第四套詞彙 | 直接沿用既有四個值 |
| 3 | 新開 `candidate_job_recommendations` | 沒交代既有 `candidate_job_match` 怎麼辦 | 明文寫清楚兩者分工（見第二節） |
| 4 | reports 建立後 queue | 會需要動 `finish()` | 改事後輪詢（見上） |
| 5 | — | 原規格沒處理：面談中阿財**已經**會口頭推薦（生產環境已發生） | 加 `P3B_SUGGEST_MODE` 開關統一管控，見第六節 |

---

## 五、決定性安全閥（就算 AI 判斷錯也擋得住）

`recommendation_service.hard_safety_filter()` 會擋掉：

| 情況 | 行為 |
|---|---|
| 職缺已不是 open/active | 擋掉（產生期間被關閉也擋得到） |
| 候選人原本應徵的職缺 | 擋掉 |
| 已應徵過／已推薦過 | 擋掉（idempotency 第二道） |
| 命中候選人明確拒絕的條件 | 擋掉，含**區域縮寫展開**（職缺寫「桃竹苗地區」、候選人說不要「苗栗」也擋得到） |
| 職缺薪資上限低於底線 **超過 15%** | 擋掉 |
| 職缺薪資上限低於底線 **15% 以內** | **不擋**，改標成「需注意」交給顧問判斷 |
| AI 虛構不存在的職缺 | 擋掉 |

### ⚠️ 實測抓到、已修正的重大問題

第一次跑陳旻婕時，兩個很適合的半導體職缺**全被薪資閥靜默擋掉**。追查發現：她表單填的「70K」是**期望**薪資，AI 卻當成**底線**。實務上期望 70K 去談 60K 的缺完全值得聊——這種取捨該留給顧問，不該被系統悄悄殺掉。

兩處修正：
1. **Prompt**：明文寫死「表單的期望薪資不算底線，只有候選人親口講『最低不能低於 X』才算，判斷不出來一律填 null，填 null 遠比填錯安全」
2. **程式**：加 15% 容忍區間，差距在容忍內改標「需注意」而不是刪掉

修正後同一位候選人的兩個職缺都正常推薦出來（見第九節）。

---

## 六、P3-B：阿財在面談中的行為

**重要發現與決策**：稽核確認阿財**現在就已經會**在面談中主動推薦其他職缺（`interview_daemon.py:1469-1519`），生產環境已發生過。因此「預設不開口」會是**功能倒退**，不是保護。

改成加一個隨時可切的開關：

| `P3B_SUGGEST_MODE` | 行為 |
|---|---|
| `live`（**預設**，維持現況） | 聽到明顯對得上的，主動問候選人有沒有興趣了解 |
| `shadow` | **對候選人絕口不提**，只在心裡判斷並寫進給顧問看的 note |

切換方式：launchd plist 的 `EnvironmentVariables` 設 `P3B_SUGGEST_MODE=shadow` → `launchctl kickstart -k gui/$UID/com.step1ne.interview`（或對應的 daemon label）。**不需要改程式、不需要重新部署。**

用途：萬一發現推薦品質不穩或候選人覺得被推銷，可以立刻讓它閉嘴但**仍保留判斷給顧問看**，而不是把整個能力關掉。

---

## 七、Human Approval（AI 不得越線）

| 界線 | 程式層的實際防線 |
|---|---|
| AI 不得填顧問決定 | 寫入時 `status` 固定 `'pending_review'`，`consultant_decision` 欄位在 AI 路徑上**從未被賦值** |
| AI 不得改 pipeline | `_run_rematch()` 只寫兩張新表；5 支 API 經 grep 確認零 pipeline 寫入 |
| AI 不得聯絡候選人 | 推薦結果只走 Telegram 給顧問；C② 到期提醒同樣只給顧問 |
| AI 不得自動送件／淘汰 | 沒有任何呼叫 `applyScreenDecision`／`applyManualStage`／`doManualForward` 的程式路徑 |

顧問可做：**採用** / **不採用**（可填原因）。決定存在推薦表自己的欄位裡，不影響候選人目前狀態。

---

## 八、Feature Flag（全部預設關閉）

| 旗標 | 位置 | 預設 | 控制 |
|---|---|---|---|
| `P3_REMATCH_ENABLED` | Python 環境變數（`ai_worker.py`） | **關** | 面談後自動分析替代職缺 |
| `P3B_SUGGEST_MODE` | Python 環境變數（`interview_daemon.py`） | `live`（維持現況） | 阿財要不要對候選人開口 |
| `P3_IDLE_NUDGE_ENABLED` | Worker env var | **關** | C① 面談中斷催一次 |
| `P3_FOLLOWUP_REMINDER_ENABLED` | Worker env var | **關** | C② 追蹤約定到期提醒 |

> `wrangler.toml` 目前沒有 `[vars]` 區塊，兩個 Worker 旗標是嚴格比對 `=== '1'`，**未設定即為關閉**，所以現在部署上去是完全 dark 的。

---

## 九、測試結果

### 自動化測試：`python3 tests/test_p3_rematch.py` → **20 項全過**

涵蓋規格要求的案例：可轉職職缺不誤擋／明確拒絕地點被擋／**區域縮寫也擋得到**／薪資低於底線被擋／資料不足時不亂擋／已關閉職缺／原應徵職缺／重複推薦／外派拒絕／虛構職缺／非法 `match_status`／清單外職缺／無佐證卻說適合／超過 3 個／空推薦合法／旗標預設關閉。

### 生產資料實測（3 位真實候選人）

| 候選人 | 原應徵 | 結果 | 說明 |
|---|---|---|---|
| **陳旻婕** | BIM 工程師 | ✅ 推出 2 個 | **命中規格的 BIM→半導體目標情境**（見下） |
| 劉柔諍 | 日本項目財務會計 | 安全閥擋下 1 個 | AI 推的職缺月薪上限 41,000，低於她明確講過的 55,000 底線 → 正確擋掉，顧問零打擾 |
| 周亦宣 | 集團財務主管 | 推 0 個 | 沒有更適合的就誠實給空結果，**沒有硬湊** |

**陳旻婕實際產出**（顧問會收到的 Telegram）：

```
🤖 阿財找到可能更適合的職缺
候選人：陳旻婕   原應徵：BIM 工程師

▸ 資深工程設計工程師（可能適合）
  • 現職 Revit 操作頻率達每日 4–5 小時，符合本職缺「進階 AutoCAD、Revit 繪圖能力」要求
  • 現職負責結構檢討與機電規劃整合，與本職缺「高科技廠各系統工程電腦繪圖」性質相近
  • 可接受地點包含台中，與本職缺（桃竹苗、台中、雲林）重疊
  待確認：是否願意接受從住宅營造轉換至高科技廠的學習曲線
  待確認：薪資 50,000–60,000（低於期望 70K）是否可接受

▸ 資深半導體專案工程師（可能適合）
  • 現職為組長管理 9–12 人，具現場工班與包商協調經驗
  • 現職涉及鋼構廠商往來協調及結構機電界面檢討
  ...
這只是 AI 的建議，還沒有通知候選人、也沒有改動任何狀態。
```

每條理由都具體到可被查證，不是「背景相符」這種空話。

### 其他驗證
- **Idempotency**：同一人重跑 → 新增 0 筆（DB 唯一索引＋已推薦過的過濾雙重保障）✅
- **語法**：3 個 Worker `node --check` 全過；Python `py_compile` 全過；前端 eslint 無新增錯誤 ✅
- **測試期間未發送任何真實 Telegram**（攔截後驗證訊息內容）✅

---

## 十、順手修好的既有 Bug

**「每天提醒都不準而且很吵」**（Jacky 2026-09-18 回報）

查證屬實且比預期嚴重：該規則判斷「有沒有被處置」**只看 `reports.consultant_decision` 一個欄位**，但顧問實際上是按「推薦給客戶」或人已進 placements 流程——系統看不到，就一直催。

實測：整條規則會撈到 **18 筆，其中 9 筆早就處理完了**。最諷刺的是陳亭瑾：這條說她「沒人處置」，同一輪 cron 的 Pipeline 提醒卻說她「卡 10 天」——**兩則提醒自己在打架**。

修法：以下任一都算已處置 → 已推薦給客戶／已進 placements／有 manual_stage／標記過不需面談；另排除重複投遞的分身。

**結果：18 筆 → 5 筆，且 5 筆都是真的沒人處置。**

⚠️ 修的過程中一度把 `screen_decision IS NOT NULL` 也加進排除條件，實測發現會把 Yi-yun Guo、陳旻婕兩位**真的還沒處置**的人悄悄藏起來（那是面談**前**的履歷篩選決定，不是面談後的處置）。已移除並在程式碼留下警告註解——**提醒漏掉比提醒太吵更危險**。

---

## 十一、尚未完成 / 延後

| 項目 | 狀態 | 原因 |
|---|---|---|
| **C③ 新職缺→回頭找舊人選** | 延後 | 系統目前沒有「人」這個實體（只有「應徵」），同一人投三次是三筆獨立資料。需先建身分合併，屬獨立工程 |
| 既有兩套推薦邏輯改呼叫 `list_matchable_jobs()` | 未做 | 本輪先確保新的那套不製造第四種定義；收斂既有兩套會動到 `interview_daemon.py` 與 precall/postcall，風險超出本輪範圍 |
| `candidate_followups.done_by` | 未加 | 表上只有 `created_by`，API 先收下 `by` 但不寫入，避免覆蓋建立者 |
| Conversation Slot State 重構 | 延後 | 稽核報告列為最大技術債，但屬於「不要做」清單 |
| 前端在正式站看不到 | — | `frontend_v2` 目前只在 staging（`step1ne-consultant-staging.pages.dev`），尚未切換正式前端 |

---

## 十二、部署步驟（**請人工確認後再執行**）

⚠️ **順序很重要**：daemon 必須先更新到新程式，否則它會撈到不認得的新工作類型並標記失敗（測試時實際發生過）。

```bash
# 1. 先重啟本機 daemon（讓它認得新的工作類型）
launchctl kickstart -k gui/$(id -u)/com.step1ne.aiworker

# 2. 部署兩個 Worker
source ~/.config/workflow-os/cf.env
cd ~/claude-projects/工作流程技能包/step1ne-recruit && npx wrangler deploy
cd ~/claude-projects/工作流程技能包/step1ne-backoffice-worker && npx wrangler deploy

# 3. 部署前端（staging）
cd ~/claude-projects/工作流程技能包/step1ne-backoffice-worker/frontend_v2
npx wrangler pages deploy . --project-name=step1ne-consultant-staging --commit-dirty=true
# ⚠️ 驗證要用固定網址 https://step1ne-consultant-staging.pages.dev
#    不要用每次部署的 hash 前綴網址（CORS allowlist 只有固定那個）

# 4. 確認一切正常後，再逐一打開旗標（建議一次開一個，觀察一天）
#    - P3_REMATCH_ENABLED：改 com.step1ne.aiworker 的 plist EnvironmentVariables → kickstart
#    - P3_IDLE_NUDGE_ENABLED / P3_FOLLOWUP_REMINDER_ENABLED：在 step1ne-recruit 的
#      wrangler.toml 加 [vars] 或用 wrangler secret，設為 "1" 後重新 deploy
```

## 十三、Rollback

| 要回退什麼 | 怎麼做 | 影響 |
|---|---|---|
| **關掉全部新功能** | 把 4 個旗標拿掉／設回預設 | 立即生效，不需重新部署程式碼（除 Worker 旗標需 redeploy） |
| 阿財不要對候選人開口 | `P3B_SUGGEST_MODE=shadow` + kickstart | 立即生效 |
| 回退程式碼 | 兩個 Worker `npx wrangler rollback`；Python 檔 `git checkout` | 新表留著不影響（沒有程式會讀它） |
| 移除資料表 | `DROP TABLE candidate_job_recommendations; DROP TABLE candidate_followups;` | **不需要做**——沒有任何既有邏輯依賴它們，留著零成本 |

**新增的 `applications.idle_nudged_at` 欄位不用回退**：沒有程式讀它時就是一個沒人用的空欄位。

---

## 十四、Production 現況 vs 新架構

```
【原本】
候選人 → 面談(interview_daemon) → 報告(reports) → TG 推給顧問 → 顧問自己讀、自己判斷
                                      │
                                      └─ 阿財口頭推薦其他職缺 → 只存在報告散文裡，沒人接

【現在】（新增的是右邊那條旁路，左邊完全沒動）
候選人 → 面談(interview_daemon) → 報告(reports) → TG 推給顧問
   │                                  │
   │  P3-B 開關：live/shadow           └──▶【新】ai_worker 輪詢有報告但沒算過推薦的人
   │  控制要不要對候選人開口                      ↓
   │                                        候選人快照 ＋ 可推薦職缺清單（單一定義）
   │                                              ↓
   │                                        LLM 判斷 → 程式安全閥再篩
   │                                              ↓
   │                              candidate_job_recommendations（pending_review）
   │                                              ↓
   │                                     TG 通知顧問 ＋ 後台「AI 職缺推薦」分頁
   │                                              ↓
   │                                   顧問按【採用】/【不採用】→ 只記錄決定
   │                                   （不改 stage、不送件、不通知候選人）
   │
   └─【新】C① 面談中斷 24hr → LINE 催候選人一次（旗標控制）
     【新】C② 「下個月再聯絡我」→ candidate_followups → 到期 TG 提醒顧問
```

---

## 十五、第一個 KPI

**AI 推薦被顧問採用的比例** — `GET /admin/recommendations/metrics` 的 `approval_rate`。

⚠️ 期待值要設定好：全系統至今只有 58 筆應徵、約每週 1-3 場面談，**這個數字要好幾個月才有統計意義**。前兩週應該看的是「推薦內容讀起來合不合理」，而不是比率數字。
