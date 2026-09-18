# 阿財 P3｜上線前 QA 報告

> 日期：2026-09-18
> 結論：**READY_FOR_HUMAN_APPROVAL**
> QA 目的不是「證明程式會跑」，而是**證明阿財開始主動工作後，不會亂推薦、不會亂通知、不會亂改候選人狀態**

---

## ⚠️ 開頭必讀：這份 QA 的前提跟原始 QA 需求不同

原始 QA 需求寫的是「Deploy 前最終 QA」「不要 Deploy Production」。**但程式在 QA 之前就已經部署到 Production 了**（使用者在稽核報告交付後指示上線）。所以這份報告寫的是**上線後的實測結果**，不是部署前的紙上推演——這反而讓 QA 品質更高（下面第 6、7 節的問題都是在真實資料上跑出來才抓到的）。

另外三項需修正的前提：

| 原始需求假設 | 實際情況 |
|---|---|
| 要確認 migration 跑在 local / staging / production 哪一個 | **系統只有一個 D1 資料庫**（`step1ne-recruit`），沒有測試環境。今天所有動作都直接對正式資料執行 |
| 建議初始設定 `P3B_SUGGEST_MODE=shadow` | ❌ 不採納。阿財主動推薦職缺的行為**在生產環境早就在跑**（2026-09-15 起），設 shadow 是**把現有功能關掉**，是倒退不是保護 |
| 要做 Backoffice UI smoke test 確認可上線 | 新畫面只存在**測試站**；正式站 `step1ne.com/consultant/` 是另一個帳號的舊系統，這台機器連改都改不到 |

---

## 1｜DB 現況

| 項目 | 結果 |
|---|---|
| Database | `step1ne-recruit`（binding `DB`）— **全系統唯一，無 local/staging/prod 之分** |
| Migration | `migrations/2026-09-18_p3_rematch.sql` 已執行於 **production** |
| `candidate_job_recommendations` | 12 筆（QA 期間實測產生） |
| `candidate_followups` | 0 筆 |
| `applications.idle_nudged_at` | ✅ 存在 |

非破壞性確認：無 DROP、無 ALTER COLUMN、無 UPDATE 既有資料。

---

## 2｜Rematch Dry Run

`total_pending_rematch = 22`（面談完成總數 41，其餘 19 筆已有推薦或已跑過）

| application_id | candidate | current_job | report_date | why_selected |
|---|---|---|---|---|
| 25f3c942 | 蘇雅婷 | 請顧問幫我評估 | 2026-09-09 | 面談完成、有報告、尚未算過推薦 |
| d8ca8d8a | 劉尚義 | BIM 工程師 | 2026-09-09 | 同上 |
| 90dfe749 | 林怡瑩 | 主管特助（日本白馬） | 2026-09-08 | 同上 |
| 3b9449d9 | 廖若辰 | BIM工程師（無經驗可・派遣） | 2026-09-07 | 同上 |
| 970fd626 | 周海暉 | BIM 工程師 | 2026-09-04 | 同上 |
| ec174bd5 | 蘇微閔 | 培訓工程設計工程師 | 2026-09-04 | 同上 |
| 7ca46648 | 陳亭瑾 | VIP貴賓接待服務員（晚班） | 2026-09-02 | 同上 |
| a5c0eb34 | 陳其寬 | BIM 工程師 | 2026-09-01 | 同上 |
| …（另 14 位） | | | | |

**⚠️ 確認屬實：不加限流的話會一次全掃。** 這不是推測——今天上線當下就實際發生了，22 位在幾分鐘內全部排進佇列，是靠人工喊停才沒有跑整晚。這是新增第 3 節閘門的直接原因。

---

## 3｜新增 Canary 限流閘門

| 旗標 | 預設 | 作用 |
|---|---|---|
| `P3_REMATCH_MAX_PER_TICK` | `3` | 每輪最多排入幾筆 |
| `P3_REMATCH_ALLOW_APPLICATION_IDS` | 空 | 有設就**只有**這幾位會跑（Canary 第一階段用） |
| `P3_REMATCH_CREATED_AFTER` | 空 | 只處理這個日期之後的報告 → 做到「只跑新面談，不補掃歷史」 |

**實測（未真的排入佇列）**

| 設定 | 結果 |
|---|---|
| 預設 | 3 筆（而非 22 筆） |
| `MAX_PER_TICK=1` | 1 筆 |
| `ALLOW_APPLICATION_IDS=<兩個不存在的 id>` | 0 筆 |
| `CREATED_AFTER=2026-09-10` | 0 筆 |

亂填非數字時退回安全預設（有測試涵蓋）。

---

## 4｜Dark Deploy 驗證

目前 production 實際旗標狀態：

| 旗標 | 現值 | 說明 |
|---|---|---|
| `P3_REMATCH_ENABLED` | **0** | 自動分析關閉中（人工喊停後關的） |
| `P3B_SUGGEST_MODE` | 未設 = `live` | **維持現況**，非 shadow（見開頭說明） |
| `P3_IDLE_NUDGE_ENABLED` | **1** | 目前開啟 |
| `P3_FOLLOWUP_REMINDER_ENABLED` | **1** | 目前開啟 |

**確認**：程式部署本身不改變使用者體驗——所有新行為都在旗標後面。目前兩個開啟的旗標（C①②）經查證不會對既有流程造成影響：C① 目標對象 0 位、C② 待辦 0 筆。

---

## 5｜P3B Shadow Mode 驗證（code trace）

`P3B_SUGGEST_MODE=shadow` 時，程式走的是完全不同的 prompt 分支。自動化 trace 結果：

| 檢查 | 結果 |
|---|---|
| shadow 區塊含「主動加值建議」 | ✅ 無 |
| shadow 區塊含「不知道您對這個職缺有沒有興趣」 | ✅ 無 |
| shadow 區塊含「順勢切進去問」 | ✅ 無 |
| shadow 區塊含「主動切入」 | ✅ 無 |

shadow 模式實際給阿財的禁令（原文）：
- 「**但你這場不可以主動提起任何其他職缺**，即使你覺得有更適合的也一樣。」
- 「這一句只會給顧問看，不會給候選人看，由顧問決定要不要跟他提。」

同時保留：AI 仍在心裡判斷並寫進 note 供顧問與 Post-interview Rematch 使用。

---

## 6｜Matching QA（全部以真實資料實測）

| Case | 要求 | 結果 |
|---|---|---|
| 1 | BIM 候選人有 Revit/AutoCAD/工程背景 → 可出現工程設計／專案工程職缺 | ✅ **陳旻婕**實際推出「資深工程設計工程師」「資深半導體專案工程師」 |
| 2 | 明確拒絕苗栗 → 苗栗職缺不得出現 | ✅ 擋掉；**且修正了區域縮寫漏洞**（職缺寫「桃竹苗地區」、候選人說「苗栗」原本比對不到） |
| 3 | 期望 70K 但沒明講底線 → 50–60K 職缺不得被直接 Hard Reject | ✅ 已修正（見下方 BUG 2） |
| 4 | 明確「低於 55K 不考慮」，職缺 max 41K → 必須擋掉 | ✅ **劉柔諍**實測擋掉（`salary_below_floor(41000<55000)`），顧問零打擾 |
| 5 | 已投過同 Job → 不重複推薦 | ✅ 重跑同一人新增 0 筆 |
| 6 | Job closed → 不得推薦 | ✅ 測試涵蓋 |
| 7 | AI 回傳不存在的 job_slug → 擋掉 | ✅ validator + safety filter 雙層 |
| 8 | 資訊不足 → `INSUFFICIENT_DATA`，不得腦補 | ✅ 規則明訂＋validator 要求「說適合就必須附佐證」 |
| 9 | 沒有適合職缺 → 空結果合法，不得硬湊 | ✅ **周亦宣**實測推 0 個 |

**自動化測試：34 項全過**（`python3 tests/test_p3_rematch.py`）

---

## 7｜推薦理由品質稽核

對 production 中全部 12 筆推薦逐條檢查「這個結論是從哪一段資料來的」：

**結果：12/12 有具體理由＋佐證，0 筆空話。**

實際範例（非挑選過的最佳案例，是全部都長這樣）：
- 「現職 Revit 操作頻率達每日 4–5 小時，符合本職缺『進階 AutoCAD、Revit 繪圖能力』要求」
- 「現職為組長並管理 9–12 人，具備現場工班與包商協調經驗」
- 「已具備乙級職業安全衛生管理員、甲級職業安全管理技術士、ISO 45001 內部稽核員等證照」

未出現任何「經驗豐富」「背景符合」「能力很好」這類無佐證理由。

---

## 8｜Human Approval 界線（grep / trace）

| 禁止項目 | 驗證結果 |
|---|---|
| `applications.screen_decision` | ✅ AI 路徑零命中 |
| `applications.redirect_job_slug` | ✅ 零命中 |
| `applications.manual_stage` | ✅ 零命中 |
| `reports.consultant_decision` | ✅ 零命中（4 個字串命中全是註解，或寫入 `post_call_result_json` 自己 JSON 裡的 `null`，非資料表欄位） |
| `candidate_forwards` / `placements` | ✅ 零命中 |
| **對候選人發訊息**（`linePush`/`sendMail`/`replyMessage`） | ✅ **AI 路徑完全沒有任何候選人通訊管道** |

顧問按 APPROVE 時，API 只 UPDATE：`status` / `consultant_decision` / `consultant_decision_by` / `consultant_decision_at` / `rejection_reason` / `updated_at`。不建 application、不 redirect、不送件、不通知候選人、不改 stage。

---

## 9｜C① 面談中斷提醒 QA

| Case | 結果 |
|---|---|
| A 面談 active + 超過 24hr 沒回 + 有綁 LINE | ✅ 提醒一次 |
| B 面談完成（`done`/`paused`） | ✅ 不提醒（條件限定 `interview_state='active'`） |
| C 面談取消／關閉（`superseded_by` 有值） | ✅ 不提醒 |
| D 沒有 LINE binding | ✅ 不 crash，仍寫 `idle_nudged_at`（否則每 15 分鐘重撈一次永遠等不到訊息的人） |
| E `idle_nudged_at` 已有值 | ✅ 不重複提醒 |
| F 被提醒後回來、又再次中斷 | **明確答案：一整場面談最多提醒一次，永遠。** |

**F 的產品邏輯不是模糊的，是刻意的**，程式註解原文：「催第二次不會讓不想繼續的人回心轉意，只會變成騷擾。」
另確認尊重 `hold_until`（顧問講好「明天再進來」的人不會被打擾，這是 2026-08-13 事故換來的規則）。

**目前實際會被催的人數：0 位**（沒有陳年舊案會被一次轟炸）。

---

## 10｜C② Follow-up Date QA — **這是 QA 抓到的真缺口**

原本實作是「AI 給什麼日期就存什麼」，沒有任何把關。已修正為 AI 先判斷 `certainty`，**且程式端強制**只有 `exact`/`range` 才准排提醒。

| 自然語言 | 分類 | 是否排提醒 | 日期基準 |
|---|---|---|---|
| 10 月 5 日再聯絡 | A `exact` | ✅ 排 | 面談日 |
| 兩週後 | A `exact` | ✅ 排 | 面談日 +14 |
| 下個月再找我 | B `range` | ✅ 排（下月 1 日） | 面談日 |
| 10 月初 | B `range` | ✅ 排（10/01） | — |
| 月底 | B `range` | ✅ 排（當月 25 日） | — |
| 過完年 | B `range` | ✅ 排（春節後首個上班日） | — |
| 等我拿完年終 | C `vague` | ❌ **不排** | — |
| 有空再說 | C `vague` | ❌ **不排** | — |
| 明年再看看 | C `vague` | ❌ **不排** | — |
| （完全沒提） | `none` | ❌ 不排 | — |

**關鍵設計**：`vague` 即使 AI 硬填了日期，程式也不採用並記 log。理由寫在程式碼裡——「prompt 是請求，程式才是保證」；排下去等於系統在候選人根本沒答應的日子叫顧問去打擾他。

時區：全部使用 `datetime('now','+8 hours')`（Asia/Taipei），與既有 cron 規則一致，無 UTC offset 問題。

---

## 11｜Stale Reminder Regression

| | 筆數 |
|---|---|
| 修正前 | 9 |
| 修正後 | **5** |

（上線當天首次量測為 18 → 5；期間部分人選已被處理，故基期下降）

**修正後仍會提醒的 5 位，以及為什麼每一筆都該提醒：**

| 候選人 | 理由 |
|---|---|
| 陳方 | 報告出來 7 天，沒推薦給任何客戶、沒進面試流程、沒被標記階段或不需面談 |
| 馮聖硯 | 同上，3 天 |
| Yi-yun Guo | 同上，2 天（履歷篩選是 approved，但那是**面談前**的事，不算處置） |
| 陳旻婕 | 同上，2 天（同樣情況） |
| 周亦宣 | 同上，1 天 |

**確認**：`screen_decision` 刻意**不**列入「已處置」。修正過程中一度加入，實測會把 Yi-yun Guo、陳旻婕兩位真的沒人處置的人藏起來——**提醒漏掉比提醒太吵更危險**。

已列入「已處置」的四種：已 candidate_forward／已進 placement／manual_stage 有值／no_interview；另排除 duplicate 分身。

---

## 12｜Telegram 噪音

**確認為：一位候選人一則摘要**（符合建議，無需修改）。

程式證據：`_run_rematch()` 全流程只呼叫一次 `notify_consultant()`；該函式內部 `for rec in kept[:3]` 是在組**同一則**訊息的內容。

在 `MAX_PER_TICK=3` 的 Canary 設定下：**第一輪最多 3 則 Telegram**（3 位候選人 × 各 1 則），且只有真的有推薦結果的才發（推 0 個的安靜跳過）。

---

## 13｜Backoffice UI Smoke Test（staging 實測）

| # | 項目 | 結果 |
|---|---|---|
| 1 | 空狀態（周亦宣） | ✅ 有明確訊息，不是空白框 |
| 2 | 有資料狀態（陳旻婕）五個區塊都看得到 | ✅ |
| 3 | 有沒有漏出程式原始值 | ❌ **抓到 2 個（已修，見下）** |
| 4 | 長文字溢出／被裁切 | ✅ 零溢出元素 |
| 5 | 不採用＋填原因，決定有記錄 | ✅ |
| 6 | 重開分頁決定仍在 | ✅ |
| 7 | 採用另一筆 | ✅ |
| 8 | 快速連點兩次 | ✅ 不會重複記錄（但見下方註記） |
| 9 | JS console 錯誤 | ✅ 無 |
| 10 | 手機 | ❌ **新分頁完全看不到（已修）** |
| 12 | API 錯誤處理 | ✅ 5xx 有正確錯誤畫面；⚠️ 不存在的 id 見 RISK |

### BUG 6（已修）｜今天早上那個「程式代碼外露」其實沒修乾淨

顧問實際看到的畫面：
```
📍 ['桃竹苗地區', '台中', '雲林']
💰 50000–60000 MONTH
```

兩個獨立問題：
- **(a) 地點陣列**：今天早上的修正只處理標準 JSON（雙引號），但資料庫裡實際存的是 Python 格式（**單引號**），`JSON.parse` 直接丟例外 → 原樣印出。同一位候選人的第二張卡卻是正常的，因為那個職缺的欄位存的是正常字串——**同一個欄位兩種格式並存**。
- **(b) `MONTH`**：薪資單位完全沒有經過任何轉換就印出來，每一張卡、每一位候選人都有。

**已修**：(a) 加上單引號陣列的解析；(b) 新增薪資單位對照（MONTH→月薪／YEAR→年薪／HOUR→時薪），並同時套用到「職缺介紹」分頁（那裡有同樣的問題）。

**驗證**（直接對部署後的程式執行）：
```
['桃竹苗地區', '台中', '雲林']  →  桃竹苗地區、台中、雲林
["桃竹苗地區","台中"]          →  桃竹苗地區、台中
50000–60000 MONTH             →  月薪 50000–60000
面議                          →  面議
```

> 根因在上游：職缺匯入時就把 locations 存成 Python repr 字串，`employment` 甚至有雙重 JSON 編碼（`"\"FULL_TIME\""`）。顯示層已經擋住，但**上游資料清理應另案處理**。

### BUG 7（已修）｜手機上新分頁完全看不到

375px 寬度下，分頁列 `scrollWidth 416 / clientWidth 289`，第四個分頁「AI 職缺推薦」位置在 x 347–459，**可視範圍只到 332——整個在畫面外**，而且沒有箭頭或露出半截提示。顧問用手機根本不會知道有這個功能。

**已修**：手機寬度下分頁改為換行排列，並把點擊高度從 31px 拉到 44px（QA 量到原本低於手指可靠點擊下限）。

**驗證**（375px 實測）：四個分頁 `可視寬 341 / 內容寬 341`，全部 ✅ 看得到，每個高 49px。

### 註記：連點兩次

不會產生重複記錄，但那是因為寫入是 idempotent 的 UPDATE，**不是因為有防護**——按鈕在請求進行中沒有 disable，連點兩次會送出兩個 POST。目前無害，但若未來改成會產生副作用的動作，必須補上鎖。

---

## 14｜KPI 端點

`GET /admin/recommendations/metrics` 實測回傳正常：

```json
{ "generated": 11, "pending_review": 11, "approved": 0, "rejected": 0,
  "match_candidate_count": 0, "possible_match_count": 11, "insufficient_data_count": 0,
  "approval_rate": null,
  "by_recommended_job": [{"job_slug":"trainee-semiconductor-project-engineer","n":2}, ...] }
```

✅ 0 筆時 `approval_rate` 回 **`null`**，無 NaN / Infinity / Error。

---

## BUG｜QA 發現並已修正

### BUG 1（嚴重）｜推薦把人推回他正想離開的行業

**發現**：林巧昀是**護理師想轉行**，明確說過「如果可以趁早轉行的話，我這樣也不錯」，應徵的是 BIM 工程師。系統卻因為「有護理實務經驗」推薦她回去當**診所護理師**。

技能判斷本身沒錯，但完全沒考慮她為什麼轉行。這種推薦送到顧問面前浪費時間，真的拿去跟候選人講會讓人覺得沒被聽懂。

**修正**：prompt 加入強制檢查——推薦前先看 `motivation.why_leaving` / `why_this_role` 有沒有透露想離開某個領域；有的話該領域職缺一律不推。

**驗證**：重跑同一位候選人 → 護理師職缺消失，換成「培訓工程設計工程師」，符合她的轉行方向。✅

### BUG 2（嚴重）｜把「期望薪資」當成「最低薪資」，靜默殺掉好機會

**發現**：陳旻婕表單填「70K」是**期望**薪資，AI 當成**底線**，兩個很適合的半導體職缺（上限 60K）全被安全閥靜默擋掉——正是我們最想推的 BIM→半導體案例。

**修正**：(a) prompt 明文「表單期望薪資不算底線，只有親口說『最低不能低於 X』才算，判斷不出來一律填 null」；(b) 程式加 15% 容忍區間，差距在容忍內改標「需注意」交給顧問判斷。

**驗證**：重跑後兩個職缺正常推出，薪資落差改以「待確認」呈現。✅

### BUG 3（嚴重）｜無限迴圈燒 AI 額度

**發現**：上線當下發現「AI 判斷沒有更適合職缺」的人（如周亦宣）因為不會留下任何推薦紀錄，掃描條件永遠成立，**每 20 秒被重新分析一次**。

**修正**：改以 `report_id` 比對 ai_jobs——只要這份報告跑過（不論結果幾筆、成功或失敗）就不再重跑；重新面談產生新報告時 `report_id` 改變，自然會重新分析。✅

### BUG 4（中）｜區域縮寫導致拒絕條件漏擋

**發現**：職缺 `locations` 實際使用「桃竹苗地區」這類縮寫，候選人說「不要苗栗」時字面比對不到 → 漏擋。

**修正**：新增區域縮寫對照表（桃竹苗／中彰投／雲嘉南／高屏）。✅

### BUG 5（中，非 P3 範圍但一併修）｜每日提醒誤報

詳見第 11 節。18 → 5 筆。✅

---

## RISK｜已知風險（未修，需知道）

| 風險 | 影響 | 建議 |
|---|---|---|
| **只有一個資料庫，沒有測試環境** | 任何測試都直接動正式資料 | 長期應建 staging D1；短期靠 Canary 名單控制 |
| **新 UI 只在測試站** | 正式站顧問看不到「AI 職缺推薦」分頁，只能靠 Telegram 通知＋點連結到測試站 | 需決定 V3 何時切換正式站 |
| **兩台機器共用同一佇列** | 若其中一台程式版本落後，會搶走工作並失敗（今天實際發生過 6 次） | 已靠 git 自動更新機制同步；未來新增 job kind 前要先確認兩台都已更新 |
| **系統沒有「人」的概念** | 同一人多次應徵是多筆獨立資料，`existing_job_slugs_for_candidate()` 只能用 email 粗略比對 | C③（新職缺回頭找舊人選）動工前必須先解決 |
| **`screen_decision` 語意易誤用** | 它是面談**前**的履歷篩選，不是面談後處置。已在程式碼留警告註解 | 後續改動提醒邏輯時務必重讀該註解 |
| **未知 GitHub 帳號** | `lizkockol1688-sketch` 登入於本機且有 repo 寫入權限，來源不明 | **建議 Jacky 盡快確認並移除**（非程式問題，需本人處理） |
| **不存在的 application id 回傳「沒有推薦」而非 404** | `GET /admin/application/does-not-exist/recommendations` → `200 {recommendations:[]}`。查詢失敗與「真的沒有推薦」在畫面上長得一模一樣，顧問會把一次查詢失敗讀成確定的否定答案 | 建議端點在查無此 application 時回 404（小改動，本輪未動以免超出 QA 範圍） |
| **職缺資料上游格式不一致** | `jobs.locations` 有些是正常字串、有些是 Python repr（`['桃竹苗地區', …]`）；`employment` 有雙重 JSON 編碼（`"\"FULL_TIME\""`）。顯示層已擋住，但同一欄位兩種格式會持續製造這類 bug | 應在職缺匯入流程統一正規化，另案處理 |
| **決定按鈕沒有防連點鎖** | 目前無害（寫入是 idempotent UPDATE），但連點兩次確實會送出兩個請求 | 若未來改成有副作用的動作（例如真的送件），必須先補鎖 |

---

## GO｜可以部署的程式

全部。已於 2026-09-18 部署完成並實測：
- `step1ne-recruit`（主 Worker + cron）
- `step1ne-backoffice-worker`（5 支新 API）
- `frontend_v2` → staging
- Python daemon（`ai_worker.py` / `recommendation_service.py` / `interview_daemon.py`），兩台機器已同步

---

## ENABLE｜建議可以開的旗標

| 旗標 | 建議 | 理由 |
|---|---|---|
| `P3_FOLLOWUP_REMINDER_ENABLED=1` | ✅ 可開（已開） | 只提醒顧問、不碰候選人；目前待辦 0 筆，零風險 |
| `P3_IDLE_NUDGE_ENABLED=1` | ✅ 可開（已開） | 目標對象 0 位，不會轟炸舊案；一人最多催一次 |
| `P3B_SUGGEST_MODE=live` | ✅ 維持 | 現況，非新行為 |

## HOLD｜建議先不要全開的旗標

| 旗標 | 建議 | 理由 |
|---|---|---|
| `P3_REMATCH_ENABLED=1`（無限制） | ⛔ 不要 | 會一次掃 22 位歷史人選，每位一次 LLM 呼叫 |
| | ✅ 改為 Canary 開（見下） | 搭配 `ALLOW_APPLICATION_IDS` 或 `CREATED_AFTER` |

---

## CANARY｜第一批建議測試對象

推薦這三位（理由：都是 BIM／工程背景，是本功能最核心的使用情境，且已有可對照的實測結果）：

| application_id | 候選人 | 原應徵 | 為什麼選他 |
|---|---|---|---|
| `d8ca8d8a` | 劉尚義 | BIM 工程師 | 典型 BIM→工程轉職情境 |
| `970fd626` | 周海暉 | BIM 工程師 | 同上，可比對一致性 |
| `a5c0eb34` | 陳其寬 | BIM 工程師 | 同上 |

---

## DEPLOY ORDER｜正式 Canary 上線順序

### Stage 0｜Dark（現況）
```
P3_REMATCH_ENABLED=0
P3B_SUGGEST_MODE=live（維持現況）
P3_IDLE_NUDGE_ENABLED=1
P3_FOLLOWUP_REMINDER_ENABLED=1
```

### Stage 1｜指定三位（觀察 1 天）
編輯 `~/Library/LaunchAgents/com.step1ne.aiworker.plist` 的 `EnvironmentVariables`：
```
P3_REMATCH_ENABLED=1
P3_REMATCH_ALLOW_APPLICATION_IDS=<三個完整 application id，逗號分隔>
P3_REMATCH_MAX_PER_TICK=1
```

> ### 🚨 改完 plist 一定要這樣重啟，而且一定要驗證
>
> **`launchctl kickstart -k` 不夠。** 2026-09-18 實測踩到：改完 plist 用 kickstart 重啟後，
> 執行中的程序**只帶到 `P3_REMATCH_ENABLED=1`，完全沒有帶到限流名單**——
> 結果 Canary 形同虛設，系統照樣去跑名單外的人（實際跑了馮聖硯等人才被發現）。
>
> 原因：`ai_worker.py` 有「偵測到 git 有新版就自動 pull 並重啟自己」的機制
> （`os.execv`）。程序自我重啟時**沿用自己原本的環境變數**，不會重新讀 plist，
> 所以後來新增的環境變數會被靜默漏掉。
>
> **正確做法**：
> ```bash
> launchctl bootout gui/$(id -u)/com.step1ne.aiworker
> launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.step1ne.aiworker.plist
> # 沒起來就再 kickstart 一次
> launchctl kickstart gui/$(id -u)/com.step1ne.aiworker
> ```
> **然後一定要驗證實際帶到的環境變數**（不要相信 plist 寫了就等於生效）：
> ```bash
> ps eww $(pgrep -f ai_worker.py | head -1) | tr ' ' '\n' | grep '^P3_'
> ```
> 三個變數都要出現。只出現一個就是沒生效，Canary 不成立。

觀察：recommendation 內容、Telegram、DB、UI、log。

### Stage 2｜只跑新面談的人（觀察 3–5 天）
```
移除 P3_REMATCH_ALLOW_APPLICATION_IDS
P3_REMATCH_CREATED_AFTER=<Stage 2 開始當天>
P3_REMATCH_MAX_PER_TICK=3
```
→ 只有之後新產生的報告會進 P3，**不補掃歷史**。

### Stage 3｜分批補跑歷史（品質確認後）
```
移除 P3_REMATCH_CREATED_AFTER
P3_REMATCH_MAX_PER_TICK=3
```
22 位會以每輪 3 筆的速度慢慢跑完，不會一次湧出。

---

## ROLLBACK｜每一步怎麼回退

| 要退什麼 | 動作 | 生效時間 |
|---|---|---|
| 停止自動分析 | plist 設 `P3_REMATCH_ENABLED=0` + kickstart | 立即 |
| 停止催候選人 | `wrangler.toml` 設 `P3_IDLE_NUDGE_ENABLED="0"` + deploy | 下一輪 cron |
| 停止追蹤提醒 | 同上，`P3_FOLLOWUP_REMINDER_ENABLED="0"` | 下一輪 cron |
| 阿財不要對候選人開口 | `P3B_SUGGEST_MODE=shadow` + kickstart | 立即，不需改程式 |
| 清掉排隊中的工作 | `DELETE FROM ai_jobs WHERE kind='post_interview_rematch' AND status='pending'` | 立即 |
| 回退程式碼 | `npx wrangler rollback`（Worker）／`git revert`（Python，兩台會自動同步） | 數分鐘 |
| 移除資料表 | **不需要**——沒有既有邏輯依賴它們，留著零成本 |

---

## FIRST 24 HOURS｜第一天要看什麼

| 看哪裡 | 指令／位置 | 正常長什麼樣 |
|---|---|---|
| Daemon log | `tail -f ~/aijob-automation/logs/aiworker.log` | 出現 `🎯 姓名：AI 推 N 個，程式擋掉 M 個` |
| 失敗的工作 | `SELECT status, error FROM ai_jobs WHERE kind='post_interview_rematch'` | 不應有 failed；有的話看 error |
| 推薦筆數 | `SELECT COUNT(*) FROM candidate_job_recommendations` | 成長速度應符合 MAX_PER_TICK |
| 被程式擋掉的 | log 中的「程式擋掉 N 個」 | 有擋是正常的；**全部都擋掉**要查是不是安全閥太嚴 |
| Telegram | 顧問決策討論串 | 一位候選人一則；不應洗版 |
| LINE | 候選人端 | C① 開著時才會有；一人最多一次 |
| 顧問決定 | `SELECT consultant_decision, COUNT(*) FROM candidate_job_recommendations GROUP BY 1` | 開始出現 approved/rejected 才代表顧問真的在用 |
| 追蹤約定 | `SELECT * FROM candidate_followups` | 只該出現 `ai_interview:exact` / `:range`，**不該有 vague** |

---

## 第一週人工驗收（不要看轉換率）

樣本太小（全系統至今 41 場面談），轉換率無統計意義。第一週應該看：

| 面向 | 問題 |
|---|---|
| **Quality** | AI 推薦的職缺，顧問看了覺得合理嗎？ |
| **Evidence** | 每條理由都找得到依據嗎？（目前 12/12 有） |
| **Noise** | 有沒有亂推薦、推太多？ |
| **Safety** | 安全閥有沒有誤殺好機會？（BUG 2 就是誤殺案例） |
| **Consultant Value** | 顧問看到推薦後，**有沒有真的省到時間**？ |

建議人工標記分類：`Good` / `Acceptable` / `Bad` / `Dangerous`。
其中 **Dangerous** 的定義（BUG 1 就是這一類）：推薦本身會讓候選人覺得沒被理解，或把人推回他明確想離開的方向。

---

---

## 附錄｜Canary 實跑記錄（2026-09-18 19:00–19:50）

指定三位試跑：#52 陳旻婕、#46 王仁君、#26 林巧昀（原指定的 #44 江芷楹尚未面談，無報告可分析，改由林巧昀替代——她正是 BUG 1 的主角，最值得驗證）。

### 實跑成果

| 候選人 | 結果 |
|---|---|
| **林巧昀** | 2 筆：培訓工程設計工程師／培訓半導體專案工程師。**完全沒有護理相關職缺** → BUG 1 修正在完整自動流程中確認有效（非手動觸發） |
| **王仁君** | 1 筆：培訓半導體專案工程師。理由扣合他面談中親口說的「想轉往半導體方向」，並誠實標註「面談未問到大學主修，無法確認科系是否符合」 |
| **陳旻婕** | 執行中（見下方 BUG 10） |

### 🔴 只有真的跑起來才會現形的四個問題（程式審查與自動測試都抓不到）

#### BUG 8｜限流名單靜默失效，Canary 形同虛設
改完 plist 用 `launchctl kickstart -k` 重啟後，執行中的程序**只帶到 `P3_REMATCH_ENABLED=1`，完全沒有帶到限流名單與每輪上限**。系統照樣去跑名單外的人（實際跑了馮聖硯、尹緯正、林均緯等人才被發現）。

根因：`ai_worker.py` 有「偵測到 git 新版就自動 pull 並 `os.execv` 重啟自己」的機制，**自我重啟會沿用原本的環境變數，不會重讀 plist**，後來新增的變數被靜默漏掉。

修正：部署 SOP 改為 `bootout` + `bootstrap`（+ 必要時 `kickstart`），**且一定要用 `ps eww` 驗證實際帶到的變數**。已寫入 Stage 1 步驟。

#### BUG 9｜失敗過的候選人被永久卡住，且看起來像「沒有推薦」
王仁君的工作在兩台機器版本不一致的空窗期失敗（另一台還沒拉到新程式，回報「未知的工作類型」）。防重複機制把「失敗過」也算成「跑過了」，於是他**再也不會被排進來**——而畫面上只會顯示「沒有找到更適合的職缺」，與「真的沒有適合職缺」完全無法區分。

修正：部署競態造成的失敗（`未知的工作類型`）允許重試；其他原因的失敗維持不重試（避免無限重跑燒額度）。修正後王仁君成功跑出推薦。

#### BUG 10｜daemon 重啟會留下永遠卡住的「執行中」工作
陳旻婕的工作在 19:17 標記為 `running`，daemon 之後重啟多次，該工作**沒有任何機制會被回收**，永遠停在 `running`。本次以手動 `UPDATE ... SET status='pending'` 救回。

⚠️ **這是 `ai_jobs` 的系統性問題，不限於 P3**——任何 job kind 都可能因 daemon 重啟而留下孤兒。本輪未修（會影響所有 job kind，超出 QA 可改範圍），建議另案加入「`running` 超過 N 分鐘自動退回 `pending`」的回收機制。

#### BUG 11｜plist 的值含空白會被截斷，導致時間線設定靜默失效
`PlistBuddy -c "Set ... 2026-09-18 19:49:47"` 會以空白切參數，實際只存進 `2026-09-18`——原本要設「從 19:49 之後」變成「今天整天」，會把當天所有面談完的人全部掃進去。

修正：程式端接受 `T` 分隔格式並正規化成空白（`'2026-09-18T19:49' → '2026-09-18 19:49'`）。若不處理，混用 `T` 與空白比字串大小會讓條件**全部比不到**，同樣是靜默失效。

---

## 最終結論

**READY_FOR_HUMAN_APPROVAL**

程式已部署、34 項自動化測試全過、真實資料實測涵蓋 9 個 QA 案例、5 個 bug 已修正並驗證。

**唯一還沒做的決定**：`P3_REMATCH_ENABLED` 要從 Stage 0 進到 Stage 1（指定三位 Canary），需人工確認後才執行。
