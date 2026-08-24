# Step1ne 獨立驗證規則手冊

2026-08-24 建立。這份文件是「每個職缺配一位 AI 獵頭顧問」大架構下的最後一道防線：
案件經理 AI 判斷 → 調度員派工 → 六個專職引擎執行（客戶開發、主動搜尋、配對防呆、
候選人聯繫、阿財初談、案件追蹤）→ **獨立驗證** → 結果寫回決策記憶。

設計範本是 `~/aijob-ops/ops/verify.py`（AIJOB「AI 老闆」系統已經在跑、已經驗證過真的
有效的獨立查證器）。這份手冊把它的規則精神原封不動搬過來，套在 Step1ne 六個引擎上；
可執行的部分在同目錄的 `verification_engine.py`。

---

## A. 黃金規則（抄自 `ops/verify.py`，不弱化）

這三條寫在 `ops/verify.py` 開頭的規則，是整套機制的靈魂，任何引擎要接上獨立驗證，
都必須遵守：

1. **查證者不得是建造者本人。** 同一個 agent 呼叫不能既執行又驗證自己——這在
   `ops/verify.py` 裡是 `ops.record_verification()` 直接擋掉的（同一個 `executor`
   字串不能既是 execution 的 executor 又是 verification 的 verifier）。`verification_engine.py`
   裡的 `log_verification()` 沿用同一個精神：`verifier_run_id` 必須跟被驗證那次執行的
   run id 不同，呼叫端要用一個獨立啟動的 agent／session 去跑驗證，不能是原本執行任務
   的同一個 context 順手驗一下自己。

2. **查證器不准讀執行者自己寫的紀錄、log、宣稱的結果當作證據。** 只能拿識別碼（工單ID、
   候選人ID、sourcing_run_id、application_id 等）重新去問下游真相。`ops/verify.py` 的
   `verify_resend()` 完全不看 builder 寫的「已送達」，是真的重打 Resend API 問投遞狀態；
   `verify_http()` 是真的重新 curl 正式網址。Step1ne 這邊同理：不能因為
   `sourcing_runs.notes` 裡寫「已深搜過」就信，要重新查 `search_audit_log` 這張原始紀錄表；
   不能因為 `candidate_job_match.match_status='MATCH_CANDIDATE'` 就信，要重新跑一次判定邏輯。

3. **每個數字都要查得到原始來源，拿不到就是「查不到」，不是「應該有做」。**
   `ops/verify.py` 對應到的狀態是 `UNKNOWN`；Step1ne 這邊對應到 `INSUFFICIENT_EVIDENCE`
   （見下方 C 節）。不接受「理論上完成了」「AI 應該有查過」這種說法——沒有原始資料可查，
   就老實記錄查不到，不准猜一個結論出來湊數。

---

## B. 六個引擎，各自怎麼驗證

Step1ne「每個職缺配一位 AI 獵頭顧問」架構下的六個專職引擎，執行完之後都要有對應的
獨立驗證方式。現況：`主動搜尋`／`配對防呆`／`阿財初談`／`案件追蹤` 四個引擎已存在（對應
`sourcing_engine.py`／`matching_engine.py`／`interview_daemon.py`／`placement_tracker.py`），
`客戶開發`／`候選人聯繫` 兩個引擎目前還在規劃中，尚未動工。

### B-1. 客戶開發（規劃中，尚未建）

驗證方式應該是：
- 重新打開找到的職缺公開頁面／公司官網，確認這間公司、這個職缺真的存在（不是引擎自己
  說「找到了」就信）。
- 確認決策窗口（用人主管／HR）的聯絡資訊是公開可查證的來源（官網、公開徵才頁、公開
  商業登記資料），不是引擎憑印象猜的信箱格式或猜的姓名。
- 不接受客戶開發引擎自己回報「找到窗口了」當作證據——要看得到那筆聯絡資訊的來源網址
  或截圖。

這個引擎還沒建，這條規則先寫在這裡，等引擎動工時同步實作對應的 `verify_client_lead()`。

### B-2. 主動搜尋（`sourcing_engine.py`）

對應 `verification_engine.py` 的 `verify_sourcing_run(sourcing_run_id)`：

- 重新查 `search_audit_log` 表，確認 `sourcing_runs.query_variant_count` 聲稱的
  query 數量，真的有對應的 `search_audit_log` 紀錄可回放——不是嘴上說「已經深搜過」，
  要能列出每一組 query 原文、跑了幾筆結果、找到幾個候選人。
- 如果 `sourcing_runs` 記的數字跟 `search_audit_log` 實際列數對不上（例如聲稱跑了 20 組
  卻只查得到 5 筆 log），直接判 `VERIFY_FAILED`。
- 抽查候選人的 `evidence_sufficient_count`：不能只信這個彙總數字，要抽查對應候選人紀錄
  裡的 `evidence_quality` 標記，是不是真的對應到 `sources` 欄位裡的真實連結（不是空的
  或猜測性的描述）。

### B-3. 配對防呆（`matching_engine.py`）

對應 `verify_matching_result(candidate_job_match_id)`：

- 讀 `candidate_job_match` 該筆紀錄的 `candidate_snapshot`、`requirement_snapshot`，
  獨立重新逐條核對每個 Hard Must 維度的證據，確認判定為 `MATCH_CANDIDATE` 的紀錄，
  沒有任何一項是 `FAIL` 或 `UNKNOWN` 卻被放行過關（`matching_engine.py` 的
  `evaluate_candidate()` 邏輯本身已經把「任一 Hard Must FAIL/UNKNOWN 就不可能是
  MATCH_CANDIDATE」寫死在聚合規則裡——獨立驗證要做的是確認**寫進資料庫的那筆紀錄**
  真的符合這條邏輯，防的是「執行者判定時腦補放水、或紀錄被人工／其他流程覆寫」這種
  資料庫紀錄跟判定邏輯對不上的情況）。
- 有能力的話，直接把 `candidate_snapshot` 跟 `requirement_snapshot` 重新餵一次
  `evaluate_candidate()`，拿到獨立算出的 `final_verdict`，跟資料庫裡存的
  `match_status` 比對；兩者不一致就標記出來，不能只靠肉眼核對就下結論。

### B-4. 候選人聯繫（規劃中，尚未建）

驗證方式應該是：完全比照 `ops/verify.py` 的 `verify_resend()` 精神——不信資料庫裡
`status='sent'` 這個欄位，要去問實際寄信服務商（例如 Resend）的 API，確認訊息真的送達
（`last_event` 是 `delivered`/`sent` 這類終態，而不是還在 `queued` 的暫時狀態）。
這條規則等候選人聯繫引擎動工時，直接搬 `ops/verify.py` 的 `verify_resend()` 實作過來，
不用重新設計。

### B-5. 阿財初談（`interview_daemon.py`）——最有現實意義的一條

對應 `verify_interview_accuracy(application_id)`：

- 重新讀該應徵者的 `messages` 表逐字稿，抽出裡面提到的僱用型態／薪資數字／其他關鍵
  事實，跟 `jobs` 表裡的資料比對。
- **為什麼這條規則重要——2026-08-14 真實事故**：候選人吳秉洋（application_id
  `1dc94c8d-a430-414b-8b68-268134167db3`，職缺 `bim-engineer`）在 8/14 19:18 的面談中，
  被阿財告知「BIM工程師，月薪41K–62K，地點在苗栗，這個案子是**派遣**進駐」，後續議薪
  段落也是照「派遣期間薪水勞健保我們公司處理」「派遣做滿3個月到1年、表現良好有機會
  **轉正**」的口徑在講。但截至 2026-08-24 查證當下，`jobs` 表裡這個職缺的
  `title` 已經是「BIM 工程師（無經驗可・**正職**）」、`employment` 是
  `['FULL_TIME']`、`salary_min/salary_max` 是 `40,833–50,167`（`updated_at`
  是 2026-08-21，明顯晚於 8/14 那場面談）。如果當初面談結束後有跑過這種比對，
  這種「候選人被告知了一個已經過期、現在已經不成立的僱用條件」的資訊外洩事故，
  本來是可以在送出報告前就被攔下來的。
- **這支函式做不到的事，要老實承認**：`jobs` 表沒有版本歷史，無法重建「面談那一刻
  jobs 表長什麼樣」，只能拿逐字稿跟 jobs 表**現在**的資料比對。不一致時標記
  `POTENTIAL_STALE_INFO`，附上面談時間與內容摘要，讓真人判斷要不要主動聯繫候選人
  更正——**不能自動判定「阿財當初講錯了」**，因為也可能是面談當下資料本來就是這樣，
  是後來才改的。這支程式的輸出是「值得真人去核對的落差清單」，不是「錯誤判決書」。
  誤用成自動下定論的工具，本身就會製造新的誤判。

### B-6. 案件追蹤（`placement_tracker.py`）

對應驗證邏輯（`verification_engine.py` 尚未針對這條寫獨立函式，先在此記錄規則，供未來
擴充）：

- 驗證 `placements.evidence_log` 欄位裡的每筆階段轉換，是不是真的有對應的具體依據
  （`placement_tracker.py` 目前的寫法已經是每次轉階段都附一句人類可讀的理由，例如
  「查無 interview_appointments 紀錄，查無 client_feedback 欄位」），不是只有
  `stage_since` 時間戳跳動、`evidence_log` 卻是空的或只有籠統的「已確認」三個字。

---

## C. 驗證結果的正式分類

每次驗證，只能落在這三種之一，附理由，寫進 `verification_log`：

| 結果 | 意思 |
|---|---|
| `VERIFIED` | 重新查了下游／原始資料，確認執行者的宣稱屬實 |
| `VERIFY_FAILED` | 重新查了下游／原始資料，發現跟宣稱不一致（數字對不上、邏輯放水、資料矛盾） |
| `INSUFFICIENT_EVIDENCE` | 查不到原始資料（表不存在、資料被清空、D1 連續逾時查不到），**不能硬猜一個結論** |

`verify_interview_accuracy()` 額外用 `POTENTIAL_STALE_INFO` 標記「面談內容跟 jobs 表現況
不一致」——這不是 `VERIFY_FAILED`（因為不確定是誰的錯），是提示真人去判斷的旗標，最終仍要
落到上面三種正式分類之一（通常是 `VERIFIED` 附帶 `discrepancies_found` 非空，讓真人看到）。

每筆驗證都要寫進 `verification_log`，不能查完就丟掉不記錄——查證本身也是一次「發生過的事」，
沒寫回去，下次沒人知道這個引擎、這筆紀錄到底驗證過沒有。

---

## D. 這份手冊怎麼被使用

未來任何一個引擎（不論是現有四個還是規劃中的兩個）執行完一個工單／一筆紀錄之後，
不能靠執行者自己回報「做完了、沒問題」就直接推進到下一步。標準流程是：

1. 執行者完成任務，留下識別碼（`sourcing_run_id`、`candidate_job_match_id`、
   `application_id` 等）。
2. 由一個**獨立於執行者的 agent/session**呼叫 `verification_engine.py` 對應的
   `verify_*()` 函式。
3. 驗證函式只吃識別碼，自己重新去查原始資料（D1 裡的原始表、或未來的外部 API），
   不讀執行者留下的摘要文字。
4. 結果連同理由寫進 `verification_log`（`log_verification()`）。
5. `VERIFY_FAILED` 或 `INSUFFICIENT_EVIDENCE` 的案例，不得自動推進到下一階段
   （例如配對防呆 `VERIFY_FAILED` 的候選人不能自動送去客戶端；阿財初談
   `POTENTIAL_STALE_INFO` 的案例要留給真人決定要不要主動聯繫候選人）。

D1 常見兩種暫時性錯誤（7429 rate limit/timeout、7009 不可用），照 `sourcing_engine.py`／
`matching_engine.py` 既有的 `d1()` 重試包裝處理，重試 2-3 次；重試後仍失敗，誠實記
`INSUFFICIENT_EVIDENCE`，不要假裝查到了。
