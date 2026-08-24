# Step1ne 候選人搜尋規則手冊（SOURCING_RULES.md）

給誰看：**下一個要做候選人 sourcing 的 AI agent**，不是給人看的報告。
開工前先讀完這份，讀完才能開始搜。

這份文件不是又一輪 sourcing pilot 的紀錄，是**永久規則**。以前跑過幾輪
（Phase 2.3 隨機搜尋、Phase 2.5 Shadow Recruiter 系統化搜尋）都存在
`fresh_sourced_candidates` 和 `candidate_job_match` 表裡，用不同的
`matching_version` 字串區分——但那些都只是「跑過的紀錄」，不是「規則」。
這幾輪一直重複同一個問題：**AI 搜第一頁沒有就說找不到**。這份文件就是
用可執行、可回放的規則把這個偷懶徹底堵住。

---

## 0. 一句話版本

> 第一頁沒有，不代表網路上沒有。沒把下面的流程走完，就不准說「找不到」——
> 只能說「在目前已執行的搜尋範圍內還沒找到」，而且這句話要能被 audit log 回放證明。

---

## 1. Anti-First-Page Rule（反偷懶第一條）

不准只看第一頁搜尋結果、只跑一組 query，就宣稱「找不到人」或「這個職缺沒有候選人」。

- 一組 query 只看前 10 筆就結案 = 偷懶，不合格。
- 只用一種說法（例如只打英文職稱）搜一次就結案 = 偷懶，不合格。
- 這條規則不是「建議」，是下面第 8 節 `SEARCH_INCOMPLETE` 檢查會**強制擋下**的東西。

---

## 2. 四層搜尋流程

任何一次 sourcing，都要依序走完這四層，不能跳過：

### 2.1 Query Expansion（查詢擴展）
至少要涵蓋 **4 類 Query Family**（見第 3 節），每一類至少 3 個變體，
所以起跳就是 4×3 = 12 組查詢（見第 9 節 Dynamic Search Budget）。

### 2.2 Result Depth Search（結果深度搜尋）
每一組 query，不能只看第一頁。規則是：**每組至少檢查 30-50 筆結果，或至少翻 3-5 頁**，
兩個條件滿足其一即可，但不能兩個都不滿足。

### 2.3 Source Diversification（來源多元化）
不能只用一個搜尋引擎、一個平台。要照下面**這個順序**依序嘗試（不是隨機選，是有優先順序的清單）：

1. Search Engine（Google 等一般搜尋引擎）
2. 公司官網（團隊介紹頁、關於我們、新聞稿）
3. LinkedIn indexed（透過搜尋引擎 X-Ray 進去的 LinkedIn 頁面，不是登入後搜尋）
4. Cake indexed（透過搜尋引擎 X-Ray 進去的 CakeResume 頁面）
5. Conference / Webinar（研討會講者、Webinar 主講人名單）
6. 協會（產業公會、專業協會的會員/理監事名單）
7. 新聞（人事異動新聞、專訪、獲獎報導）
8. 年報 / ESG 報告（上市櫃公司年報裡的管理層名單、ESG 報告的部門主管）
9. 公開 PDF（見第 5 節 PDF/Report Mining）

至少要用到 **4 種不同來源類型**才算合格（見第 8 節 `source_type_count>=4`）。

### 2.4 Candidate/Company Chain Search（人選/公司鏈式搜尋，seed expansion）
找到一個候選人或一家目標公司之後，要用它當種子往外展開：
- 這個人 LinkedIn/Cake 頁面上列出的「同事」「相似背景的人」
- 這家公司的競爭對手、同產業同規模的公司
- 這個人过去待過的公司，去找那家公司現在做同樣職位的人

這一步不能跳過（見第 8 節 `candidate_chain_attempted=true`）。

---

## 3. 四個 Query Family 範本

每次開搜前，四類都要各自產出至少 3 個變體。

### 3.1 Exact Titles（精確職稱）
直接用職缺的職稱去搜，中英文都要。
- 範例（以 Operations Manager 為例）：`"Operations Manager" Taiwan`、`營運經理`、`營運部經理`

### 3.2 Adjacent Titles（相鄰/相近職稱）
同一種工作在不同公司可能叫不同名字。
- 範例：`Operations Manager` 的相鄰職稱包含 `Business Operations Manager`、`General Manager`、
  `Site Manager`、`廠務經理`、`供應鏈經理`、`Ops Lead`

### 3.3 Skill-Responsibility Based（技能/職責導向）
不搜職稱，搜這個角色實際在做的事，因為很多公司職稱跟實際工作內容對不上。
- 範例：`跨部門流程優化 供應鏈管理 經理`、`P&L responsibility operations Taiwan`、
  `導入ERP系統 專案負責人`

### 3.4 Target Company Search（目標公司搜尋）
先列出這個角色最可能出現的公司清單（同產業、同規模、競爭對手），
針對每家公司搜「這家公司 + 這個職稱/職責」。
- 範例：`鴻海 營運經理`、`台達電 Operations Manager`、`Delta Electronics site:linkedin.com/in Operations`

---

## 4. Query Mutation 示範（原文照抄，不能省略）

從一組原始 query 開始，遇到搜尋結果不夠時，要**遞進式**變化，不是換一個完全無關的字重搜。
以 `Operations Manager Taiwan` 為例，完整的遞進路徑：

```
Operations Manager Taiwan
  → 加產業限定：Operations Manager Taiwan manufacturing
  → 加目標公司：Operations Manager Taiwan Delta Electronics
  → 加 X-Ray 語法：site:linkedin.com/in "Operations Manager" Taiwan
  → 換成中文公司名重搜：台達電 營運經理
```

這個範例本身就是規則的一部分——未來任何職缺的 query mutation 都要照這個模式
（原始詞 → 加產業 → 加公司 → 加 site: 限定 → 換語言重搜），不是每次重新發明。

---

## 5. PDF / Report Mining

年報、ESG 報告、產業白皮書、研討會議程常常藏著目標職缺的在職者名字或聯絡窗口。
用 `filetype:pdf` 搜法：

- `filetype:pdf 年報 [公司名] 經理`
- `filetype:pdf ESG報告 [公司名] 部門主管`
- `filetype:pdf [產業名] 研討會 議程 講者`
- `filetype:pdf [公司名] annual report management team`

---

## 6. Deduplication 規則

**同一個人在 5 個不同來源出現，算 1 個 Candidate、5 個 Evidence Sources**，不是 5 個候選人。

去重判斷依據（符合以下任一組合就視為同一人）：
- 姓名 + 目前任職公司 一致
- 姓名 + 相同 LinkedIn/Cake 個人頁面連結
- 姓名 + 高度相似的職涯路徑（同幾家公司、相近年資）

去重之後，這個 Candidate 底下要保留所有來源當 Evidence，不能去重時把來源也一起丟掉——
來源越多代表這個人越「查有實據」，是判斷 Evidence Sufficient 的依據。

---

## 7. Search Result Classification（搜尋結果分類）

每一筆搜尋結果都要分到以下其中一類，不能不分類直接跳過：

| 分類 | 意思 |
|---|---|
| `CANDIDATE` | 明確是符合條件的候選人個人頁面/資料 |
| `POSSIBLE_CANDIDATE` | 疑似符合但資訊不完整，需要進一步查證 |
| `COMPANY` | 公司頁面，非個人 |
| `JOB_PAGE` | 職缺公告頁面，不是候選人 |
| `NEWS` | 新聞報導 |
| `ASSOCIATION` | 協會/公會頁面 |
| `CONFERENCE` | 研討會/Webinar 頁面 |
| `NOISE` | 完全無關的雜訊結果 |
| `DUPLICATE` | 已經在本輪或先前紀錄中出現過的同一個人/來源 |
| `AUTH_REQUIRED` | 需要登入才能看到內容（LinkedIn 全頁、Cake 完整檔案等），**不等於找不到人**（見第 10 節） |

---

## 8. 三個核心防偷懶機制（Jacky 特別強調，不可放寬）

### 8.1 `SEARCH_INCOMPLETE`

在下任何「找不到」的結論之前，agent（或程式）**必須自我檢查**以下五個條件是否**全部**成立：

```
query_family_count      >= 4    （四個 Query Family 都用過）
query_variant_count     >= 12   （總查詢數至少 12 組）
source_type_count       >= 4    （至少用過 4 類來源）
candidate_chain_attempted == true  （做過 seed expansion）
diminishing_return_condition == true  （已達邊際效益遞減，見 8.2）
```

**只要缺一項，狀態就是 `SEARCH_INCOMPLETE`，不准下「找不到」的結論。**
必須先把缺的項目補齊，才能往下判斷。

### 8.2 `SEARCH_SATURATED`

連續 **3 組不同的 query variant**，都同時滿足「0 個新候選人 + 0 筆新證據 + 0 個新來源」，
才能判定為飽和，這時候才准停止搜尋。

只有 1-2 組沒有新發現不算飽和——搜尋結果本來就會有起伏，不能因為連續 2 組沒收穫就放棄，
要撐到連續 3 組真的什麼都沒有才停。

### 8.3 Search Audit Log（可回放紀錄）

每一組 query 都要留下紀錄，欄位對應 `search_audit_log` 表：

```json
{
  "query": "實際下的搜尋字串，原文照抄",
  "query_family": "EXACT_TITLES | ADJACENT_TITLES | SKILL_RESPONSIBILITY | TARGET_COMPANY",
  "result_depth": "實際檢查到第幾筆/第幾頁",
  "results_checked": "這組總共看了幾筆",
  "candidates_discovered": ["這組新發現的候選人"],
  "duplicates": "撞到幾個已存在的候選人",
  "noise": "分類為 NOISE 的結果數",
  "auth_required": "分類為 AUTH_REQUIRED 的結果數",
  "new_evidence": ["這組補到的新證據"],
  "started_at": "...",
  "finished_at": "..."
}
```

日後只要有人問「把這次 sourcing run 的 query log 給我」，就要**真的拿得出逐條紀錄**。
拿不出來，就代表根本沒搜過——這是判斷「這次 sourcing 是不是玩真的」的鐵證。

---

## 9. Dynamic Search Budget（動態搜尋預算）

初始預算 = **4 個 Query Family × 至少 3 個 Query Variant = 至少 12 組搜尋起跳**。

- 不是搜到天荒地老（沒有上限失控地一直搜）。
- 也不是看 10 個結果就停（低於 12 組預算連基本盤都沒跑完）。
- 12 組是**起跳門檻**，實際要搜多少組，由 `SEARCH_SATURATED` 條件決定何時停止。

---

## 10.「查不到」的正式表述規則

**禁止**說「網路上找不到」「Candidate 不存在」這種絕對化的講法，
因為那暗示著「已經窮盡了整個世界」，但實際上只搜過有限範圍。

**只能**用這個固定表述：

> 在本輪已執行的 **X 組 queries**、**Y 個 search results**、**Z 類來源**中，
> 尚未找到足夠證據的候選人。

正式代碼：`NOT_FOUND_IN_CURRENT_SEARCH_SCOPE`

而且下這句話之前，必須先通過第 8.1 節的 `SEARCH_INCOMPLETE` 檢查（五項全部成立），
否則連 `NOT_FOUND_IN_CURRENT_SEARCH_SCOPE` 都不能講，只能繼續搜。

### 10.1 `AUTH_REQUIRED` 不等於 `NOT_FOUND`

LinkedIn 回傳 HTTP 999、CakeResume 要求登入才能看完整檔案，這些狀況要標記為
`AUTH_REQUIRED`，**不可以**寫成 `NO_CANDIDATE` 或當作「這裡沒有人」處理。
`AUTH_REQUIRED` 代表「看得到有這個人存在，但被擋在登入牆外面」，
是真人審查佇列（見第 12 節）要優先處理的類型，不是搜尋失敗。

---

## 11. 依職缺類型選 Source Strategy（含 Paid Source 政策，2026-08-23 修正）

### 11.0 一句話原則（政策修正核心）

> **付費來源是加速器，不是氧氣。沒有 LinkedIn Recruiter Lite、沒有 104 企業版履歷搜尋，
> sourcing 依然要能正常啟動、正常跑完整個流程。**

之前的理解有誤——曾經把「LinkedIn Recruiter Lite / 104 企業版沒開通」當成
sourcing 卡住、要等 Jacky 開帳號才能繼續的理由。這是錯的政策假設，已徹底修正：
**FREE/AVAILABLE SOURCE FIRST 是 Step1ne 現階段正式策略**，不是退而求其次的替代方案，
也不是等付費開通前的過渡期做法。任何 sourcing run，從 `start_run()` 開始，
都不應該因為沒有付費帳號而被任何檢查擋下來。

### 11.1 每個 Job 一進來，先決定目前可用的免費來源清單

每次開搜前，要先依職缺類型決定 `SOURCE_STRATEGY`，不是每個職缺都用同一套來源組合。
下表是**預設**組合，不是永久寫死——可以依職缺實際 Requirement 動態調整，
但預設起點永遠是免費/可用來源，不含任何付費來源：

| 職缺類型 | Source Strategy（依序） |
|---|---|
| Software / Engineering | GitHub → Google → LinkedIn public indexed results → Cake public discovery |
| Product / Marketing | Cake public discovery → Google → LinkedIn public indexed results |
| Executive / Operations / Finance | Google → LinkedIn public indexed results → 公司官網 → 新聞 → 研討會 → 協會 |
| Admin / Customer Service / general | inbound applications → social → public job/candidate sources |

開搜前先產出屬於這個職缺的 `SOURCE_STRATEGY` 清單，寫進 `sourcing_runs.notes` 或
搜尋計畫裡，不能憑感覺臨場決定要不要查某個來源。對應
`sourcing_engine.py` 的 `SOURCE_STRATEGY_BY_JOB_TYPE`。

### 11.2 LinkedIn 要分兩種來源，不准混在一起

- `LINKEDIN_PUBLIC_DISCOVERY`：透過 Google 等搜尋引擎 X-Ray 進去的 LinkedIn 頁面、
  public snippet、public professional references——這是**正常可用**的免費來源，
  屬於第 11.1 節每個 Source Strategy 都會用到的一環。
- `LINKEDIN_RECRUITER_PAID`：LinkedIn Recruiter Lite 這種付費帳號——標記為
  `OPTIONAL_PAID_SOURCE`，不是 `REQUIRED_SOURCE`。

LinkedIn 出現 Login Wall（`AUTH_REQUIRED`，見第 10.1 節）**不代表整個 sourcing 被
`BLOCKED`**，只代表這一條路徑走不通，換別條路（Google indexed results、
Cake、公司官網……）繼續走，不是停下來等付費帳號。

### 11.3 104 也分兩種來源，不准混在一起

- `104_PUBLIC_JOB_INTELLIGENCE`：104 公開頁面能看到的職缺公告、公司徵才頁——
  用來做 target company mapping、market intelligence、job intelligence、
  competitor hiring signals，**這部分不涉及爬候選人履歷，是合法的市場情報用途**，
  屬於免費可用來源。
- `104_PAID_RESUME_DATABASE`：104 企業版履歷主動搜尋——標記為
  `OPTIONAL_PAID_SOURCE`，不是 `REQUIRED_SOURCE`。

### 11.4 Paid Source 分級表

| 來源 | 分級 |
|---|---|
| LinkedIn Recruiter Lite | `OPTIONAL_PAID_SOURCE` |
| 104 企業版履歷搜尋 | `OPTIONAL_PAID_SOURCE` |
| Cake 企業人才搜尋 | `OPTIONAL_PAID_SOURCE` |

三者全部是 `OPTIONAL_PAID_SOURCE`，**沒有任何一個是 `REQUIRED_SOURCE`**。
沒有付費帳號，不得阻塞 sourcing 的任何一個環節——`start_run()`、
Source Strategy 產出、Query 生成、Deep Search、Widening、Matching、
Audit Log 寫入，全部都不應該被付費來源相關的檢查擋下來。

### 11.5 正確的執行狀態列舉

```
SOURCE_READY_FREE      免費來源可正常使用，sourcing 正常進行
SOURCE_PARTIAL         部分免費來源可用，部分不可用（不影響整體繼續）
SOURCE_AUTH_REQUIRED   遇到登入牆（見第 10.1 節），只代表這一條路徑走不通
SOURCE_PAID_OPTIONAL   有付費來源可用，但屬於加速器、非必要
SEARCH_SATURATED       免費來源已達邊際效益遞減（見第 8.2 節）
CONSIDER_PAID_SOURCE   有具體 ROI Evidence，建議考慮付費來源（見第 11.6 節）
```

**不准**因為付費來源未開通就直接判 `SOURCE_ACCESS_BLOCKED`——這個狀態已廢除。
除非這個職缺真的完全沒有任何可合法使用的來源（這種情況極罕見，要有具體理由才能下這個結論，
不能只因為「還沒開通付費帳號」就下）。

### 11.6 Source Escalation 規則：什麼時候才能建議付費來源

只有當**三個條件同時成立**，才能建議 `CONSIDER_PAID_SOURCE`：

1. 免費來源已經跑完完整的 sourcing run，達到 `SEARCH_SATURATED`（第 8.2 節的定義：
   連續 3 組 query variant 都是零收穫）。
2. 有實際 evidence 證明 candidate evidence depth 不足（例如：找到候選人但證據來源
   太薄，補不到 Minimum Evidence Rule 第 15.9 節要求的門檻）。
3. Match yield 太低（找到的候選人，走完第 15 節判定漏斗後，真正判為 Match 的比例
   太低）。

**不得**因為「LinkedIn Recruiter 很專業」「104 是大型平台」這種理由推薦 Jacky 花錢，
必須具體說明：哪個職缺、免費來源卡在哪裡、搜了多少組 query、缺什麼資料、
付費來源理論上能補什麼，才能標 `CONSIDER_PAID_SOURCE`（見下方 ROI Evidence 要求）。
只要缺一項，就回傳 `SOURCE_READY_FREE`——意思是免費來源還沒用盡，不該考慮付費。
對應 `sourcing_engine.py` 的 `check_consider_paid_source(sourcing_run_id)`。

### 11.7 Paid Source 建議必須附 ROI Evidence

任何 `CONSIDER_PAID_SOURCE` 的建議，都必須附上具體說明文字，至少包含：

- 哪個職缺（`target_job` / `sourcing_run_id`）
- 免費來源搜了多少組 query（`query_variant_count`）
- 缺什麼資料（evidence depth 不足在哪個維度、比例多少）
- 理論上付費來源能補什麼（例如：LinkedIn Recruiter Lite 能看到完整 profile
  而不只是 public snippet；104 企業版能主動搜到未投遞但公開履歷的在職者）

沒有這份具體說明，不准標 `CONSIDER_PAID_SOURCE`。

### 11.8 Human Login 是補資料，不是啟動條件

如果 shortlist 中某位候選人的 LinkedIn 完整 profile 是 `AUTH_REQUIRED`，
可以標記 `HUMAN_PROFILE_REVIEW_REQUIRED` 丟給真人看（對應第 12 節 Human Review
Queue）——但真人只需要看少量 shortlist（第 12 節已限制每個職缺 5-10 位），
不需要 Jacky 從頭自己重新 sourcing 一遍。`AUTH_REQUIRED` 從來就不是「整個
sourcing 卡住」的理由，第 10.1 節早就講過這件事，這裡再次強調並延伸到
Paid Source 政策：**免費來源永遠是預設路徑，付費來源永遠是選配加速器。**

---

## 12. Human Review Queue 規則

搜尋完成之後，**不是**把上百筆 search hit 直接丟給顧問。

- 只把分類為 `MATCH_CANDIDATE`、`POSSIBLE_MATCH`、`HIGH_VALUE_INSUFFICIENT_DATA`
  的候選人放進真人審查佇列。
- 每個職缺最多 **5-10 位**，不能更多。
- 真人審查要補的，是 AI 拿不到的東西：
  - LinkedIn 登入後的完整 profile
  - 轉職意願
  - relocation 意願
  - 期望薪資
  - 機密經歷（AI 搜不到的內部資訊）
- 真人**不是**幫 AI 重新搜尋。如果真人審查發現 AI 給的名單根本不夠，
  代表這次 sourcing 沒有通過第 8.1 節的 `SEARCH_INCOMPLETE` 檢查，要回頭補搜，
  不是丟給真人自己去補搜尋量。

---

## 13. Final Hard Rule（原文照抄，不得改寫）

> 第一頁沒有，不代表網路沒有。沒有完成以上流程：`SEARCH_INCOMPLETE`，
> 不准宣稱找不到人，所有找不到都必須能被 audit log 回放證明。

---

## 14. 這份規則怎麼被使用

1. **任何 Phase X sourcing pilot 的 prompt，都應該先引用/嵌入這份 `SOURCING_RULES.md` 的內容**，
   而不是每次重新發明規則。開工前的第一步永遠是讀這份文件。

2. 開搜前，先呼叫 `sourcing_engine.py` 的 `new_run_id()` 產生 `sourcing_run_id`，
   再呼叫 `start_run()` 建立 `sourcing_runs` 紀錄，並依第 11 節產出這個職缺的
   `SOURCE_STRATEGY`。

3. 每跑完一組 query，呼叫 `log_query()` 寫一筆 `search_audit_log`。

4. 在下任何結論之前，呼叫 `check_search_incomplete()`；判斷是否該停之前，
   呼叫 `check_search_saturated()`。

5. 結束時呼叫 `finish_run()`，狀態只能是 `SEARCH_SATURATED` 或 `SEARCH_INCOMPLETE`
   ——如果 `check_search_incomplete()` 還是 True 卻硬要結案，`finish_run()` 會拒絕並拋出例外。

6. 任何 sourcing run 結束時，要能回答一份 `STEP1NE-SOURCING-RUN-{RUN_ID}.md`
   要求的問題清單（這份清單就是驗收這次 sourcing 有沒有做實的標準）：
   - 搜了多少 Query？
   - 哪些 Query Families？
   - 每組看了多少 results？
   - 搜了哪些 Sources？
   - 找到多少 Candidate？
   - 去重後多少？
   - Evidence Sufficient 多少？
   - MATCH 多少？
   - 哪個 Search Strategy Yield 最高？（用 `compute_yields()` 算）
   - 為什麼停止搜尋？（`SEARCH_SATURATED` 的具體證據，或說明還在 `SEARCH_INCOMPLETE` 補搜中）

回答不出來任何一題，就代表這次 sourcing 沒有照規則做，要回頭補。

---

## 15. 配對判定規則（Precision-First Match Guard）

給誰看：**下一個要判斷「這個候選人算不算符合這個職缺」的 AI agent**，不是給人看的報告。
這一節接續第 1-14 節——第 1-14 節管的是「怎麼找人」，這一節管的是「找到的人怎麼判定算不算真的符合」。
開始判定前先讀完這一節，讀完才能下 `MATCH_CANDIDATE`。

### 15.0 一句話原則

> **找人的時候可以寬，推薦人的時候必須非常窄。**

第 1-14 節允許、甚至要求 Query Expansion、Result Depth、Source Diversification 盡量把涵蓋面拉大——
搜尋端寬是對的，是規則要求的。但進 shortlist、進 `MATCH_CANDIDATE` 的那道門，必須收得非常緊。

Candidate Count 不是 KPI，Search Volume 也不是 KPI。真正的 KPI 是：

- **Precision**（真的符合的人 / AI 判定符合的人）
- **Evidence Quality**（證據夠不夠、夠不夠獨立、夠不夠有上下文）
- **Requirement Fit**（跟職缺的硬條件對不對得起來）
- **Recruitability**（這個人真的可能被獵動嗎）

這一節是**永久規則**，跟第 1-14 節一樣，不是又一輪職缺 pilot 的紀錄。
對應的可執行程式碼在 `matching_engine.py`，跟 `sourcing_engine.py` 是同一套基礎設施的兩半：
`sourcing_engine.py` 管「這個 sourcing run 有沒有真的搜過」，
`matching_engine.py` 管「這個 sourcing run 找到的候選人，能不能被判定為 Match」。
兩者用同一個 `sourcing_run_id` 串起來。

### 15.1 Search Before Search：先拆解 Requirement，才准開始找人

開始 sourcing 之前，必須先把這個職缺的 Job Requirement 拆成五類：

| 類別 | 意思 |
|---|---|
| `HARD_MUST` | 絕對門檻，缺了就不能是 Match，不管其他條件多漂亮 |
| `SOFT_MUST` | 應該要有，缺了會降級（例如從 MATCH_CANDIDATE 降到 POSSIBLE_MATCH），但不是絕對否決 |
| `NICE_TO_HAVE` | 加分項，有更好，沒有不影響 Gate |
| `UNKNOWN` | 目前資訊不足以歸類，先放這裡，不准硬塞進 PASS 或 FAIL |
| `EXCLUSION` | 出現這個條件直接排除（例如：目前仍在職且無任何轉職訊號、明顯的利益衝突） |

沒拆完這五類，狀態就是 **`SOURCING_BLOCKED_REQUIREMENT_INCOMPLETE`**，不准開始找人。
這不是「建議先想清楚」，是硬性關卡——對應 `matching_engine.py` 的 `check_requirement_ready()`。

### 15.2 HARD MUST 是絕對 Gate

任何一項 `HARD_MUST` 條件缺乏足夠證據（FAIL 或 UNKNOWN），就不准判 `MATCH_CANDIDATE`。
**分數補不回來**——不能用「其他八項都很漂亮」去抵銷一項 Hard Must 的缺口。
這是整份規則最重要的一條，也是 `matching_engine.py` 裡 `evaluate_candidate()` 的核心邏輯，
下面第 15.9 節會再強調一次並附上驗證方法。

### 15.3 完整判定漏斗

任何一個 search hit，要走完整條漏斗才能到 `MATCH_CANDIDATE`，不准跳關：

```
SEARCH_HIT
  （只有姓名 / headline，來自第 1-14 節的搜尋結果）
      ↓
CANDIDATE_DISCOVERED
  （職稱方向、職涯階段、產業、function 都看起來合理——這一步還不是 Match，
   只是「這個人值得繼續往下查」）
      ↓
── Requirement Gate（第 15.2、15.4-15.8 節的全部檢查）──
      ↓
MATCH_CANDIDATE / POSSIBLE_MATCH / INSUFFICIENT_DATA / NOT_MATCH
```

**不准**因為職稱像、公司大、名校、外商，就直接從 `SEARCH_HIT` 跳到 `MATCH_CANDIDATE`。
這四個字（職稱像、公司大、名校、外商）出現在判定理由裡，本身就是危險信號，
代表可能沒有走完 Requirement Gate。

### 15.4 Career Stage Gate（職涯層級關卡）

職涯層級要跟職缺一致，**即使 industry / skill 都符合**，seniority mismatch 仍然可能導致 `NOT_MATCH`。
典型例子：職缺是「產品助理」，候選人是「資深產品經理」——industry 對、skill 對，但層級不對，
理由要寫清楚，不能只寫「不符合」，要挑出具體原因：

- `SENIORITY_MISMATCH`（層級不對，過高或過低）
- `COMPENSATION_MISMATCH`（薪資帶對不上，資深的人不會接受助理級薪資）
- `CAREER_REGRESSION_RISK`（讓資深的人做較低階職務，通常留不住，容易空歡喜一場）
- `ROLE_SCOPE_MISMATCH`（職務範疇對不上，例如職缺只管一個小產品線，候選人管過整條 BU）

### 15.5 Seniority 不准只看 Title

Title 只能當 **`DISCOVERY_SIGNAL`**——值得注意、值得往下查，但不能直接當判定依據。

- Director 不代表一定資深（有些公司 Director 是中階）
- Manager 不代表一定有 8 年經驗（有些公司 25 歲就給 Manager title）
- COO 不代表一定懂供應鏈（可能是財務或行政背景出身的 COO）

要判定 Seniority，必須看：
- Career timeline（做了幾年、每個階段多久）
- Actual responsibilities（實際做了什麼，不是職稱寫什麼）
- Years of relevant experience（跟這個職缺相關的年資，不是總年資）
- Management scope（帶過多少人、多大預算、多大範圍）

只有 Title、沒有上面四項佐證 → Seniority 維度只能判 `UNKNOWN`，不能判 `PASS`。

### 15.6 Company Fit ≠ Candidate Fit

「任職於一家製造業公司」不代表「有製造業營運經驗」。這家公司底下有財務、HR、法務、行銷、
IT 等各種不直接碰營運的職能。**必須確認候選人本人實際做的 function**，
不能用「他任職的公司是做這行的」去反推「他懂這行的營運」。

### 15.7 Keyword Hit ≠ Functional Fit

Profile 裡出現 `operations`、`supply chain`、`manufacturing`、`international` 這些字，
不代表符合對應的 Functional 條件——要看上下文。

例如：「提升 software operations 效率」不能當「企業營運管理經驗」的證據，
因為 software operations（軟體維運）跟企業營運管理是完全不同的 function，
只是字面上都有 operations 這個詞。這種誤判是 keyword-matching 最常見的陷阱，
第 15.8 節的 False Positive Guard 就是專門用來擋這個。

### 15.8 False Positive Guard

進 shortlist 之前，一定要自問：**「有沒有可能這只是關鍵字碰巧命中？」**

檢查以下五種 Context 有沒有實際證據支撐，不是只看關鍵字本身有沒有出現：

- `TITLE_CONTEXT`（職稱脈絡：這個字出現在職稱裡，還是只出現在自介的某一句話）
- `INDUSTRY_CONTEXT`（產業脈絡：公司/產業別跟這個字對得起來嗎）
- `FUNCTION_CONTEXT`（職能脈絡：實際職責描述有沒有支撐這個字）
- `SENIORITY_CONTEXT`（層級脈絡：層級跟這個字所暗示的角色對得起來嗎）
- `CAREER_CONTEXT`（職涯脈絡：整條職涯路徑跟這個字合理嗎，還是這個字只出現一次、前後不連貫）

如果一個維度的主要證據**只有 keyword overlap**、上面五種 Context 一個都沒有，
**不准讓這個維度判 PASS，也不准讓這個候選人進 shortlist**。
對應 `matching_engine.py` 的 `check_false_positive_risk()`。

### 15.9 Minimum Evidence Rule（最低證據量規則）

進 `MATCH_CANDIDATE` 至少要有以下其中一種：

- **2 個以上獨立的專業證據來源**（例如：LinkedIn 個人頁 + 公司官網團隊介紹頁，兩個各自獨立佐證同一件事）
- **1 份足夠詳細的完整專業 Profile**（資訊完整到本身就能佐證多個維度，不需要靠拼湊）

單獨一則 search snippet（搜尋結果摘要那兩三行字）**不夠**，不能只憑這個就判 PASS，更不能憑這個判 MATCH_CANDIDATE。

### 15.10 UNKNOWN 不能偷偷算 PASS

`UNKNOWN` 就是 `UNKNOWN`，不能因為「順理成章應該是這樣」就偷偷算 PASS。

典型陷阱：`ENGLISH=UNKNOWN`，因為候選人「在外商工作」就想當然爾判 PASS——
在外商工作不代表英文一定好（可能只跟台灣同事用中文溝通），
除非有具體證據（例如履歷用英文寫、面試用英文進行過、有國際專案的英文溝通紀錄），
否則就維持 `UNKNOWN`，不准偷偷升級成 PASS。

任何一項 `HARD_MUST` 是 `UNKNOWN`，依第 15.2 節，最高只能判 `INSUFFICIENT_DATA`，不能判 `MATCH_CANDIDATE`。

### 15.11 Match Explanation 必須逐條，不准只給總分

輸出判定結果時，**不准**只給一個總分（例如「這個人 87 分」）就結束。
必須逐條列出每個維度的判定：

```
EXPERIENCE:     PASS / FAIL / UNKNOWN
INDUSTRY:       PASS / FAIL / UNKNOWN
FUNCTION:       PASS / FAIL / UNKNOWN
SENIORITY:      PASS / FAIL / UNKNOWN
ENGLISH:        PASS / FAIL / UNKNOWN
LOCATION:       PASS / FAIL / UNKNOWN
TRAVEL:         PASS / FAIL / UNKNOWN
COMPENSATION:   PASS / FAIL / UNKNOWN
RECRUITABILITY: NORMAL / LOW / VERY_LOW / UNKNOWN
```

每一條都要附上具體證據或「為什麼是 UNKNOWN」的理由，不能只寫代碼不寫理由。

### 15.12 分數不得覆蓋 Hard Fail

即使總分 95 分，只要有一項 `HARD_MUST` 是 `FAIL`，就不能判 `MATCH_CANDIDATE`。
分數只在「所有 Hard Must 都 PASS」的前提下，才拿來決定 `MATCH_CANDIDATE` 還是 `POSSIBLE_MATCH`。
分數從來不是用來「補償」或「抵銷」Hard Fail 的工具——這條跟第 15.2 節是同一件事，
在這裡再強調一次是因為這是最容易被「這個人條件真的很好」的直覺誤導而違反的一條。

### 15.13 Recruitability 與 Qualification 分開判斷

`QUALIFICATION`（這個人符不符合職缺條件）跟 `RECRUITABILITY`（這個人有沒有可能被獵動）
是兩件完全不同的事，**不准混在一起判**。

一個候選人可以 `QUALIFICATION=MATCH` 但 `RECRUITABILITY=VERY_LOW`，典型情況：

- Founder / Chairman / Company Owner（自己是老闆，沒有被獵動的理由）
- Family Successor（家族接班人，不會因為獵頭聯絡就跳槽）
- 已經是上市公司 CEO / 高階經營層（職涯天花板已經在頂端，向下或平移的機會通常沒有吸引力）

不要因為履歷漂亮、條件完美就把這種人塞進 outreach 名單——履歷再漂亮，
`RECRUITABILITY=VERY_LOW` 的人聯絡了也是浪費顧問的時間，這不是「多一個候選人沒關係」，
是會拉低整體 outreach 的 Precision 跟顧問對這份名單的信任。

### 15.14 Query Precision Feedback Loop（查詢精準度回饋迴圈）

這一條把第 15 節的判定結果，回頭餵給第 1-14 節的搜尋端，讓搜尋策略跟著校正：

每搜 10-20 個結果，就要算一次：

```
relevant_hits       = 分類為 CANDIDATE 或 POSSIBLE_CANDIDATE 且最終沒被判 NOT_MATCH 的數量
false_positive_hits = 分類為 CANDIDATE 但最終被判 NOT_MATCH 的數量
false_positive_rate = false_positive_hits / (relevant_hits + false_positive_hits)
```

**`false_positive_rate > 70%`** 就不准繼續用同一組 query，必須 `REFINE_QUERY`，
加上以下任一種限定條件重下：

- 產業限定
- Seniority 限定
- 公司類型限定
- Negative Keywords（見 15.15 節）
- Exact title（精確職稱，不用相鄰職稱）
- Functional keywords（職能關鍵字，不只是職稱關鍵字）

### 15.15 Negative Keywords（排除詞）

依這個職缺的 Requirement，自動建立排除詞清單，把明顯不對的結果先濾掉。

範例：職缺是「營運主管」，可能的排除詞：
`software operations`、`devops`、`sales operations`、`marketing operations`、
`student`、`intern`、`recruiter`、`consultant`、`freelance`

**這份排除詞只能基於「這個職缺」的 Requirement 建立**，不准做成永久的全域黑名單——
下一個職缺如果剛好就是要找 `sales operations` 的人，這份排除詞就完全不適用，
必須重新針對新職缺的 Requirement 建立新的一份。

### 15.16 Calibration Set（校準測試集）

每個重要職缺，開始大量 sourcing 之前，先建立一組校準案例：

- **3-5 個 GOOD MATCH**（人工已確認真的符合的候選人）
- **3-5 個 CLEAR NOT MATCH**（人工已確認明顯不符合的候選人）

AI 開始大量 sourcing 之前，先用這組案例測自己的判定邏輯：

- 每個 GOOD MATCH 案例都應該判成 `MATCH_CANDIDATE`，至少不能判成 `NOT_MATCH`
- 每個 CLEAR NOT MATCH 案例都不准被誤判成 `MATCH_CANDIDATE`

只要連人工已經確認過的案例都判錯，狀態就是 **`MATCHING_CALIBRATION_FAILED`**，
**禁止**進入大量 sourcing——邏輯都還沒校準對，搜得越多、錯得越多，不是越有幫助。
對應 `matching_engine.py` 的 `run_calibration_check()`。

### 15.17 False Positive Audit（事後稽核）

每一輪 sourcing 結束之後，從這個 run 判定為 `MATCH_CANDIDATE` 或 `POSSIBLE_MATCH` 的名單裡，
**隨機抽 5 個**（不足 5 個就全部抽），重新從 Evidence 從零開始判斷一次
（不能參考原本判定的結論，避免確認偏誤）。

如果這 5 個裡面 **超過 20% 被推翻**（也就是 5 個裡有 2 個以上原本判 Match、重新判卻不是），
觸發 **`PRECISION_ALERT`**，這個 run 判定出來的名單**不得直接進 Outreach**，
要先回頭檢查判定邏輯哪裡出錯、修正之後重新跑一次 Audit 才能放行。
對應 `matching_engine.py` 的 `run_false_positive_audit()`。

### 15.18 Precision KPI

每一輪 sourcing + matching 結束，要記錄以下四個指標：

| 指標 | 定義 |
|---|---|
| Candidate Precision | 真正符合的人 / AI 原本判定符合的人（**這是最重要的一個**） |
| False Positive Rate | 見 15.14 節 |
| Evidence Completeness | 有走完 Minimum Evidence Rule（15.9 節）的候選人比例 |
| Match Confirmation Rate | False Positive Audit（15.17 節）沒被推翻的比例 |

### 15.19 Optimization Goal 優先順序

```
Precision  >  Evidence Quality  >  Candidate Count
```

**不准優化 Candidate Count。** 找到 50 個「可能符合」但 Precision 只有 30% 的名單，
比找到 8 個「確認符合」的名單還糟糕——後者顧問看得完、也敢直接聯絡，
前者顧問要自己重新篩一次，等於白做工，而且會讓顧問下次不再信任 AI 給的名單。

### 15.20 Final Rule（原文照抄，不得改寫）

每一位進 shortlist 之前，都要能回答這幾個問題——任何一個答不清楚，就不准進 shortlist：

- 為什麼是這個人？
- 哪些條件已經證明？
- 哪些仍然未知？
- 有沒有可能只是關鍵字誤中？
- 職涯層級合理嗎？
- 這個人真的可能被獵動嗎？

> **SEARCH DEEP，但 MATCH STRICT。**

### 15.21 這一節怎麼被使用

1. 開始 sourcing 前，先呼叫 `matching_engine.py` 的 `check_requirement_ready(job_slug)`——
   沒有現成的 requirement snapshot 就先做第 15.1 節的五類拆解、存進去，才能往下搜。
2. 每判定一個候選人，呼叫 `evaluate_candidate(candidate_evidence, requirement_snapshot)`，
   拿到逐條判定表跟最終 verdict，寫進 `candidate_job_match` 表。
3. 覺得某個候選人可能只是關鍵字碰巧命中，呼叫 `check_false_positive_risk(candidate_evidence)` 先自查。
4. 大量 sourcing 前，先呼叫 `run_calibration_check(job_slug, good_cases, bad_cases)`，
   沒過就不准往下跑。
5. 每輪結束，呼叫 `run_false_positive_audit(sourcing_run_id)` 抽查，
   超過 20% 被推翻就是 `PRECISION_ALERT`，不得直接進 Outreach。

---

## 16. 彈性搜尋廣度／嚴格配對標準（Adaptive Precision/Recall）

給誰看：跟第 15 節一樣，給下一個要做 sourcing／matching 的 AI agent。
這一節緊接第 15 節之後，兩節**不衝突、管的是不同問題**：

- 第 15 節「Precision-First Match Guard」管的是：**符合不符合，怎麼判定才不會誤判**。
- 第 16 節（這一節）管的是：**人不夠的時候，怎麼合法地把搜尋網撒更廣，同時絕不放寬判定門檻**。

一句話原則：

> **放寬搜尋，不放寬標準。（Broad Discovery, Strict Matching）**

### 16.0 為什麼要有這一節

第 15.3 節的判定漏斗原本是 `SEARCH_HIT → CANDIDATE_DISCOVERED → 直接走 Requirement Gate`。
但如果在 Discovery 階段就要求所有 `HARD_MUST` 都先被證明，會有一個副作用：
**漏掉真正有希望、但證據還沒補齊的人**——這種人可能職稱對得上、function 也合理，
只是還沒查到年資或英文能力的直接證據，如果 Discovery 這一關就用 Match 等級的
嚴格標準去篩，這種人根本不會被留下來繼續查證。

這一節在漏斗中間插入一個「Shortlist」中間層，把「先找到值得查的人」跟
「嚴格驗證這個人到底符不符合」拆成兩個獨立的步驟，並加上「候選人太少時
怎麼自動放寬搜尋範圍」的規則——但放寬永遠只發生在搜尋端，判定端（第 15 節那套）
一個字都不能改鬆。

### 16.1 三段式漏斗（取代第 15.3 節那個兩段式的簡化版本）

```
SEARCH_HIT
  （只有姓名 / headline）
      ↓
CANDIDATE_DISCOVERED
  （職稱方向、職涯階段、產業、function 看起來合理——這一步還不是 Match）
      ↓
── Discovery 階段：寬鬆 ──────────────────────────────────
POSSIBLE_CANDIDATE
  只要符合下面任一項訊號，就可以先進這個狀態，不需要所有 HARD_MUST 都已被證明：
    - relevant title（職稱相關）
    - OR relevant function（職能相關）
    - OR relevant company/industry（公司/產業相關）
  這一步刻意寬鬆，因為篩太早、篩太嚴，會把證據還沒補齊但其實符合的人濾掉。
      ↓
── Shortlist 階段：開始嚴格驗證 ──────────────────────────
SHORTLISTED
  這一步才開始逐條嚴格驗證：
    experience / industry / function / seniority / English / location /
    travel / recruitability
  驗證方式就是第 15 節整套規則（False Positive Guard、Minimum Evidence Rule……）。
      ↓
── Match 階段：絕對 Gate（就是 evaluate_candidate()，邏輯完全等於第 15 節）──
    HARD_MUST 才是絕對 Gate：
      - 缺證據（UNKNOWN）→ INSUFFICIENT_DATA
      - 明確不符（FAIL）→ NOT_MATCH
      - 證據完整且符合（PASS）→ MATCH_CANDIDATE（HARD/SOFT 都過）或
        POSSIBLE_MATCH（HARD 過但 SOFT 沒全過，或有 False Positive 風險）
```

`POSSIBLE_CANDIDATE` 是 Discovery 階段的中間狀態，跟 15.3 節、第 8-9 節既有的
`MATCH_CANDIDATE` / `POSSIBLE_MATCH` 這兩個**最終 verdict** 是不同層級的東西，
不要混用——`POSSIBLE_CANDIDATE` 是「值得往下驗證」，`POSSIBLE_MATCH` 是「驗證完，
但沒有全過」，兩者中間隔著整個 Shortlist 驗證階段。

對應 `matching_engine.py` 的 `DISCOVERY_FUNNEL_STAGES` 常數。

### 16.2 找到的人太少時：依序放寬 Discovery 條件，不准直接放棄

如果候選人數不夠，**不准**立刻宣稱 `NO_CANDIDATE` 或 `NOT_FOUND_IN_CURRENT_SEARCH_SCOPE`
（那是第 10 節的結論，前提是已經走完第 8.1 節 `SEARCH_INCOMPLETE` 檢查）。
應該依序放寬 Discovery 條件，放寬順序固定六層，**不能跳層**：

```
EXACT_TITLE → ADJACENT_TITLE → RELATED_FUNCTION
  → RELATED_INDUSTRY → RELATED_COMPANY_TYPE → CAREER_PATH_SIMILARITY
```

- `EXACT_TITLE`：精確職稱（對應第 3.1 節 Exact Titles）
- `ADJACENT_TITLE`：相鄰/相近職稱（對應第 3.2 節 Adjacent Titles）
- `RELATED_FUNCTION`：相關職能（不限職稱，找做同一種事的人）
- `RELATED_INDUSTRY`：相關產業（同產業不同職能角度切入）
- `RELATED_COMPANY_TYPE`：相關公司類型（同規模/同商業模式的公司，不限產業別）
- `CAREER_PATH_SIMILARITY`：職涯路徑相似（用職涯發展軌跡找人，不再限定職稱/產業/公司類型）

**只放寬搜尋，不放寬最終 Match 標準**——這六層只決定「Discovery 階段要不要繼續往下
找更多候選人」，跟 Shortlist、Match 兩個階段的判定標準完全無關，那兩階段永遠是
第 15 節那套嚴格規則，不會因為搜到第幾層而跟著鬆動。

### 16.3 Adaptive Widening Rule

`Candidate Discovered < 10` → 自動 `WIDEN_SEARCH`，而且**每次只放寬一層**，
不能一次跳兩層（例如不能因為心急就直接從 `EXACT_TITLE` 跳到 `RELATED_INDUSTRY`）。

對應 `matching_engine.py` 的 `check_needs_widening()`（判斷要不要放寬、算出下一層是什麼）
跟 `next_widening_layer()`（擋下跳層請求）。

如果已經放寬到最後一層 `CAREER_PATH_SIMILARITY` 還是不夠 10 人，這時候才允許照
第 10 節的固定表述下 `NOT_FOUND_IN_CURRENT_SEARCH_SCOPE`（前提仍然要先通過
第 8.1 節 `SEARCH_INCOMPLETE` 檢查）。

### 16.4 不准放寬 HARD MUST

搜尋可以變寬（六層一路放到 `CAREER_PATH_SIMILARITY`），但 `MATCH_CANDIDATE` 的標準
**不得因為人太少而下降**。候選人數再怎麼不夠，`evaluate_candidate()` 的 Hard Must
Gate 邏輯（第 15.2 節）都不會被放鬆——這是結構上天生就成立的事：
`evaluate_candidate()` 的輸入只有候選人證據跟 Requirement Snapshot，
根本不知道、也不需要知道這個候選人是從放寬序列第幾層搜出來的。
放寬跟判定，是兩支完全獨立、不互相傳遞資訊的邏輯。

### 16.5 Precision/Recall Balance：Recall First, Precision Later

搜尋目標的優先順序，在 Discovery 這一步跟第 15.19 節「Precision > Evidence Quality
> Candidate Count」的整體優先順序並不衝突——那一條講的是**最終進 shortlist/outreach
的名單**要優先 Precision；這一條講的是**Discovery 這一步**要優先 Recall：

> 先不要漏掉可能的人 → 再把不符合的人淘汰。

也就是說：**Discovery 寬進來，Shortlist/Match 嚴出去**。整個系統的 Precision
最終還是由第 15 節那套規則守住，不會因為 Discovery 端 Recall 優先而被稀釋。

### 16.6 Final Rule（原文照抄，不得改寫）

> 找不到人時，先放寬搜尋條件，不降低錄取標準。
> Search 可以寬，Match 必須嚴。
> **放寬搜尋，不放寬標準。**

### 16.7 這一節怎麼被使用

1. Sourcing 進行中，候選人數不夠時，呼叫 `check_needs_widening(sourcing_run_id)`——
   如果 `needs_widening=True`，依回傳的 `next_layer` 決定下一輪要用哪一層的 query
   （對照 16.2 節的六層定義去產生新的 Query Family 變體）。
2. 實際往下放寬一層之後，呼叫 `advance_widening_layer(sourcing_run_id)` 把進度記下來，
   下次再呼叫 `check_needs_widening()` 才知道現在在哪一層。
3. 候選人從 Discovery 進到 Shortlist、開始逐條驗證第 15.11 節那八個維度時，
   照樣呼叫 `evaluate_candidate()`——這個函式的行為完全不受候選人是從放寬序列
   第幾層找到的影響，第 15 節的判定規則原封不動適用。
4. 已經放寬到 `CAREER_PATH_SIMILARITY` 還是不夠，且已通過 `SEARCH_INCOMPLETE`
   檢查，才能用第 10 節的固定表述說「在目前已執行的搜尋範圍內還沒找到」。
