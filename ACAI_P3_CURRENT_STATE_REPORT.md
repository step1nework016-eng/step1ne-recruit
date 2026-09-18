# 阿財 AI 面談系統｜P3 升級前 Production 現況稽核報告

> 稽核日期：2026-09-18
> 稽核方式：**以 Production source code 與 live D1 資料庫為準**，文件與 code 不一致時一律以 code 為準
> 稽核範圍：唯讀。本次稽核**沒有修改任何 production code、沒有新增 DB 欄位、沒有改任何 prompt**
> 目標讀者：另一位 AI 或產品規劃者，只讀這一份就能理解現況與 P3 缺口

---

## 零、執行摘要（先看這段）

### 三個最重要的發現

**1. 「AI 主動推薦其他職缺」不是 0，已經做到大約 6 成——但只到「講出來」為止。**
`interview_daemon.py` 早在面談當下就會把「其他 40 個開放職缺」塞進阿財的 prompt，並明確指示他在對話中主動推薦更適合的職缺（`interview_daemon.py:1080-1089` 撈職缺、`:1460-1519` 注入與指示）。**生產環境已有實例**：候選人吳秉洋應徵 `bim-engineer`，報告第一行就是 AI 自己寫的「建議送帆宣（無經驗軌）——無 Revit／Navisworks／AutoCAD 或工程製圖背景」。
**但天花板很明確**：輸出只是「對話中的一句話」＋「報告 markdown 裡的一段字」。沒有結構化欄位、不會自動寫 `redirect_job_slug`、不會建任何 match 紀錄、不會觸發任何通知。**人類顧問必須自己讀到那句話，再手動操作。**

**2. 「自動追蹤候選人」的基礎設施已經存在且穩定運行，只是被寫死成 6 個特定情境。**
真正的排程引擎不是本機 Python，是 Cloudflare Worker 的 `scheduled()`，每 15 分鐘跑一次（`step1ne-recruit/src/index.js` 約 `:5880-6469`，`wrangler.toml:13 crons = ["*/15 * * * *"]`）。它已經在做：面談後 5 分鐘推播、2 天沒進度推播、面試前一天提醒、報到前提醒、到職 Day1/3/7/28 關懷、顧問端逾期提醒。**而且 LINE 主動推播（push，非 reply）是真的能對候選人用的**，不受 replyToken 限制。
**缺的是通用化**：目前每個情境都是一組寫死的 SQL 條件＋一個 `*_notified_at` 防重複欄位。沒有「下一步動作」「下次追蹤日期」這種資料模型（前端註解明講 `applications` 從來沒有 `next_action`/`next_step` 欄位，`frontend_v2/src/app.js:458-462`）。

**3. 最大的結構性缺口不是 AI 能力，是「資料沒有結構化」。**
- 候選人端：`applications` 82 欄裡**完全沒有** 技能／年資／學歷／科系／產業／職務類別／語言／證照 這些欄位。這些資訊只以**敘事型 JSON** 存在 `reports.content_json`（例如 `work_history[]` 是自由文字的公司/職稱/期間字串，不是可比對的分類碼）。
- 職缺端：唯一結構化的 gate 欄位 `hard_filters` **37 個職缺裡只有 1 個有填**、`must_check_items` 只有 10 個、`years_min` 只有 4 個。其餘「必要技能」全是 `must_skills` / `required_conditions` 裡的中文散文。
- 結論：**現在所有「matching」之所以能運作，是因為把散文丟給 LLM 讓它自己判斷**，不是因為有可程式化比對的資料。這決定了 P3 的技術路線選擇（見第十三節）。

### 一句話回答「目前在哪」

**阿財目前是完整的 P1、半套的 P2、以及一個「意外已經存在但沒被結構化」的 P3 雛形。**

---

## 一、Production 實際流程（以 source code 為準）

```
候選人進來 → 建立 Candidate → 履歷解析 → 職缺綁定 → AI 面談
  → 面談結果 → AI 分析 → 顧問查看 → 顧問後續處理
```

| # | 步驟 | 層 | 實際位置 |
|---|---|---|---|
| 1 | 候選人進來／建立 Candidate | Frontend + API + DB | `step1ne-public-worker/src/index.js:4062` `POST /apply`；30 分鐘去重 `:4128-4148`；寫入 `applications` `:4188-4204`；產 `chat_token` `:4258-4260`；初始狀態 `interview_state='not_started'`, `status='pending_assessment'` `:4261-4263` |
| 2 | 履歷解析 | Python daemon + 外部工具 | `step1ne-recruit/parse_resumes.py:172` `extract()`：PDF→`pdftotext -layout`（fallback `pdfplumber`）`:178-198`；docx/doc/rtf→macOS `textutil`（fallback `docx2txt`）`:200-218`；txt/md `:220-221`；xlsx（日本履歷書）`:223-244`；URL/作品集抓取 `:278-523`（含 SSRF 防護）。另 `generate_pre_interview_note() :56-82` 呼叫 `claude -p` 產生自由文字筆記寫入 `applications.pre_interview_note :78` |
| 3 | 職缺綁定 | DB（slug 關聯，**非 FK 約束**） | `applications.job_slug` 於 `/apply` 寫入（`step1ne-public-worker/src/index.js:4193`），全站以 slug join `jobs` |
| 4 | AI 面談 | 前端 widget + Python daemon + LLM | 候選人端走 `step1ne-public-worker/src/index.js:4664` `/chat/*` HTTP 輪詢（`:4671-4977`，含 `/chat/:token/voice` 走 Workers AI Whisper 做口說語言驗證）。**產生回合的是獨立輪詢 daemon**：`interview_daemon.py:2584 handle()` → `context_for() :1098` → `build_prompt() :1186` → `run_claude() :1657`（subprocess 呼叫 `claude -p`）→ 解析 JSON → 寫 assistant `messages` 列 → 前端輪詢取得。主迴圈 `tick() :2821` |
| 5 | 面談結果 | DB | 逐字稿存 `messages`；房間生命週期存 `applications.interview_state/interview_started_at/interview_ended_at`（`step1ne-public-worker/src/index.js:4804-4875`） |
| 6 | AI 分析 | LLM ×2 | `interview_daemon.py:2277 finish()` 第一次呼叫產 Markdown 報告（`:2347`）；第二次 `report_to_json() :2214-2274`（`:2239`）把 Markdown 轉結構化 JSON，經 `_normalize_report_json() :2039-2213` 正規化；`save_report() :448` 寫入 `reports.content_md` / `content_json` |
| 7 | 顧問查看 | 推播 + 後台 | (a) 主動推：`deliver_after_interview() :795-887` 送顧問版/客戶版 PDF＋履歷＋KPI 按鈕到 Telegram（`:868-880`）；(b) 拉取：`https://step1ne.com/consultant/reports/`，後端讀 `reports.content_json/md`（`step1ne-backoffice-worker/src/index.js:9705, :10413, :10881, :10957`） |
| 8 | 顧問後續處理 | 後台 + DB + cron | 顧問動作寫 `applications.manual_stage` / `screen_decision` / `consultant_call*` 與 `placements`；`step1ne-recruit/src/index.js:6238 candidateCareTick()`、`:6386 pipelineReminders()`（15 分 cron）自動提醒顧問並推播候選人 |

### ⚠️ Repo 結構的重要修正（規劃時務必注意）

| Repo | 實際身分 |
|---|---|
| `step1ne-recruit/src/index.js`（6,468 行） | **主 Worker**（`wrangler.toml` → `name = "step1ne-recruit-api"`, `crons = ["*/15 * * * *"]`）。**全系統最重要的排程引擎在這裡**，不是在 Python |
| `step1ne-recruit/*.py`（約 40 支） | 本機 launchd daemon／cron 腳本，包含 `interview_daemon.py`（阿財本體）、`ai_worker.py`（AI 佇列處理器） |
| `step1ne-public-worker/src/index.js`（5,254 行） | 候選人面向：`/apply`、`/chat/*`、`/line-webhook`、`/assessment/:token` |
| `step1ne-backoffice-worker/src/index.js`（約 12k 行） | 顧問後台 `/admin/*`、`/consultant/reports/` |
| `step1ne-messaging-relay/src/index.js`（87 行） | 只做 HMAC 驗簽＋Service Binding 轉發，**無商業邏輯** |

---

## 二、P1｜AI 初談能力盤點

| 能力 | 狀態 | 證據 |
|---|---|---|
| 履歷解析 | 🟡 | 只有純文字抽取，parse 階段**不產生任何結構化欄位**（`parse_resumes.py:172-246`）。**圖檔 PDF 直接拒絕、無 OCR fallback**（`:183-185`，`meaningful_ratio < 0.05`） |
| 基本資料取得 | ✅（表單）／🟡（履歷衍生） | 表單欄位完整寫入 `applications`；履歷衍生的結構化 `basics`（年齡/居住地/學歷/語言/證照）**只在面談後報告順便產出一次**（`interview_daemon.py:2094-2098`） |
| Candidate Profile | 🟡 | `applications` 有 82 欄，但**沒有獨立的結構化 candidate profile 表**。詳見第四節 |
| 面談上下文 | ✅ | `build_prompt() :1186-1642` 注入：技能 SOP、當前日期 `:1199`、到職障礙 `:1216-1233`、阿福健檢記憶 `:1239-1253`、職缺專業題庫 `:1265-1291`、初篩 verdict＋必問題 `:1295-1309`、應徵表單 `:1311-1322`、電洽摘要＋面談前筆記 `:1328-1334`、Big Five/Grit `:1341-1369`、完整職缺資料 `:1371-1456`、**其他開放職缺 `:1469-1519`**、社群連結 `:1528-1558`、履歷全文（上限 12,000 字）`:1560-1583`、完整對話歷史 `:1585-1595` |
| Hard Gate | 🟡 | **面談前算一次、面談中不強制**。`screen_resumes.py` LLM 算出 `hard_fail`（`:47, :82-83`）存 `screenings.hard_fail`，以文字注入 prompt（`interview_daemon.py:1299-1300` `🚨 硬性不符：`）。**沒有任何程式路徑會因硬條件不符中止或分支面談** |
| 地點／薪資／工作型態／到職日／必要技能 | 🟡 | 面談當下只是 prompt 文字指示；**只有面談結束後才結構化**成 `hard_conditions[]`（`:2119-2124`）、`blocker_findings[]`（`:2159-2164`）、`motivation{}`（`:2091-2093`）。`messages`/`applications` 沒有逐回合的結構化 slot |
| 面談問題生成 | ✅ | 混合動態：初篩 LLM 題（`screen_resumes.py:46`）＋`job_expertise.questions_json` 題庫（`build_expertise.py` 離線建，注入 `:1265-1291`）＋`blockers_json`（`:1216`）＋SKILL.md 階段腳本（`skill() :1128-1183`，依 seniority／語言裁切）。prompt 明確禁止逐字念題庫（`:1271-1272`） |
| Follow-up 追問 | ✅（靠 prompt，非狀態） | `:1290-1291`「含糊就追問」、履歷空窗追問 `:1572-1580`。能運作的前提是**每回合重送整份逐字稿** |
| 面談摘要 | ✅ | `finish() :2277` 產出 summary / one_liner / top_selling_point / top_risk / resume_vs_spoken 差異 / observations / fit_scores（六維加權 `:2154-2213`）/ consultant_followups |
| Risk / Flag | 🟡 | 事後掃描為主：`_BANNED_WORDS`/`_accuses() :2004-2019`、`law5_hits() :348`（就服法第 5 條）掃的是**已產生的報告**，只記警告 `:2183-2192`。唯一即時機制是面談中觸發第 5 條過濾時要求模型自我改寫（`:2604`）。**沒有即時對人類告警的機制** |
| 顧問可查看結果 | ✅ | TG 主動推播雙 PDF＋顧問後台拉取（見第一節第 7 步） |

### 🔴 最關鍵的 P1 缺口：沒有任何 Question / Slot State

**系統完全沒有 `answered` / `partial` / `unknown` / `conflict` / `declined` / `not_applicable` 這類逐題狀態。** 兩路證據：

1. **Schema 面**：live D1 `PRAGMA table_info(messages)` 回傳恰好是 `['id','application_id','role','content','created_at']`——沒有任何 slot／topic／status 欄位。其他表（`applications`／`job_expertise`／`screenings`／`assessments`）也都沒有逐題狀態。最接近的東西（`blocker_findings`、`expertise_findings`、`hard_conditions`）是**面談結束後一次性算出的產物**，不是面談中維護的狀態。

2. **Code 面**：`context_for() :1098-1110` 每回合無條件重抓
   `SELECT role, content, created_at FROM messages WHERE application_id=... ORDER BY id ASC LIMIT 200`（`:1108-1109`），
   `build_prompt()` 再原樣倒進 prompt（`:1585-1595`）。**回合與回合之間沒有任何中介狀態物件。**

**現在靠什麼避免重複提問？** 靠兩件事：(a) 每回合把整份逐字稿重送，讓 LLM 自己重讀推論；(b) **寫在 prompt 裡給 LLM 的自然語言指令**，例如 `:1231-1233` 明文要求「記下他的原話，不要自己幫他圓場、也不要當作問過了」。**防線在 prompt，不在程式。**

**後果（P3 規劃必須處理）**：
- 逐字稿超過 `LIMIT 200` 會靜默截斷最舊內容，此路徑**看不到任何壓縮或摘要保險機制**
- 硬條件到底有沒有被問到，**只能靠面談結束後 LLM 自我回報**，session 進行中無法驗證
- 每回合重付 token／延遲成本重新推導狀態
- **P3 要做的「依據已知資訊動態換職缺」需要一個可信的「目前已知什麼」狀態，而這個東西現在不存在**

---

## 三、P2｜AI 判斷與分流盤點

### 目前真實存在的分類：`content_json.verdict`

四個值，定義於 `interview_daemon.py:1995` `_VERDICTS`：
`值得轉給顧問` / `待顧問判斷` / `硬條件不符` / `資訊不足建議補問`

產生於報告 LLM 呼叫（`:2347`），若模型輸出不合法則 fallback 成 `待顧問判斷`（`:2052`）。

### 對照你要的 A–E 五分流

| 目標分類 | 狀態 | 說明與證據 |
|---|---|---|
| **A 優先聯繫** | 🟡 | 無專屬欄位。最接近的是 `verdict='值得轉給顧問'` ＋ 另一套**六維加權分數** `fit_scores.total`/`grade`(A/B/C/D)（程式端確定性計算，`:2163-2208`），註解明說是為了讓顧問決定「手上三個『值得轉給顧問』要先聯絡誰」（`:2164-2166`）。**但 grade 嚴禁出現在任何對外輸出**（`ai_worker.py:680-682`），且**沒有任何程式讀它來觸發通知或排序佇列** |
| **B 需顧問判斷** | ✅ | `verdict='待顧問判斷'` 是一級 enum，且是非法輸出的預設值，穩定產生 |
| **C 可轉其他職缺** | 🧪 | `_VERDICTS` 裡**沒有**這個值。只存在於 2026-09-18（本日）新增的 `ai_worker.py:728-787 prompt_postcall_result` 的 `ai_recommendation.route='alternative_role'`（`:725, :769`）。**生產資料：`applications.post_call_result_json` 58 列中只有 1 列有值**。另外，真正的 DB 欄位 `applications.redirect_job_slug` **58 列全為 NULL**，且只由人類顧問經 `applyScreenDecision()`（`step1ne-backoffice-worker/src/index.js:3332-3353`）寫入，**AI 從不自行寫入** |
| **D 人才池** | 🧪 | 同 C，只存在於新 prototype 的 `route='talent_pool'`。生產中真正的「人才池」是**面談前的履歷分流**：`screen_resumes.py` 把分數 <70 的履歷丟到 TG「履歷池」討論串（`:14-16`, `THREAD_POOL=304`），跟面談後分流是兩件事 |
| **E Hard Gate Fail** | ✅（部分） | `verdict='硬條件不符'` 是一級 enum。**且這是全系統唯一一個 AI 判斷結果會真的改變系統狀態的案例**：`content_json.language_verification.verdict='通過'` 才會寫 `applications.lang_verified_at`（`:2397-2398`），未通過則發 `🚨` TG 告警給顧問（`:2427-2449`） |

### 分類結果會不會影響後續 Workflow？

**絕大多數不會，只有一個例外。**

- `content_json.verdict`：**純描述性**。唯一被程式讀取的地方是後台的校準儀表板，用來比對「AI 判斷 vs 顧問實際決定」的一致率（`step1ne-backoffice-worker/src/index.js:11437-11488, 11701-11703`）——這是給人看的 QA 指標，**不驅動任何路由、通知或 UI 狀態**
- `language_verification.verdict`：**會**（唯一案例，寫 DB 旗標＋觸發專屬告警）
- `screen_decision`（approved/redirect/declined）：**會**驅動不同的 email 與狀態轉換（`applyScreenDecision`, `:3286-3366`）——但**100% 由顧問設定，AI 不參與，且發生在面談之前**
- `reports.consultant_decision`（forwarded/rejected/need_more）：**會**驅動候選人端不同的 LINE／email 文案（`:2777-2781`）並決定是否對客戶曝光（`:1769-1777, :4853`）——但**100% 由顧問寫入**

### 死欄位（規劃時不要誤用）

`reports.recommend`（58/58 NULL）、`reports.hard_pass`（58/58 NULL）、`reports.content_client_md`（58/58 NULL）、`applications.client_consent_at`（全 repo 查無任何讀寫點）。

---

## 四、職缺 Matching 的可用欄位盤點

### Candidate 側（`applications`，82 欄，58 列）

| 概念 | 欄位 | 型態 | 填充率 |
|---|---|---|---|
| 期望薪資 | `expected_salary` | **自由文字**（如「40000以上，可談」） | 47/58 |
| 可到職日 | `available_date` | 自由文字 | 47/58 |
| 可接受地點 | `location_ok` | 自由文字 | 46/58 |
| 備註 | `note` | 自由文字 | 26/58 |
| 人格特質 | `disc_d/i/s/c` + `disc_primary` | **結構化數值** | 42/58 |
| 社群連結 | `social_links`（JSON） | 結構化但極稀疏 | 1/58 |
| 電洽結構化結果 | `post_call_result_json` | 結構化（本日新增） | 1/58 |
| 電洽摘要 | `call_summary_md` | 散文 | 18/58 |

**完全不存在的欄位（重要）**：現職職稱、年資、技能、學歷、科系、產業、職務類別、求職動機、工作型態偏好、可接受地點（結構化清單）、語言、證照。
`applications` 表上**沒有** `current_title` / `skills` / `education` / `major` / `industry` / `function` / `motivation` / `work_mode_pref` / `languages` / `certificates` 任何一欄。

**這些資訊實際在哪？** 在 `reports.content_json`（58/58 有 `content_md`，46/58 有 `content_json`）。其結構包含 `verdict`、`one_liner`、`motivation{why_leaving, why_this_role, salary_gap, notice_period, other_offers, blockers}`、`work_history[]{employer, role, duration, source, nature}`、`resume_vs_spoken[]`、`hard_conditions[]{item, verdict, detail, evidence_source}`、`observations.assessment[]`、`for_client{}`、`consultant_followups[]`。

> **⚠️ 規劃時最關鍵的一句話**：`content_json` 雖然是 JSON，但它是**「敘事包在 JSON 裡」**，不是 matching-ready 的特徵向量。`work_history` 的 `role`/`duration` 是自由文字字串，沒有正規化分類碼；沒有 `skills: []`、`years_experience: N`、`industry: "..."` 這種可跨候選人程式比對的欄位。

### Job 側（`jobs`，100 欄，37 列，其中 28 列 `status='open'`）

| 概念 | 欄位 | 型態 | 填充率 |
|---|---|---|---|
| 必要技能 | `must_skills` | **自由散文**（抽樣 `bim-engineer` 為一整段中文，硬條件/後勤/加分項混在一起） | 29/37 |
| 加分項 | `nice_to_have_skills` | 自由散文 | 26/37 |
| **Hard Gate** | `hard_filters`（JSON array） | 結構化 `[{label, status:"unknown"}]`——但 status 永遠是 `"unknown"`，是「該問的清單」不是「已判定的 gate」 | **1/37** |
| 必要評估項 | `must_check_items`（JSON array） | 同上形狀 | 10/37 |
| 一般必要條件 | `required_conditions` | 自由散文 | 27/37 |
| 薪資範圍 | `salary_min`/`salary_max`/`salary_unit`/`salary_note` | **結構化數值** | 28/37 |
| 地點 | `locations` | 自由字串（非 enum／多選） | 33/37 |
| 僱用型態 | `employment` | 自由文字 | 35/37 |
| 年資要求 | `years_min` | 結構化數值 | **4/37** |
| 學歷 | `education_level` | 自由文字 | 13/37 |
| 科系 | — | **無專屬欄位**，混在散文裡 | — |
| 證照 | — | **無專屬欄位**，混在散文裡 | — |
| 語言要求 | `language_requirement` | 自由文字 | 10/37 |
| 到職時程 | `onboard_by` | 自由文字／日期樣字串 | 13/37 |
| JD 結構化嘗試 | `jd_spec_json` | JSON，但用於 JD 產生與刊登，**非 matching 用途** | 28/37 |
| 狀態 | `status` | enum（28 個 open） | 37/37 |

**結論**：職缺側唯一真正 gate 形狀的結構化欄位（`hard_filters`、`must_check_items`）只覆蓋少數職缺，而且欄位設計本身**從來不是為了被程式自動評估**——它是「顧問/面談官應該問這些」的清單。

---

## 五、Matching Engine 現況

### 有沒有 embedding / vector / semantic search？

**完全沒有。** 用 `grep -rniE "embedding|vector|pgvector|cosine|semantic.search|vectorize"` 掃過 `step1ne-recruit` 全部 `.py` 與 `step1ne-backoffice-worker` 全部 `.js`：**零命中**。沒有 embedding store、沒有向量索引、沒有相似度計算、沒有任何 embeddings API 呼叫。

### `matching_engine.py`（751 行）是什麼

- **定位（來自自身 docstring `:1-28`）**：2026-08-23 建立，是**「對外開發獵才（outbound sourcing）」流程的嚴格度把關器**，實作 `SOURCING_RULES.md` §15-16 的「找人的時候可以寬，推薦人的時候必須非常窄」。**與 inbound 面談流程無關**。
- **核心函式**：
  - `split_requirements(job_slug) :290`——把 `jobs` 欄位拉進一個空的五層骨架（HARD_MUST/SOFT_MUST/NICE_TO_HAVE/UNKNOWN/EXCLUSION）**等人手動填**，本身不做任何自動分類
  - `save_requirement_snapshot() :335` / `check_requirement_ready() :380`——存讀 `job_requirement_snapshot`；**沒填完就整個擋掉**（`SOURCING_BLOCKED_REQUIREMENT_INCOMPLETE`）
  - `evaluate_candidate(candidate_evidence, requirement_snapshot) :488`——核心。輸入是**一位候選人「已經被上游人/AI 判好」的逐維度證據 dict**（八維：EXPERIENCE/INDUSTRY/FUNCTION/SENIORITY/ENGLISH/LOCATION/TRAVEL/COMPENSATION，每維 PASS/FAIL/UNKNOWN＋證據品質＋來源），對上**一個**職缺的 requirement snapshot，輸出 `MATCH_CANDIDATE`/`POSSIBLE_MATCH`/`INSUFFICIENT_DATA`/`NOT_MATCH`。硬規則：任一 HARD_MUST=FAIL → 直接 `NOT_MATCH`；任一 HARD_MUST=UNKNOWN → 最高只能 `INSUFFICIENT_DATA`
- **它明確不做的事**：
  - **不做「一個候選人 → 搜尋所有職缺」**。沒有任何函式會迭代 `jobs` 表
  - **內部沒有任何 LLM 呼叫**（`grep subprocess.run|CLAUDE_BIN|--model` 於該檔零命中），是純確定性 Python 聚合邏輯
  - **完全沒有接進 `interview_daemon.py` 或 `ai_worker.py`**（在 `interview_daemon.py` 內 grep `matching_engine|evaluate_candidate|job_requirement_snapshot` 零命中）
- **實際使用規模**：`job_requirement_snapshot` **全表只有 2 列、2 個 job_slug**（對比 37 個職缺 / 28 個 open）。這是一個兩個職缺的試點，**沒有規模化使用**。
- **`candidate_job_match`（27 列）**：是更早期 Phase 2.1 的產物，`matching_engine.py` 的 docstring 明說這一輪**不動**這些舊紀錄，所以這 27 列可能根本不是它產生的。

---

## 六、「不適合 A 職缺 → 自動找到 B 職缺」現況

系統裡有**三個互不相連**的機制可能被誤稱為「AI 推薦其他職缺」，規劃時必須分清楚。

### 機制一：面談中主動轉推（**已存在、已在生產環境運作**）

- `interview_daemon.py:1080-1089`（`_fetch_static`）執行：
  `SELECT slug, title, main_duties, locations, salary_note, service_line, seniority, must_skills, required_conditions, nice_to_have_skills, language_requirement, personality_traits FROM jobs WHERE status='open' AND slug != {cur_slug} ORDER BY created_at DESC LIMIT 40`
  → 存成 `static['other_jobs']`
- `interview_daemon.py:1460-1519` 把這份清單注入阿財的**即時對話 prompt**，並明確指示（`:1482-1502`）：面談前先默默把清單對照履歷掃一遍；若對話中浮現明確且具體的匹配，就主動用提問方式推薦（「聽起來跟您剛剛講的…滿吻合的——不知道您對這個職缺有沒有興趣了解一下？」），並依候選人回答分支
- `interview_daemon.py:1503-1507` 要求把結果寫進輸出 JSON 的 `note` 欄位「讓顧問在報告裡看得到」
- `interview_daemon.py:2320-2335` 讓**報告撰寫階段**（Phase 7）再拿一次同樣的 `other_jobs` 清單＋完整履歷與逐字稿，把轉推建議寫進 `reports.content_md`

**生產環境實證**：報告 `r_1dc94c8d…`（候選人吳秉洋，應徵 `bim-engineer`）開頭即為 AI 自撰的「**建議送帆宣（無經驗軌）**——無 Revit／Navisworks／AutoCAD 或工程製圖背景，走無經驗培訓路線」。

**天花板**：輸出**只有散文**（對話中的一句話 ＋ markdown 報告裡的一段）。沒有結構化欄位、不自動寫 `redirect_job_slug`、不建 `candidate_job_match`、不產生任何通知或任務。人類顧問必須自己讀到，再手動去後台操作 `applyScreenDecision`（`step1ne-backoffice-worker/src/index.js:3339`）。

### 機制二：`alternative_jobs`（2026-09-18 本日新增，**面談中完全碰不到**）

- 位置：`ai_worker.py` 的 `prompt_precall_card()`（`alternative_jobs` schema 於 `:338`）與 `prompt_postcall_result()`（schema 於 `:764`），加上驗證器與確定性後過濾 `_filter_alternative_jobs() :844-883`（用關鍵字子字串比對排除候選人明確拒絕的條件，**不是** embedding、**不是** `matching_engine.py` 的 gate 邏輯）
- 職缺清單來源：`step1ne-backoffice-worker/src/index.js:10499-10507 fetchAlternativeJobCandidates()`
  `SELECT slug, title, salary_min, salary_max, salary_unit, locations, main_duties FROM jobs WHERE status IN ('open','active') AND slug != ? ORDER BY updated_at DESC LIMIT 20`
- **觸發路徑（關鍵）**：只在 `POST /admin/application/:id/precall-card`（`:10663-10680`）與 `POST /admin/application/:id/postcall-result`（`:10775-10790`）內被呼叫——**兩者都是顧問後台端點，需要顧問在 UI 上按「產生」**。它們把工作排進 `ai_jobs`，由本機 `ai_worker.py` 撈出執行
- **在 `interview_daemon.py` 內 grep `precall_card|postcall_result|ai_worker|ai_jobs`：零命中。** 面談 daemon 完全不知道這個機制存在
- 結果寫入 `applications.call_prep_md`（precall）或 `post_call_result_json`（postcall），只給後台前端顯示。要轉成行動還需要顧問第二次手動送出 `POST /admin/application/:id/postcall-decision` 帶 action（`:10793-10818`）

### 機制三：`matching_engine.py` — 見第五節，未接線、近乎未使用

### 直接回答：距離 P3 情境還缺什麼

你要的情境（BIM 硬條件不符 → 自動找到「半導體專案工程師」→ 阿財當場告訴候選人）：

1. **「找到並講出來」這半段已經有了**（機制一），而且已在生產環境發生過。所以 P3 的「AI 主動推薦其他職缺」**不是從零開始**。
2. **缺的是把散文訊號變成結構化、可觸發自動化的資料**：
   - (a) 讓 Phase 7 報告 prompt（`:2320-2343`）**同時**吐出一個 `alternative_jobs` 形狀的結構化區塊進 `reports.content_json`（這個 JSON 欄位已存在、目前沒用於此）。**機制二已經寫好的 schema、validator（`_validate_precall_card`）與後過濾（`_filter_alternative_jobs`）可以直接搬過來重用**
   - (b) 讓這個結構化區塊能自動落地（寫 `candidate_job_match` 或 `applications.redirect_job_slug`），今天這兩件事都必須人工執行 `applyScreenDecision`
   - (c) 主動通知顧問，而不是等顧問自己去開那一位候選人的 precall/postcall 畫面
3. **「自動追蹤」那半段幾乎沒有對應基礎**：所有與轉職缺相關的狀態轉換（`redirect_job_slug`、`screen_decision`、`manual_stage`）**今天全部由顧問觸發的後台路由寫入**，沒有任何 AI 流程會自動寫。
4. **資料完整度是前提**：`job_requirement_snapshot`（2/37）、`hard_filters`（1/37）、`must_check_items`（10/37）都近乎空的。**今天機制一與機制二能運作，正是因為它們把散文丟給 LLM 判斷，而不是比對結構化資料。** 若 P3 想走「確定性 hard gate 自動比對」路線，必須先補職缺側資料，這是一個獨立的資料工程專案。

---

## 七、自動追蹤能力盤點

### 排程基礎設施

**兩套並存**：

**(1) Cloudflare Worker cron（最重要，真正的追蹤引擎）**
`step1ne-recruit/src/index.js` 的 `scheduled()`，每 15 分鐘（`wrangler.toml:13`），主體約 `:5880-6469`。

**(2) 本機 macOS launchd（20 個啟用 + 1 個停用）**——`crontab -l` 是空的，全走 launchd：

| Label | 觸發 | 腳本 | 作用 |
|---|---|---|---|
| `com.step1ne.aiworker` | KeepAlive | `ai_worker.py` | 撈 `ai_jobs` 佇列跑 AI 文件工作 |
| `com.step1ne.checkup` | KeepAlive | `checkup_daemon.py` | 阿福履歷健檢對話引擎 |
| `com.step1ne.articlepublish` | 5 分 | `article_publish_tick.py` | 已核准文章自動建置部署 |
| `com.step1ne.bd` | 5 分 | `jobintake/bd_tick.py` | 反向開發：從未媒合人才池產匿名人選卡＋開發信 |
| `com.step1ne.callreport` | 5 分 | `consultant_call_report_tick.py` | 電洽紀錄 → 初篩報告 |
| `com.step1ne.candidatesummary` | 每日 09:10 | `jobintake/candidate_summary_tick.py` | 預產各職缺的候選人面向摘要 |
| `com.step1ne.clientreport` | KeepAlive | `client_report_tick.py` | 客戶版履歷 PDF 產生 |
| `com.step1ne.closesync` | 10 分 | `close_sync.py` | 職缺關閉後同步公開頁面 |
| `com.step1ne.jdaidraft` | 1.5 分 | `jobintake/jd_ai_draft_tick.py` | JD 行銷文案 AI 重寫 |
| `com.step1ne.jdregen` | KeepAlive | `jobintake/jd_regen_tick.py` | 職缺頁重建部署 |
| `com.step1ne.jobcopy` | 每週一 09:00 | `job_copy_loop.sh` | 職缺文案稽核→修正→部署→複測閉環 |
| `com.step1ne.medic` | 30 分 | `step1ne_medic.py` | daemon 自癒看門狗，失敗才叫人 |
| `com.step1ne.placementtracker` | 每日 09:30 | `placement_tracker.py --tick` | 送件→到職 14 態管線追蹤（硬性禁止自行標 PLACED） |
| `com.step1ne.portalimport` | KeepAlive | `jobintake/portal_import_tick.py` | 客戶上傳 JD 解析成 51 欄表單 |
| `com.step1ne.report` | 5 分 | `report_tick.py` | 顧問自由文字更新 → 結構化管線狀態（一律 TG 按鈕確認後才寫） |
| `com.step1ne.socialpost` | 每日 09:20 | `social_post_agent.py` | 社群徵才貼文草稿 → TG 審核 |
| `com.step1ne.talentsourcing` | 每日 09:00 | `talent_sourcing_daily.py` | 每位顧問一輪對外獵才 |
| `com.step1ne.talentsourcingtg` | KeepAlive ~30s | `talent_sourcing_tg_worker.py` | 認領 TG 發起的獵才請求 |
| `com.step1ne.threadsrefresh` | 每週一 08:00 | `refresh_threads_token.sh` | Threads token 續期 |
| `com.step1ne.weeklyarticle` | 每週三 11:00 | `run-weekly-article-step1ne.sh` | 週報文章草稿 → TG（絕不自動發布） |
| `com.step1ne.topicresearch` | **已停用** | `topic_research_tick.py` | 內容選題 |

### 已存在的「追蹤狀態」欄位（P3 最可重用的資產）

全部遵循同一個模式：**「時間戳條件」＋「`*_notified_at` 防重複欄位」＋「cron 檢查」**。

| 欄位 | 用途 | READ（判斷該不該提醒） | WRITE（標記已提醒） |
|---|---|---|---|
| `remind_at` / `reminded_at` | 候選人自選面談時間提醒 | `step1ne-recruit/src/index.js:6114-6119` | `:6134`（`sendMail` 於 `:6122`） |
| `stale_pinged_on` | 報告已產但顧問未決策，每日提醒一次 | `:6073-6087` | `:6104`（用「當日 token」讓它每天重發） |
| `hold_until` | 凍結面談室 3 小時逾時 | `interview_daemon.py:2720, :2753-2764` | `step1ne-backoffice-worker/src/index.js:6413` |
| `postinterview_notified_at` | 面談結束 5 分鐘後安撫推播 | `:5929-5934` | `:5959` |
| `progress2day_pinged_at` | 面談後 2 天仍無進度的催促 | `:6017-6026` | `:6056` |
| `applied_notified_at` | 申請後 10 分鐘延遲確認信 | `:5975-5983` | `:6005` |
| `ready_notified_at` | 防止重複寄面談連結信 | `step1ne-backoffice-worker/src/index.js:3467` | `:3477` |
| `start_notified_at` | 面談室開始的一次性 TG 通知 | `interview_daemon.py:896-913` | `:920` |
| `decline_email_sent_at` | 刻意延遲 3 小時的婉拒信 | `step1ne-recruit/src/index.js:6168-6188` | 同區塊 |
| `no_interview_at` / `no_interview_by` | 顧問手動標記「不需再面談」 | `step1ne-backoffice-worker/src/index.js:11452, :11499, :11504, :11552` | `:11561, :11572, :11580` |
| `client_consent_at` | **死欄位**（全 repo 查無讀寫） | — | — |

同模式的其他表：`checkups.remind_at/reminded_at`（`:6140-6161`）、`interview_appointments.reminded_at`（`:6195-6223`）、`placements.prep_reminded_at`（`:6244-6291`）、`placements.candidate_care_log`/`care_log`（`:6238-6467`，到職 Day1/3/7/28 候選人關懷 ＋ 保證期 Day7/20/30/50/60/80/90/110 顧問提醒）。

### 完全不存在的東西

- **「下一步動作」（next action）**：不存在。直接證據：`step1ne-backoffice-worker/frontend_v2/src/app.js:458-462` 有一條 2026-09-15 的修正註解明講「**application.next_action/next_step 從沒存在過（applications 表沒有這兩欄）**」——前端曾經長期讀取兩個不存在的欄位才被抓到。
- **「下次追蹤日期」（next follow-up date）**：不存在。`candidate_notes`（35 列，`type` 只有 `電洽紀錄`33 列 / `聯繫紀錄`2 列）**純粹是過去聯繫的 log，沒有任何未來日期欄位**。`manual_stage*` 是「管線階段覆寫」不是提醒機制。
- **顧問可設定的未來提醒 UI**：`frontend_v2/src/*.js` 查無 提醒／追蹤／設提醒／next contact 相關的未來日期設定介面。最接近的 `line-followup-overdue`（`adapters.js:100, :104`、`views.js:365-366`）是「候選人點了 LINE 連結但沒回覆」的互動 SLA，不是顧問設定的提醒。

---

## 八、P3 五個自動追蹤情境評估

| 情境 | 判定 | 原因 |
|---|---|---|
| **A** 面談做到一半，候選人 24 小時沒回覆 → 自動提醒一次 | 🟡 **部分可以** | 基礎設施齊備（cron 引擎＋LINE 主動推播＋`*_notified_at` 防重複模式都已在用）。但**目前沒有任何規則在看「面談中途停滯」**——現有的 `hold_until`/3 小時逾時是**關閉房間**，不是提醒候選人回來。需新增一條規則＋一個防重複欄位，**沒有架構障礙** |
| **B** 面談完成但顧問未處理 → 提醒顧問 | ✅ **已經可以，已在跑** | `stale_pinged_on` 機制（`:6073-6104`）：報告產出後 24 小時顧問仍未決策就每日推 TG。另有 `progress2day_pinged_at`（`:6017-6056`）面談後 2 天推播 |
| **C** 候選人說「下個月再聯絡我」→ 建立 follow-up date → 到期提醒 | ❌ **做不到** | **沒有任何「未來日期」資料模型**（見第七節）。`candidate_notes` 只有過去 log。且阿財目前也不會把這種口頭約定抽成結構化欄位——它會寫進 `consultant_followups[]` 的自由文字裡，沒有日期型別、沒有到期觸發 |
| **D** 新職缺出現 → 搜尋人才池 → 找出過去適合的人 → 提醒顧問 | ❌ **做不到** | 查無任何在職缺建立/發布時觸發候選人回配的程式。`jobintake/jd_ai_draft_tick.py`、`jd_regen_tick.py`、`publish_job.py`（職缺建立/發布路徑）**都沒有掛任何 matching hook**。最接近的 `bd_tick.py:57-73 pool()` 是「候選人→找公司」的反方向，且不由新職缺觸發 |
| **E** 候選人不適合目前職缺 → 自動重新 matching → 找其他職缺 | 🟡 **部分可以（但只到「講出來」）** | 機制一（第六節）已經會在面談中與報告中主動提出替代職缺，生產環境已有實例。**但沒有任何自動化後續**：不寫結構化欄位、不建 match 紀錄、不通知、不改狀態，全靠顧問人工讀取並操作 |

---

## 九、LINE OA / Candidate Chat 架構

### 面談的主要 Channel 是「網頁聊天室」，不是 LINE

候選人 `/apply` 後取得 `chat_token`，面談在瀏覽器內走 `/chat/*` HTTP 輪詢（`step1ne-public-worker/src/index.js:4664`、`:4671-4977`，含 `/chat/:token/voice` 走 Workers AI Whisper 做口說語言驗證）。**沒有證據顯示面談本身曾在 LINE 內進行。**

### LINE OA 的角色：綁定 + 進度查詢 + 主動推播

| 項目 | 現況 |
|---|---|
| Webhook / 驗簽 | `step1ne-messaging-relay/src/index.js:66-74`，HMAC-SHA256 用**未解碼 raw bytes**（`verifyLineSignature :24-36`，註解 `:11-13` 說明必要性），驗過才用 Service Binding 轉發（`:49`），失敗回 403 不轉發（`:71`） |
| userId → application 對應 | 表 `line_bindings`（`line_user_id, state, phone, application_ids, email, pending_name, pending_email, source, picture_url, profile_synced_at, display_name, ...`）。**`application_ids` 是 JSON 陣列欄，非 1:1 FK**，查詢時用 `safeJsonArray(row.application_ids).includes(applicationId)`（`step1ne-recruit/src/index.js:5942`） |
| 綁定流程 | LINE 內的對話狀態機 `not-bound → ask_name → ask_email → ask_phone → bound`（`step1ne-public-worker/src/index.js:992-993` 註解，處理於 `:2825-2980`，比對 applications 於 `:2924-2951`） |
| 訊息儲存 | **LINE 對話不存 `messages` 表**（那張只放 AI 面談逐字稿）；綁定過程的暫存值存在 `line_bindings` 自己（`pending_name`/`pending_email`/`state`）。**本次 schema 拉取沒有找到獨立的 LINE 聊天記錄表** |
| Conversation state | 只有 `line_bindings.state` 這個綁定流程用的狀態，**沒有通用的對話 session state** |
| AI 自動回覆（反應式） | `lineReply()`/`lineReplyMessages()`（`step1ne-public-worker/src/index.js:1014-1073`）用 Reply API，**需 replyToken，只能在候選人先發訊息後才能用** |

### ✅ 系統可以主動傳訊息給候選人（P3 關鍵能力，已具備）

`linePushMessages(env, lineUserId, messages)`（`step1ne-public-worker/src/index.js:1038-1058`，`step1ne-backoffice-worker/src/index.js:1198-1215` 有同樣一份）打 `POST https://api.line.me/v2/bot/message/push`，**不需 replyToken**；另有純文字版 `linePush() :1309-1326`。

**已在生產環境運作的候選人端主動推播（全部由 cron 時間條件觸發，零候選人動作）**：

| 情境 | 位置 |
|---|---|
| 面談結束 5 分鐘「初審已完成」 | `step1ne-recruit/src/index.js:5958` |
| 面談後 2 天無進度「進度提醒」（含「提醒顧問看一下」postback 按鈕） | `:6055` |
| 二面前一天提醒 | `:6213-6215` |
| 到職前 ≤7 天「報到前準備事項」 | `:6290` |
| 到職 Day 1/3/7/28 關懷（含「一切順利／想聊聊」按鈕） | `:6357` |
| 顧問動作觸發：進度更新／Offer Flex／到職日 Flex | `step1ne-public-worker/src/index.js:1330-1352, :1357-1400, :1402+` |

> **架構結論**：`lineReply*`（反應式，需 replyToken）與 `linePush*`（主動式，只需 `line_user_id`）是兩條清楚分離的路徑，後者**已經是被 cron 呼叫中的生產能力**。這是 P3「自動追蹤候選人」最直接可重用的積木——需要做的是把這 6 個寫死情境通用化成資料驅動的規則引擎，而不是從零建推播能力。

**Telegram 側注意**：所有 TG 推播**都是給顧問的內部管道**，候選人從不在 Telegram 上（`placement_tracker.py:147-160`、`step1ne_medic.py`、`report_tick.py:200`、`talent_sourcing_tg_worker.py:91-98`、`step1ne-recruit/src/index.js:6095, :6131, :6184, :6217, :6259, :6465`）。

**Email 側**：同樣有 cron 觸發的候選人信件（面談提醒 `:6122`、阿福提醒 `:6147`、延遲確認信 `:5987`、延遲婉拒信 `:6175`）。

---

## 十、AI Prompt / Agent 架構

### 模型策略

**全系統單一模型**：`claude-sonnet-5`。
`interview_daemon.py:127-128`（`TALK_MODEL = REPORT_MODEL = 'claude-sonnet-5'`）、`ai_worker.py:41`（`MODEL = 'claude-sonnet-5'`）。沒有「便宜模型做 X、貴模型做 Y」的分工。

### 呼叫點清單

**`interview_daemon.py`（面談與報告）**

| 函式 / 呼叫點 | 位置 | 用途 |
|---|---|---|
| `build_prompt()` → `run_claude()` | `:1186` / `:1657-1689`（subprocess `:1673`） | 每一回合建構完整面談 prompt 並呼叫模型，期望回傳 `{messages:[...], end: bool}` |
| `finish()` 報告產生區塊 | `:2277-2356`（subprocess `:2347`） | 面談結束後由完整逐字稿＋職缺＋履歷產出 Phase 7 自由文字 Markdown 報告 |
| `report_to_json()` | `:2214-2274`（subprocess `:2239`） | 第二段呼叫，把上面的 Markdown 轉成 `REPORT_JSON_SPEC` 結構化 JSON，prompt 明確要求「只做搬運與整理，不要重新判斷」 |
| 第 5 條自我改寫 | `:2604`（重用 `run_claude`） | 就服法第 5 條過濾（`law5_hits`）觸發時，要求模型改寫自己剛才的輸出 |

**`ai_worker.py`（後台佇列，全部由 `ai_jobs` 表驅動，poller `:1183 main()`）**

| 函式 | 位置 | 用途 |
|---|---|---|
| `prompt_call_summary_client` | `:108-128` | 電洽逐字稿 → 客戶版摘要 |
| `prompt_call_notes_summary` | `:129-148` | 電洽筆記 → 六段式結構摘要 |
| `prompt_call_prep` | `:149-259` | 電洽前準備（舊版） |
| `prompt_precall_card` | `:260-539` | PRECALL v1.4 結構化卡片（Hard Gates／必問題／`conversation_flow`／`alternative_jobs`／`resume_summary`） |
| `prompt_sourced_client_report_synthesize` | `:540-574` | 對外獵才人選的客戶版報告（無面談資料） |
| `prompt_client_report_synthesize` | `:575-722` | 已面談人選的客戶版報告，會逐項對照 `hard_filters`/`must_check_items`（`:686-690, :709-710`） |
| `prompt_postcall_result` | `:728-787` | 電洽後結構化決策（gate 結果／`route`／`alternative_jobs`） |

周邊確定性邏輯：`_validate_postcall_result :790-841`、`_filter_alternative_jobs :844-883`（**刻意不信任 AI 自行排除，用程式再篩一次**，`:847`）、`_wrap_postcall_result :886-912`（**硬寫 `consultant_decision:null`，架構上阻止 AI 自行填寫顧問決定**）。

其他檔案：`screen_resumes.py`（面談前履歷×JD 評分，約 `:126`）；`matching_engine.py`**零 LLM 呼叫**。

### 架構判定：**多階段 Prompt Chaining Pipeline**

**不是**單一大 prompt，**也不是** Agent / tool-calling state machine。

```
tick() :2821 → handle() :2584 → build_prompt() :1186 → run_claude() :1673   ← 每回合重建完整 prompt
   ↑______________ 迴圈直到 result['end'] 或 n >= MAX_TURNS ______________|
                              ↓
              finish() :2277 → LLM 呼叫 :2347（逐字稿 → Markdown 報告）
                              ↓
              report_to_json() :2214 → LLM 呼叫 :2239（Markdown → 結構化 JSON）
                              ↓
   後台 Worker 排入 ai_jobs → ai_worker.py :1183 依 kind 分派到 7 支 prompt_* 之一
```

特徵：
- **沒有模型端持久狀態**，狀態全在 D1（`messages` 表），每回合以純文字重新注入 prompt
- **沒有 tool calling、沒有 function calling 迴圈**
- 唯一的「狀態機」是 Python 的回合計數（`n >= MAX_TURNS`）與布林 `end` 旗標
- 每一階段的輸出是以**字面文字**餵進下一階段的 prompt——這是 prompt chaining，不是 agentic

---

## 十一、資料庫 Schema（Production 實存，D1 `step1ne-recruit`）

17 張稽核表全部存在。列數為稽核當下實測。

| 表 | 欄數 | 列數 | 代表什麼 | P3 相關關鍵欄位 |
|---|---|---|---|---|
| `applications` | 82 | 58 | 一位候選人對一個職缺的應徵，以及全部面談/管線/AI 產物狀態 | `status`, `screen_decision`, `redirect_job_slug`(全 NULL), `manual_stage(+note/by/at)`, `interview_state`, `post_call_result_json`, `lang_verified_at`, `owner`, 各 `*_notified_at` |
| `jobs` | 100 | 37（28 open） | 一個職缺需求 | `status`, `hard_filters`(1/37), `must_check_items`(10/37), `required_conditions`, `must_skills`, `salary_min/max`, `years_min`(4/37), `jd_spec_json` |
| `messages` | 5 | 1,365 | 阿財↔候選人的逐字稿 | `role`, `content`（**無任何狀態欄位**） |
| `reports` | 10 | 58 | 面談後報告產物（每份應徵最新一筆） | `content_json`（真正的結構化輸出所在）, `consultant_decision`；`recommend`/`hard_pass`/`content_client_md` **皆為死欄位** |
| `candidate_notes` | 6 | 35 | 顧問自由筆記（過去聯繫 log） | `type`（`電洽紀錄`33/`聯繫紀錄`2）；**無未來日期欄位** |
| `candidate_forwards` | 25 | 26 | 推薦給某客戶公司（per application×company） | 自己的 `manual_stage*`、`client_rejected_at/reason`、`advisor_not_recommended_*`、`placement_id` |
| `candidate_messages` | 8 | 3 | 客戶 portal 的訊息串 | `from_role`, `content` |
| `job_candidate_summaries` | 3 | 32 | 各職缺候選人彙整摘要快取 | `summary_text` |
| `job_requirement_snapshot` | 10 | **2** | 職缺需求的五層結構化拆解（matching_engine 的輸入契約） | `hard_must`, `soft_must`, `nice_to_have`, `exclusion`, `is_complete` |
| `sourced_candidates` | 38 | **3,653** | 對外獵才找到的人選（與 inbound 應徵流程獨立） | `job_slug`, `score`, `grade`, `status`, `recruitability_class`, `contacted_at`, `converted_application_id` |
| `fresh_sourced_candidates` | 8 | 7 | 待 matching 評估的新進 sourced 人選 | `target_job`, `match_status`, `blockers`, `matching_version` |
| `candidate_job_match` | 12 | 27 | (候選人, 職缺) 配對評估結果 | `match_status`, `match_score`, `reasons`, `blockers`, `matching_version` |
| `ai_jobs` | 11 | 25 | 後台 Worker（生產者）↔`ai_worker.py`（消費者）的非同步佇列 | `kind`, `status`, `payload_json`, `result_text`, `attempts` |
| `stage_events` | 8 | 7 | 管線階段轉換的 append-only 稽核 log | `stage`, `set_by`, `note` |
| `placements` | 57 | 23 | 進入客戶端後的完整生命週期（面試→Offer→到職→計費） | `stage`, `placement_status`, `offer_status`, `candidate_decision`, `billing_eligibility`, `care_log` |
| `chat_reports` | 6 | 2 | 候選人對面談本身的問題回報 | `reason`, `handled_at` |
| `interview_feedback` | 4 | 18 | 候選人對阿財的滿意度評分 | `rating`, `comment` |
| `line_bindings` | — | — | LINE userId ↔ application 綁定 | `line_user_id`, `state`, `application_ids`(JSON array) |

### 關係（簡）

```
jobs(slug) 1──N applications(job_slug)        ← 非 FK 約束，只是 slug 慣例
applications(id) 1──N messages                ← 面談逐字稿
applications(id) 1──N reports                 ← 實務上取 created_at 最新一筆
applications(id) 1──N candidate_notes
applications(id) 1──N candidate_forwards ──1 placements
applications(id) N──N line_bindings           ← 透過 application_ids JSON 陣列，非關聯表
jobs(slug) 1──1 job_requirement_snapshot      ← 只有 2 筆
jobs(slug) 1──N sourced_candidates            ← 3,653 筆，與 applications 平行的另一條世界線
```

> **規模落差提醒**：`sourced_candidates`（3,653 列）比其他所有表大兩個數量級。對外獵才的量遠大於 inbound 面談管線（58 筆應徵、58 份報告）。**本報告第一到第十節討論的面談/報告/決策機制，至今只跑過約 58 筆真實應徵。**

---

## 十二、升級 P3 前的技術債與風險

依「會不會擋住 P3」排序：

| # | 風險 | 嚴重度 | 說明 |
|---|---|---|---|
| 1 | **Conversation 完全沒有 state** | 🔴 高 | 每回合重送全逐字稿（`LIMIT 200` 靜默截斷），無 slot/topic 狀態。P3 要「依已知資訊動態換職缺」就必須有可信的「目前已知什麼」——這個東西現在只存在於 LLM 每回合的即時推論裡，**無法被程式讀取、無法驗證、無法觸發自動化** |
| 2 | **Candidate / Job 資料沒有結構化** | 🔴 高 | 候選人沒有技能/年資/學歷/產業欄位；職缺的 `hard_filters` 只有 1/37 有填。**任何「確定性自動比對」路線都會卡在這裡**。目前能運作是因為把散文丟給 LLM |
| 3 | **沒有未來日期 / next action 資料模型** | 🔴 高 | 情境 C（「下個月再聯絡我」）完全做不到。現有追蹤全是「過去時間戳 + 固定間隔」，沒有「約定的未來時點」 |
| 4 | **沒有職缺→候選人方向的任何能力** | 🔴 高 | 情境 D（新職缺回配人才池）零基礎。職缺建立/發布路徑沒有任何 hook |
| 5 | **Matching 沒有獨立 Service，且現有的那個沒接線** | 🟠 中高 | 三個機制互不相連（面談內 `other_jobs` / 後台 `alternative_jobs` / `matching_engine.py`），各自有各自的職缺清單查詢與判斷邏輯。**P3 若再加第四套，會變成四份不同步的「推薦邏輯」** |
| 6 | **AI 輸出與系統狀態之間幾乎沒有橋接** | 🟠 中高 | 全系統只有 `language_verification.verdict` 一個 AI 判斷會改變系統狀態。其餘 AI 分類（含 verdict、fit_scores.grade）**沒有任何程式讀取**，等於精心產生後就只是給人看的文字 |
| 7 | **Status 三套並行、語意重疊** | 🟠 中 | `applications.status` / `interview_state` / `manual_stage` / `screen_decision` / `reports.consultant_decision` / `placements.stage` / `candidate_forwards.manual_stage` 各自為政（後兩者還有 high-water-mark 合併規則）。P3 再加 route 分類會讓狀態真相來源更分散 |
| 8 | **Prompt 綁死單一職缺的殘留** | 🟠 中 | 面談 prompt 雖已注入 `other_jobs`，但整個報告 schema（`hard_conditions`、`fit_scores`）仍然是「對這一個職缺」的評估。P3 需要「對 N 個職缺的相對評估」時，現有 schema 無法表達 |
| 9 | **沒有 Audit Log（AI 決策面）** | 🟡 中低 | `stage_events` 只有 7 列且只記管線階段。**AI 為什麼推薦 B 職缺、依據哪一句話**沒有任何結構化留存，只在報告散文裡。P3 若讓 AI 影響候選人看到的內容，這會變成合規與除錯的痛點 |
| 10 | **Human approval 目前靠慣例不靠機制** | 🟡 中低 | 好消息是既有設計很自律（`_wrap_postcall_result` 硬寫 `consultant_decision:null`、`placement_tracker.py` 禁止自行標 PLACED、`report_tick.py` 一律 TG 按鈕確認）。但這些是**各自實作的慣例**，沒有統一的 approval 框架。P3 讓 AI 主動對候選人講話時，需要明確的「哪些可自動、哪些要人核可」界線 |
| 11 | **Candidate identity mapping 不穩** | 🟡 中低 | `line_bindings.application_ids` 是 JSON 陣列，比對靠應用層 `includes()`；同一人多次應徵會產生多列 `applications`，**沒有「人」這個實體**（只有「應徵」）。P3 的人才池回配需要「人」層級的身分 |
| 12 | **Job status 語意不一致** | 🟡 中低 | `status` 實際值有 `open`/`active`/`closed`/`draft`/`client_draft`；不同程式用不同條件（`IN ('open','active')` vs `!= 'closed'` vs `NOT IN ('closed','pending_review','client_draft')`）。**機制一用 `status='open'`（40 筆），機制二用 `IN ('open','active')`（20 筆）——兩個推薦機制看到的職缺池本來就不一樣** |
| 13 | **履歷無 OCR fallback** | 🟡 低 | 圖檔 PDF 直接拒絕（`parse_resumes.py:183-185`）。若 P3 要擴大人才池覆蓋，這是資料進不來的漏洞 |
| 14 | **前端 `frontend_v2` 無獨立 git 版控** | 🟡 低 | 目前唯一版本紀錄是 CF Pages 部署歷史（既有狀態，非本次產生） |

---

## 十三、Gap Table

| 能力 | Current | P3 Need | Gap | Difficulty | Existing Reusable Components |
|---|---|---|---|---|---|
| **Candidate Profile** | 🟡 表單欄位結構化；技能/年資/學歷/產業等**只存在於 `reports.content_json` 的敘事式 JSON** | 可跨候選人程式比對的結構化特徵 | 需要定義 candidate profile schema 並從既有 `content_json` 回填 | **高**（資料工程，非 AI 問題） | `reports.content_json` 已有 46/58 筆、`_normalize_report_json()` 的正規化模式可延伸 |
| **Job Profile** | ❌ 大多為散文；`hard_filters` 1/37、`years_min` 4/37 | 結構化的 must/nice/gate/範圍 | 需要職缺資料補完專案 | **高**（人力密集） | `jd_spec_json`(28/37)、`job_requirement_snapshot` 五層 schema、`portal_import_tick.py` 的 51 欄解析能力 |
| **Hard Gate** | 🟡 面談前算一次（`screenings.hard_fail`），面談中不強制、面談後只描述 | 面談中即時判定並可分支 | 需 slot state + 即時 gate 判定 | **中高** | `screen_resumes.py` 的 hard_fail 判定、`matching_engine.py` 的 HARD_MUST 聚合邏輯（確定性、可直接搬） |
| **AI Interview** | ✅ 完整且穩定（1,365 則訊息、58 場） | 同左 + 狀態化 | 主要缺 slot state | **中** | `interview_daemon.py` 全套 |
| **AI Classification** | 🟡 4 值 verdict 穩定產生，但**無人讀取**；A-E 五分流只有 B、E 真正存在 | 五分流 + 驅動後續流程 | 需把 route 落成 DB 欄位並接上 workflow | **中** | `_VERDICTS` 機制、`fit_scores` 六維計算、新版 `prompt_postcall_result` 的 5-route enum 與 validator |
| **Candidate→Job Matching** | 🟡 只有 LLM 讀散文判斷（機制一/二），無確定性比對 | 可靠的多職缺比對 | 受限於上面兩個 Profile gap | **高** | 機制一的 `other_jobs` 查詢＋prompt、`_filter_alternative_jobs()` 確定性後過濾 |
| **Job→Candidate Matching** | ❌ 完全沒有 | 新職缺回配人才池 | 全新能力 | **高** | `bd_tick.py:57-73 pool()` 的人才池查詢模式、`sourced_candidates`(3,653) 作為候選池 |
| **Alternative Job Recommendation** | 🟡 **面談中已能講出來且已在生產環境發生**，但無結構化、無自動化 | 結構化 + 自動落地 + 通知 | 把散文變結構化並接上動作 | **低-中（最划算的一步）** | 機制一（`interview_daemon.py:1080-1089, :1460-1519, :2320-2335`）＋機制二的 schema/validator/post-filter 可直接搬 |
| **Follow-up Engine** | 🟡 6 個寫死情境穩定運行 | 資料驅動的通用規則引擎 | 需抽象化 + 未來日期模型 | **中** | cron `scheduled()` 引擎、`*_notified_at` 防重複模式、`placements.care_log` 多階段關懷模式 |
| **Scheduler** | ✅ 兩套（CF cron 15 分 + 20 個 launchd） | 同左 | 幾乎無 gap | **低** | `step1ne-recruit/src/index.js:5880-6469`、launchd plist 模式 |
| **Proactive Messaging** | ✅ **LINE 對候選人主動推播已在生產運行**（6 情境）；Email 同理；TG 對顧問 | 同左 + 通用化 | 只需把觸發條件通用化 | **低** | `linePushMessages()`、Flex + postback 按鈕模式、`sendMail` cron 模式 |
| **Human Approval** | 🟡 靠各自實作的慣例（硬寫 null、禁止自標、TG 按鈕確認） | 統一的核可框架 | 需抽象成共用機制 | **中** | `_wrap_postcall_result` 的欄位隔離、`report_tick.py` 的 TG 確認模式、`/postcall-decision` 端點 |
| **Audit Log** | ❌ 只有 `stage_events`(7 列)，AI 決策理由無結構化留存 | AI 決策可追溯 | 全新 | **中** | `stage_events` schema 可延伸、`ai_jobs` 已保留 payload/result |

---

## 十四、五個問題的明確回答

### 1. 目前阿財比較接近 P1 還是 P2？

**接近完整的 P1，加上「會產生但沒人讀」的半套 P2。**

P1 面向（初談本身）已經是成熟的生產系統：問題生成、上下文注入、追問、摘要、報告、交付顧問全部齊備且穩定（58 場面談、1,365 則訊息）。

P2 面向（判斷與分流）**產生了判斷，但判斷不驅動任何事**：`content_json.verdict` 四值穩定產出，六維 `fit_scores` 也確實算出 A/B/C/D 級別——但全系統**沒有任何一行程式讀它們來決定下一步**，唯一的消費者是給人看的一致率儀表板。五分流中只有 B（需顧問判斷）與 E（Hard Gate Fail）真正存在於既有 schema，C（轉職缺）與 D（人才池）只存在於 2026-09-18 才加的原型裡（生產資料 1/58 筆）。

> 用你那張分工圖的語言說：**第 2、3、4 格（履歷解析／Hard Gate 先確認／AI 初談）已經做到了；第 5 格（AI 先做初步判斷與分流）是「AI 有想，但系統沒接」。**

### 2. 升級 P3 最大的 3 個 Gap 是什麼？

**Gap 1｜沒有「已知資訊」的結構化狀態（Conversation State）**
阿財每一回合都在用「重讀 200 則逐字稿」重新推導自己知道什麼。這讓「依據已知條件即時換職缺」這件事在程式層面無法驗證、無法觸發。這是**所有 P3 情境的共同前置**。

**Gap 2｜Candidate 與 Job 兩側都沒有可比對的結構化資料**
候選人沒有技能/年資/產業欄位，職缺的 hard gate 只有 1/37 有填。目前的推薦之所以能動，是因為把散文交給 LLM 判斷。**這決定了 P3 只能走「LLM 判斷 + 確定性後過濾」路線，走不了「純規則比對」路線**——除非先做一個獨立的資料補完專案。

**Gap 3｜AI 的結論到系統動作之間沒有橋**
全系統只有一個 AI 判斷（語言驗證）會真的改變系統狀態。P3 需要的「自動追蹤」「自動重配」本質上都是「AI 結論 → 寫入狀態 → 觸發排程 → 主動推播」的鏈條，而**這條鏈今天只有頭（AI 會判斷）跟尾（推播基礎設施完備），中間兩節完全空白**。

### 3. 哪些現有程式可以直接沿用？

| 可直接沿用 | 位置 | 為什麼 |
|---|---|---|
| **面談中的其他職缺注入** | `interview_daemon.py:1080-1089`（查詢）、`:1460-1519`（prompt） | 已在生產運作且有實例，改的是「輸出格式」不是「能力」 |
| **alternative_jobs 的 schema／validator／後過濾** | `ai_worker.py` `_validate_precall_card`、`_validate_postcall_result :790-841`、`_filter_alternative_jobs :844-883` | 剛寫好、已驗證、包含「不信任 AI 自行排除」的確定性防線 |
| **LINE 主動推播全套** | `linePushMessages()`（`step1ne-public-worker/src/index.js:1038-1058`）＋ Flex/postback 模式 | 真正的主動推播（非 reply），已在 6 個情境生產運行 |
| **cron 追蹤引擎與防重複模式** | `step1ne-recruit/src/index.js:5880-6469`，`*_notified_at` 慣例 | 現成的「條件成立→動作→標記」骨架，加規則即可 |
| **AI 佇列** | `ai_jobs` 表 + `ai_worker.py` poller + 後台 `queueAiJob()` | 非同步 AI 工作的現成基礎設施，加一個 `kind` 就能長出新能力 |
| **Hard Gate 聚合邏輯** | `matching_engine.py:488 evaluate_candidate()` | 純確定性 Python，沒有外部依賴，可當作 gate 判定的函式庫直接 import |
| **報告結構化第二段呼叫** | `report_to_json() :2214` + `_normalize_report_json() :2039` | 「把散文轉成 schema」的現成模式，要新增結構化欄位時照這套走 |
| **人才池查詢模式** | `jobintake/bd_tick.py:57-73 pool()` | 「找出未媒合的過去應徵者」的現成 SQL 模式 |

### 4. 哪些地方必須重構？

| 必須重構 | 理由 |
|---|---|
| **面談的狀態管理（`context_for()` / `build_prompt()`）** | 必須在回合之間維護一個結構化的「已知/未知/衝突」狀態物件，取代「每回合重讀 200 則」。這同時解決 token 成本、逐字稿截斷風險與「硬條件到底問了沒」無法驗證三個問題 |
| **推薦職缺的三套並行邏輯** | 機制一（面談內）、機制二（後台 precall/postcall）、機制三（matching_engine）各有各的職缺查詢與判斷，連「哪些職缺算 open」的定義都不一樣（`status='open'` vs `IN ('open','active')`）。**必須收斂成單一 recommendation service**，否則 P3 會變第四套 |
| **AI 分類的落地路徑** | `verdict` / `fit_scores` / `route` 目前是三套平行的分類詞彙（4 值 / A-D 級 / 5 route），且都不驅動流程。需要收斂成一套「會被程式讀」的 route 欄位，並明確定義每個 route 觸發什麼 |
| **狀態真相來源** | `status`/`interview_state`/`manual_stage`/`screen_decision`/`consultant_decision`/`placements.stage` 六套並行。P3 加入自動化後，「誰說了算」必須先定義清楚，否則自動流程與顧問手動操作會互相覆蓋 |
| **候選人身分（identity）** | 目前只有「應徵」沒有「人」。人才池回配、跨職缺追蹤都需要人層級的實體與去重 |

### 5. 如果不修改 Production，最小可行的 P3 Prototype 可以怎麼做？

**核心思路：完全不碰面談流程（風險最高、最穩定的部分），只在「面談結束之後」這個安全點切入，用既有的 `ai_jobs` 佇列 + 既有的推播引擎，做一條旁路。**

```
既有：面談結束 → finish() → 報告產生 → deliver_after_interview() 推 TG
                                  │
新增（旁路，不改既有流程）：       ▼
         新 ai_jobs kind = 'post_interview_rematch'
                                  ▼
         ai_worker.py 新 handler：讀 reports.content_json（已含 hard_conditions/verdict/work_history）
                                 + 讀 open jobs 清單（沿用 fetchAlternativeJobCandidates 的 SQL）
                                  ▼
         沿用既有 _validate_* 與 _filter_alternative_jobs 產出結構化 alternative_jobs[]
                                  ▼
         寫入一個獨立的新表（不動 applications）
                                  ▼
         沿用既有 cron 模式：若 verdict='硬條件不符' 且有備案 → 推 TG 給顧問（不碰候選人）
```

**為什麼這是最小可行**：
- **不改面談 daemon**（風險最高的部分零變動）
- **不改既有 DB 欄位**（只加一張獨立新表，可隨時 drop）
- **不新增 AI 基礎設施**（`ai_jobs` 佇列、`ai_worker.py` poller、prompt/validator/post-filter 全部現成）
- **不碰候選人**（第一版只推給顧問，把「AI 對候選人主動講話」這個高風險決策留到驗證過推薦品質之後）
- **可量測**：跑一段時間後，比對「AI 建議的備案」與「顧問實際採用的備案」一致率——這正好複用既有校準儀表板的作法（`step1ne-backoffice-worker/src/index.js:11437-11488`）

**這個 Prototype 驗證的假設**：AI 在「已知硬條件不符」的情況下，推薦的替代職缺有多準？
**驗證通過後才值得投資的下一步**：把同樣的結構化輸出接回面談當下（機制一），讓阿財當場講；以及補 Conversation State 讓「當場換職缺」在程式層面可控。

---

## 附錄：重要 Source Path 索引

| 主題 | 路徑 |
|---|---|
| 阿財面談引擎 | `~/claude-projects/工作流程技能包/step1ne-recruit/interview_daemon.py` |
| AI 佇列處理器 | `~/claude-projects/工作流程技能包/step1ne-recruit/ai_worker.py` |
| **主 Worker + 15 分 cron 追蹤引擎** | `~/claude-projects/工作流程技能包/step1ne-recruit/src/index.js`（約 `:5880-6469`） |
| 候選人面向 Worker | `~/claude-projects/工作流程技能包/step1ne-public-worker/src/index.js` |
| 顧問後台 Worker | `~/claude-projects/工作流程技能包/step1ne-backoffice-worker/src/index.js` |
| 顧問後台前端 | `~/claude-projects/工作流程技能包/step1ne-backoffice-worker/frontend_v2/src/` |
| LINE/TG webhook 中繼 | `~/claude-projects/工作流程技能包/step1ne-messaging-relay/src/index.js` |
| 履歷解析 | `~/claude-projects/工作流程技能包/step1ne-recruit/parse_resumes.py` |
| 履歷初篩 | `~/claude-projects/工作流程技能包/step1ne-recruit/screen_resumes.py` |
| 配對引擎（未接線） | `~/claude-projects/工作流程技能包/step1ne-recruit/matching_engine.py` |
| 職缺題庫建置 | `~/claude-projects/工作流程技能包/step1ne-recruit/build_expertise.py` |
| 對外獵才 | `~/claude-projects/工作流程技能包/step1ne-recruit/talent_sourcing_agent.py`, `talent_sourcing_daily.py` |
| 送件/到職追蹤 | `~/claude-projects/工作流程技能包/step1ne-recruit/placement_tracker.py` |
| 反向開發（人才池→客戶） | `~/claude-projects/工作流程技能包/step1ne-recruit/jobintake/bd_tick.py` |
| launchd 排程定義 | `~/Library/LaunchAgents/com.step1ne.*.plist` |
| D1 查詢方式 | `source ~/.config/workflow-os/cf.env && cd ~/claude-projects/工作流程技能包/step1ne-recruit && python3 -c "import d1_http; print(d1_http.query('SQL'))"` |

---

*本報告由唯讀稽核產出，未修改任何 production code、DB schema 或 prompt。所有數據為 2026-09-18 稽核當下的 live D1 實測值。*
