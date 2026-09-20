# Pre-call Integration Audit

規格來源：`Step1ne_PreCall_Integration_Spec_v1.3_含UI參考.zip`（核心文件跟 v1.2 逐字相同，v1.3 只多了 `references/UI_REFERENCE_*` 兩份視覺參考，README/MANIFEST 版號更新）。
Production 來源：`step1ne-recruit`（D1 + 本機 daemon）、`step1ne-backoffice-worker`（顧問後台 API）、V3 前端（`frontend_v2`）。
本報告只做稽核與方案比對，**沒有動任何 Production 程式碼、DB、API**。

---

## 結論先講（給沒時間看全文的版本）

**這個 Pre-call 功能，Production 裡已經有大約 60–70% 的底層零件在跑，只是名字不同、分散在三個地方、沒有被組成一張卡片。** 真正要做的不是「蓋一個新系統」，是「把三條現成的資料流接成一張 UI」：

1. `jobs` 表已經是完整的職缺 source of truth（117 個欄位，含 `hard_filters`／`must_check_items`／`required_conditions`／`main_duties`／`client_screen_conditions`）——規格要的 Hard Gate 輸入，這裡全部找得到，**不需要新建 Job 資料表**。
2. `applications.call_prep_md` 已經是一個在跑的「電洽前準備」AI 產出欄位（`ai_worker.py.prompt_call_prep()`），輸出 `{summary, questions, talkingPoints, anticipatedQna}`——跟規格 00/01/03 文件要的東西**概念上是同一件事**，現在只是自由格式文字，沒有結構化成 Hard Gate／狀態機。
3. `matching_engine.py` 已經有一套更正式的「五分類＋三態判定」引擎（`hard_must/soft_must/nice_to_have/unknown/exclusion` → `matched/unknown/unmatched`），跟規格文件 02 的 Hard Gate 引擎**幾乎是同一套邏輯**，只是目前只用在人才搜尋比對（sourcing），沒有接到電洽這個場景。

最大風險不是「沒有資料」，是**如果不看這份稽核直接照規格新建欄位，會產生第二套「職缺硬條件」、第二套「必問題目」、第二套「候選人狀態」，跟現有的 `hard_filters`／`call_prep_md`／`consultant_call_notes` 各自紀錄互相不同步**——這在 Step1ne 過去已經發生過類似的事（`call_prep_md`／`call_summary_md`／`note` 三份候選人紀錄互相打架，2026-09 才修過一次 ReferenceError 版本）。

---

## A. Existing Architecture — Job → Candidate → Application/Pipeline 現況

Production 是「一張大表＋一個關聯表」的模型，不是三個獨立實體：

```
jobs（職缺，117 欄，slug 為主鍵）
   │  job_slug
   ▼
applications（應徵/人選紀錄，89 欄，這是真正的「候選人×職缺」關聯實體）
   │  application_id
   ├──▶ messages（阿財面談逐字稿 / LINE 對話）
   ├──▶ reports（初篩報告，含 content_json 結構化欄位）
   ├──▶ screenings（履歷篩選判定，score/verdict/hard_fail，跟面談報告分開的另一套）
   ├──▶ candidate_notes（type 分類的時間軸備註，含「電洽紀錄」type）
   ├──▶ candidate_forwards（推薦給客戶的紀錄）
   ├──▶ placements（成案後的階段追蹤）
   ├──▶ line_bindings（LINE 綁定，一對多）
   └──▶ turn_signals（面談中的離開/貼上行為快照）
```

**候選人本身沒有獨立主表**——`applications` 一列就是「一個人選投了一個職缺」的完整紀錄，人選的姓名/電話/Email/履歷都直接放在這張表上（不是外鍵指向一張獨立的 `candidates` 表）。`sourced_candidates`／`fresh_sourced_candidates` 是主動開發階段的候選人（還沒變成正式 application），轉換成 application 後才進入上面這條主線。

**職缺狀態**（`jobs.status`）與**應徵狀態**（`applications.status` / `manual_stage` / `interview_state`）是分開的兩層：職缺本身有開/關（`status`, `closed_by`, `closed_reason`），每個人選在這個職缺下有自己的進度（`interview_state`: pending/active/done, `manual_stage`: 顧問手動設的階段列）。`resolveStage()`（backoffice-worker）是把這些原始欄位換算成前端看到的階段標籤（`fill/decide/client/pool` 四分頁）的地方，**這是唯一的階段判斷邏輯來源**，不要另外發明一套。

**AI/對話相關現有模組**（跟這次規格直接相關的三條）：

| 模組 | 觸發時機 | 輸入 | 輸出欄位 |
|---|---|---|---|
| `interview_daemon.py`（阿財，AI 面談） | 候選人自己在線上跟 AI 對談 | 履歷＋職缺 hard_filters/must_check_items/到職障礙(blockers_json) | `messages` 逐字稿 → `reports.content_json` |
| `ai_worker.py: prompt_call_prep` | 顧問按「電洽前準備」 | 履歷全文＋職缺(title/required_conditions/main_duties)＋（如果有）阿財逐字稿 | `applications.call_prep_md`（JSON→MD：summary/questions/talkingPoints/anticipatedQna） |
| `ai_worker.py: prompt_client_report_synthesize` | 電洽結束、顧問要出客戶版報告 | 履歷＋逐字稿／電洽筆記＋`hard_filters`/`must_check_items` 清單 | `hard_filters[].status`（pass/partial/unknown）、`must_check_items[].status`、完整客戶版 JSON |

這三個已經涵蓋規格文件 00（電洽前準備）、01/03（必問題目）、02（Hard Gate 判定，只是現在叫 `hard_filters`）、05（部分：`hard_filters` pass/partial/unknown 概念上等於規格的 matched/unknown/unmatched）的核心邏輯，**只是分散在「電洽前」「電洽後」兩個時間點各自產生，中間沒有串成一個狀態機**。

---

## B. Field Mapping

逐項對照規格要的資料（依規格 schema JSON + docs 01-06 的欄位）：

| 規格欄位 | 對應 Production | 狀態 |
|---|---|---|
| `candidate.name` | `applications.name` | Existing |
| `candidate.target_role` | `applications.job_slug` → `jobs.title` | Existing |
| `candidate.current_role` | 履歷全文（非結構化）／`consultant_call_notes` | Derived（AI 從履歷文字抽取，沒有獨立欄位） |
| `candidate.years_experience` | 履歷全文 | Derived |
| `candidate.location` | `applications.location_ok`（布林，不是地點文字）＋履歷 | **Conflict**：規格要「地點文字」，Production 這欄是「地點是否可行的布林值」，語意不同 |
| `candidate.source` | `applications.source_channel` / `utm_source` | Existing（規格文件完全沒定義這欄要存什麼，Production 反而有明確的來源追蹤欄位，規格該採用這個而不是自創） |
| `call_goal`（規格：結構化 decision/target_role/validation_points/reason） | 無對應欄位，`call_prep_md` 是自由文字 MD，沒有結構化的「這通電話唯一目標」 | **Missing**（但可以從 `call_prep_md` 的 JSON 中間產物改造出來，不用整個重蓋——見 I 節） |
| `hard_gates[]`（criterion/category/classification/candidate_status） | `jobs.hard_filters`／`jobs.must_check_items`（label 陣列）＋ `reports.content_json.hard_filters[].status`（pass/partial/unknown） | Existing（**欄位存在，但只有「候選人目前狀態」在電洽後才寫入，規格要的是電洽前就要有 classification/source_strength 這些判斷過程，這段推理過程 Production 沒有保存，只留最終 pass/partial/unknown 結果**） |
| `hard_gates[].status` 三態（matched/unknown/unmatched） | 現有是 `pass/partial/unknown` 三態 | **Conflict**：三態語意相近但字面不同，需要 adapter 做值轉換，不要開兩套字典 |
| `must_ask_questions[]`（question/validates_gate/why_it_matters/backup_probe/answer_type） | `call_prep_md` 的 `questions[]`（純字串陣列，沒有 validates_gate/backup_probe） | Existing 但**結構不足**：現有的只是問題文字陣列，規格要的「這題驗證哪個 Gate」「備用追問」目前沒有 |
| `ai_flags[]`（risk_level/evidence_confidence/category） | 無直接對應。`reports.content_json.things_to_flag`（電洽後才產生，是給客戶看的溫和提醒，不是電洽前的風險旗標） | **Missing**（電洽前的風險旗標目前不存在，`things_to_flag` 是電洽後產物，時間點不對） |
| `post_call_decision.primary_route`（5 態） | `applications.manual_stage` / `screen_decision` / `candidate_forwards` 各自代表「推薦」「不推薦」「待定」等狀態，但**沒有統一的 5 態欄位**，也沒有規格要的「轉其他職缺」「人才池」這種明確路由值（雖然 `talent_pool` 概念存在，是另一張表 `sourced_candidates.status`，不是 `applications` 上的欄位） | **Conflict + Missing**：概念上有對應狀態分散在 3-4 個不同欄位/表，規格要的是單一 enum，需要 adapter 統一映射，不建議新增第 5 套狀態欄位 |
| `consultant_override_allowed` / AI建議 vs 顧問決定分開存 | `reports.consultant_decision` 已經是「顧問最終決定」欄位，AI 產的報告本身（`content_json`）是「AI 建議」，兩者本來就是分開存、互不覆蓋 | **Existing**（這個規格要的行為 Production 已經在做，只是沒用在電洽場景） |
| `resume_flags` / 履歷疑點 checklist | 無直接對應，履歷解析是阿財 `interview_daemon.py` 面談時的內部判斷（會问但不落地存成結構化清單） | Missing（電洽場景沒有這段，AI 面談場景有但不是同一支程式） |

---

## C. Job Integration — 怎麼直接綁定現有 Job，不重建 JD

**結論：不需要新表，`jobs` 表本身就是給這個功能用的資料源，只需要一層 adapter。**

`jobs` 表跟這次規格最相關的欄位已經全部存在：

- Hard Gate 輸入：`required_conditions`、`must_skills`、`hard_filters`（結構化 label 清單）、`must_check_items`（另一份客戶指定必查清單，跟 hard_filters 分開）、`client_screen_conditions`
- 職缺內容：`main_duties`、`nice_to_have_skills`、`preferred_background`、`personality_traits`
- 電話中會用到的現況資訊：`salary_min/max/unit`、`salary_tier_table`、`locations`、`work_mode`、`work_hours`、`employment`
- 到職邏輯：`onboard_by`、`urgency`、`interview_process`

**Adapter 應該做的事**（不是新建欄位，是把現有欄位「翻譯」成規格要的 Hard Gate 分類）：

```
jobs.required_conditions + jobs.hard_filters  ──▶  Hard Gate Classifier（規格 doc02 的 4 層判斷邏輯）
                                                    ──▶ hard_gate_analysis.gates[]（產出，可以是 runtime 計算，不用存）
jobs.nice_to_have_skills                       ──▶  nice_to_have 分類（不用再判斷，職缺自己已經分好類了）
```

**重要發現**：`jobs.hard_filters` 本身已經是「顧問／AI 覺得這個職缺的門檻是什麼」的結構化清單（`{label}` 陣列，在 `prompt_client_report_synthesize` 裡逐項核對 pass/partial/unknown）——**規格 doc02 想要的「JD → Hard Gate 分類」這一步，Production 其實已經有一份人工/AI 產出的版本，只是這份清單是用在「電洽後核對」，不是「電洽前生成必問題目」**。最小改動方案是：讓 Pre-call 直接讀 `jobs.hard_filters` 當作 Hard Gate 候選清單，不用重新跑一次規格 doc02 的分類邏輯去產生一份新清單（除非 `hard_filters` 是空的，那時才即時用 AI 補一份，等同規格 doc02 的功能只在缺資料時才啟動）。

---

## D. Candidate Integration

| 資料 | 來源 |
|---|---|
| 可以直接用現有 Candidate Profile/Resume | `applications.resume_file_id`／`resume_url` → 履歷全文（透過 `fileB64()` + `env.AI.toMarkdown` 抽取，跟 `call_prep_md` 現有流程完全同一套，直接複用） |
| 電話前 AI derived data | 履歷全文丟給 AI 抽取 current_role/years/skills 這類——**現在 `call_prep_md` 的 prompt 已經在做這件事**（`prompt_call_prep()` 直接把履歷全文塞給 AI，不是先結構化再組 prompt），只是沒有把中間結果（AI 從履歷讀出的 current_role 等）另外存成獨立欄位，Pre-call 卡如果要顯示「已知資訊」區塊，這段可以直接複用 `prompt_call_prep` 的輸出，只是要多要求它多吐幾個欄位 |
| 電話後才需要保存的新資訊 | Hard Gate 逐項驗證結果（matched/unknown/unmatched）、AI Flag 是否 resolved、post-call route——這些目前**完全沒有電洽場景的落地欄位**，`hard_filters[].status` 雖然存在但是電洽後**產出客戶版報告時**才寫入，不是電洽當下即時記錄（顧問在電話中勾選「✅已確認符合」那個當下沒有東西可以寫） |

---

## E. Hard Gate Storage Strategy

逐項判斷該存 DB／runtime／AI derived／cache／不持久化：

| 資訊 | 建議策略 | 理由 |
|---|---|---|
| Hard Gate 清單本身（criterion/category/classification） | **優先 runtime 從 `jobs.hard_filters` 讀，缺資料才 AI derived**，不整批存新表 | 職缺內容變動時（顧問改了 JD）不需要同步兩份資料 |
| Hard Gate 分類過程（source_strength/為什麼判定為 hard/nice/pending） | **不持久化，或最多 cache**（跟同一支 job 的 AI 輸出一起短期快取） | 這是推理過程不是事實，職缺沒變的話下次可以重算，硬存反而變成第二個需要維護一致性的地方 |
| Must Ask 3 題 | **cache（跟 `call_prep_md` 一樣的模式）**，電洽前產生、電洽後可以丟棄或跟這次通話記錄一起存 | 現有 `call_prep_md` 就是這個模式（產生一次、下次電洽可能要重產），照抄即可 |
| AI Flag | 同上，cache | 跟 Must Ask 同一批產出，沒必要分開存 |
| Gate Result（電話中顧問即時勾的 matched/unknown/unmatched） | **存 DB**，這是電話當下的真實紀錄 | 這是「事實」不是「AI 猜測」，顧問按下去的結果如果沒存住，等於這通電話白打 |
| Post-call Decision（AI 建議路徑＋顧問最終決定） | **存 DB**，兩者分開存（呼應現有 `content_json`／`consultant_decision` 分離的既有模式） | Production 已經有這個模式在跑，照抄，不要合併成一欄 |

**具體建議**：新增 DB 欄位僅限「Gate Result」跟「Post-call Decision」這兩類真正的通話事實紀錄，其餘（Hard Gate 分類、Must Ask 題目、AI Flag）都走「跟 `call_prep_md` 同一種 runtime AI 產生＋暫存」模式，不需要為每一種都建一張新表。

---

## F. API Gap Analysis（列出缺口，這階段不實作）

現有可直接沿用：
- `fileB64()` + `env.AI.toMarkdown`（履歷讀取，已在 `call_prep_md` 端點用）
- `queueAiJob()` + `ai_worker.py` 的 `HANDLERS` 機制（新增一個 `precall_card` kind 即可，不用另外蓋一套排隊系統）
- `jobs` 表查詢（`SELECT ... FROM jobs WHERE slug=?`，`call_prep_md` 端點已經在用同樣的查詢模式）

需要新增（設計層面，不實作）：
1. `POST /admin/application/:id/precall-card`——比照 `call-prep` 端點的模式（查 job+resume → `queueAiJob('precall_card', ...)`），輸出比 `call_prep_md` 更結構化（含 hard_gates/must_ask/ai_flags 陣列，不是純 MD 文字）
2. `POST /admin/application/:id/precall-gate-result`——電話中顧問點擊 Gate 狀態時即時寫入（這是 E 節說「必須存 DB」的那部分，目前完全沒有對應端點）
3. `POST /admin/application/:id/precall-decision`——電話結束時存 AI 建議路徑＋顧問最終決定
4. `GET /admin/application/:id/precall-card`——讀取（如果還沒過期／職缺沒變，直接回快取結果，不重新呼叫 AI）

---

## G. Frontend Integration

現有 V3 顧問前端（`frontend_v2`）已經有的相關入口：

- `app.js:578`：候選人詳情面板裡已經有一顆「電洽前準備」按鈕（`application-call-note` action），這就是現有的 Pre-call 入口，只是目前點下去只顯示 `call_prep_md` 這段自由文字
- `app.js:557`：階段追蹤列有一個 `call` 節點（`done: !!application.call_summary_md`），代表系統已經知道「電洽」是流程裡的一個獨立節點
- `app.js:642`：候選人詳情面板的「安排面談」分頁下方已經有「電洽紀錄」區塊，顯示 `call_summary_md`

**建議整合點**：不新開一個獨立的「Pre-call」大分頁，而是**把現有「電洽前準備」按鈕點開的內容，從純文字 MD 升級成規格 06 講的三段式卡片**（Pre-call Card → Call Mode → Post-call Decision），仍然掛在候選人詳情抽屜裡，理由：

- 入口：沿用現有候選人詳情抽屜的「電洽前準備」按鈕，不用改導覽結構
- Job Context：抽屜本來就是某個 `application_id` 底下開的，`job_slug` 已知，直接查 `jobs` 表
- Candidate Context：抽屜本來就在顯示這個人選的履歷/資料，Pre-call Card 只是同一個抽屜裡的新分頁
- 開始電洽／Call Mode：可以是同一個抽屜切換成「通話中」視圖（比照現有「人選面談室」的分頁式設計，[views.js](../../../私人開發項目/frontend_v2/src/views.js) 已經有類似的分頁模式可以照抄）
- 電話後 Decision：通話結束後，同一個位置換成 Decision 卡片，取代現在單純顯示 `call_summary_md` 那段

---

## H. Risk / Duplication Check

| 風險 | 是否存在 | 說明 |
|---|---|---|
| 重複 Job logic | **高風險，若照規格原樣實作會發生** | 規格 schema JSON 自創 `candidate.target_role` 等欄位名，且完全沒有 job_id／候選人 UUID 這種外鍵概念（我方稽核已確認：規格三份文件裡，Must-ask 題目跟 Gate 的關聯欄位名稱在 doc03／template md／schema JSON 三處**互相不一致**：`validates_gate` / `validates` / `why`——這本身就是規格內部尚未收斂的訊號，套用時要選一個、不要三個都抄） |
| 重複 Candidate logic | 中風險 | `candidate.years_experience`／`current_role` 這類規格自創欄位，如果真的建表存起來，會變成履歷全文之外的第二份「候選人現況」，之後履歷更新這裡不會自動同步 |
| 重複 AI interview logic | 低風險 | 阿財面談（`interview_daemon.py`）跟電洽（顧問親自打）是兩條刻意分開的路徑（`consultant_call_report.py` 的設計本來就是為了不讓這兩者的報告格式分裂），Pre-call 卡是給顧問打電話用，不會跟阿財衝突，但**兩邊都在讀 `jobs.hard_filters`／`must_check_items`，要用同一套 adapter，不要各寫一份解析邏輯**（現有阿財的到職障礙 `blockers_json` 又是另一套獨立的、只給阿財用的資料，三套資料源都指向類似的「職缺硬條件」概念，這是現有系統本身已經存在、規格上線前就該一起處理的技術債） |
| 重複 pipeline/status | **高風險，若照規格 5 態路由原樣新增欄位會發生** | 現有 `manual_stage`／`screen_decision`／`candidate_forwards` 已經在表達類似語意，規格的 5 態（recommend/need_more_info/alternative_role/talent_pool/pause_recommendation）不建議變成第五套獨立狀態欄位，應該 adapter 映射到既有狀態，或最多新增一個「AI 建議路徑」欄位（純建議，不驅動流程），流程本身還是走現有 `manual_stage` |
| 造成資料不同步 | 確認存在既有先例 | Production 已經發生過 `call_prep_md`／`call_summary_md`／`note` 三份候選人紀錄互相不同步、甚至讓端點直接 ReferenceError 掛掉的真實事故（2026-09-14 修復記錄）。這次如果又加一個獨立的 Pre-call 資料層，會變成第四份候選人紀錄，風險同樣存在，**必須明確定義哪份是 source of truth** |

---

## I. Minimal-change Architecture（最少修改方案）

```
                    ┌─────────────────────────────────────┐
                    │  jobs 表（既有，不新建）              │
                    │  hard_filters / must_check_items /   │
                    │  required_conditions / main_duties   │
                    └───────────────┬───────────────────────┘
                                    │ 既有查詢，call-prep 端點已在用
                                    ▼
   applications.resume_file_id ──▶ Adapter（新，薄）
   （既有履歷）                     │
                                    ▼
                    ai_worker.py 新增一個 HANDLER：
                    'precall_card'（比照現有 call_prep 模式，
                    不是另開一套排隊機制）
                                    │
                                    ▼
                    輸出結構化 JSON（hard_gates/must_ask/ai_flags/
                    call_goal），存法比照 call_prep_md：
                    ── 不落地存 DB，或短期 cache 在
                       applications 新增 1 欄（precall_card_json）
                                    │
                                    ▼
                    V3 前端：既有「電洽前準備」按鈕升級顯示
                    （沿用 G 節的入口，不新開分頁）
                                    │
              顧問通話中點擊 Gate 狀態 ──▶ 新端點，寫入 DB
              （這是真正需要新 schema 的地方，見 E 節）
                                    │
                                    ▼
              通話結束 ──▶ 新端點存 AI建議路徑 + 顧問決定
              （比照 reports.content_json / consultant_decision
                既有的「AI建議、人工決定分開存」模式）
```

**只有兩處真正需要新 DB schema**：
1. `applications` 新增 `precall_card_json`（cache，可為 NULL，職缺沒變/在有效期內可重用，過期就是 NULL 觸發重新生成）
2. 新表或新欄位記錄「電話中即時 Gate Result」跟「電話後 Decision」（這是規格裡唯一 Production 完全沒有對應物、而且明確需要持久化的真實資料）

**其餘全部走 adapter，不新建 Job／Candidate／Pipeline 概念。**

---

## J. Implementation Plan（先不執行）

**Phase 1 — Adapter + 唯讀 Pre-call Card**
- 新增 `precall_card` AI job kind（複製 `call_prep` 的模式，改輸出結構化 JSON）
- Adapter：`jobs.hard_filters` → Hard Gate 候選清單（缺資料才即時用 doc02 邏輯分類）
- 前端：現有「電洽前準備」按鈕的內容從純 MD 升級成規格 06 的卡片版面（唯讀，不含 Call Mode 互動）
- 不新增任何寫入端點，先驗證「產出的內容顧問覺得有用」

**Phase 2 — Call Mode + Gate Result 落地**
- 新增 Gate Result 寫入端點與最小 schema
- 前端加 Call Mode 畫面（Gate 狀態三態按鈕、Quick Note、Exit Checklist）
- 這一步才真正產生規格要的「電話中即時紀錄」

**Phase 3 — Post-call Decision + 既有流程串接**
- 新增 Decision 端點，AI 建議路徑 + 顧問決定分開存
- Adapter 把 5 態路由映射回既有 `manual_stage`／`candidate_forwards`／`sourced_candidates.status`（人才池）流程，**不新建第五套狀態欄位**
- 跟現有「記錄新的通話」（`doCallNote`）流程合併，不要並存兩個「打完電話要做的事」入口
