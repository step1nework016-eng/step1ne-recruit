-- Step1ne 招募：應徵表單 + 預約
-- 設計原則：所有招募管道（社群／SEO／廣告／LINE 社群）都導到同一張表單，
-- 所以這裡的資料必須能回答「哪條管道帶來的人最後成案」。

CREATE TABLE IF NOT EXISTS applications (
  id            TEXT PRIMARY KEY,
  created_at    TEXT NOT NULL,

  -- 職缺
  job_slug      TEXT NOT NULL,           -- 對應 step1ne.com/jobs/<slug>
  job_title     TEXT,

  -- 基本資料（最少可用集合，多一欄就少一些完成率）
  name          TEXT NOT NULL,
  email         TEXT NOT NULL,
  phone         TEXT,

  -- 硬條件：這三項不問，AI 面談時全部要重問一次
  expected_salary   TEXT,
  available_date    TEXT,
  location_ok       TEXT,

  -- 履歷：檔案存 files 表，或使用者自己貼連結
  resume_file_id    TEXT,
  resume_url        TEXT,
  note              TEXT,

  -- 來源歸因。沒有這幾欄就無法判斷廣告錢花得值不值得。
  utm_source    TEXT,
  utm_medium    TEXT,
  utm_campaign  TEXT,
  referrer      TEXT,

  -- 流程狀態
  status        TEXT NOT NULL DEFAULT 'new',
                -- new / booked / interviewed / reported / passed / rejected / no_show
  consent_at    TEXT NOT NULL,           -- 個資告知同意時間，法律要求
  handled_by    TEXT,
  handled_note  TEXT
);

CREATE INDEX IF NOT EXISTS idx_app_status  ON applications(status);
CREATE INDEX IF NOT EXISTS idx_app_job     ON applications(job_slug);
CREATE INDEX IF NOT EXISTS idx_app_created ON applications(created_at);

-- 履歷檔。之後換 R2 時這張表可以只留 metadata。
CREATE TABLE IF NOT EXISTS files (
  id          TEXT PRIMARY KEY,
  created_at  TEXT NOT NULL,
  filename    TEXT,
  mime        TEXT,
  size        INTEGER,
  content_b64 TEXT
);

-- 預約時段。no_show 要記——那個比例決定這個流程值不值得繼續。
CREATE TABLE IF NOT EXISTS bookings (
  id            TEXT PRIMARY KEY,
  application_id TEXT NOT NULL,
  slot_start    TEXT NOT NULL,           -- ISO8601，台北時間
  created_at    TEXT NOT NULL,
  reminded_1d   TEXT,
  reminded_15m  TEXT,
  attended      INTEGER,                 -- NULL 未到時間／1 有到／0 沒出現
  FOREIGN KEY (application_id) REFERENCES applications(id)
);

CREATE INDEX IF NOT EXISTS idx_book_slot ON bookings(slot_start);

-- 面談逐則對話。這是法律證據，不刪。
CREATE TABLE IF NOT EXISTS messages (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id TEXT NOT NULL,
  role          TEXT NOT NULL,           -- assistant / candidate
  content       TEXT NOT NULL,
  created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_msg_app ON messages(application_id);

-- 初篩報告與顧問的處置。處置要回寫，否則永遠不知道 AI 判斷準不準。
CREATE TABLE IF NOT EXISTS reports (
  id             TEXT PRIMARY KEY,
  application_id TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  content_md     TEXT NOT NULL,
  hard_pass      INTEGER,                -- 硬條件是否全數符合
  recommend      TEXT,                   -- worth_interview / need_more_info / not_fit
  consultant_decision TEXT,              -- 顧問實際怎麼處置
  decided_at     TEXT
);

-- 職缺主檔（內部版）。跟官網公開的職缺頁是同一個職缺，但這裡帶客戶身分。
--
-- 為什麼要分開：官網職缺頁與表單的職缺清單是匿名的（公開內容不能出現客戶名稱），
-- 但 AI 面談時候選人有權知道自己在應徵哪一家公司。同一份資料，兩種曝光層級。
--
-- ⚠️ client_name 只能出現在需要驗證的內部端點，
--    不可以出現在 /apply 或任何公開回應裡。
CREATE TABLE IF NOT EXISTS jobs (
  slug          TEXT PRIMARY KEY,        -- 對應 step1ne.com/jobs/<slug>
  title         TEXT NOT NULL,
  updated_at    TEXT NOT NULL,

  -- 內部才看得到
  client_name   TEXT,                    -- 真實公司名稱
  client_intro  TEXT,                    -- 給候選人的一兩句介紹（產業、規模、在做什麼）
  hiring_manager TEXT,

  -- 硬條件：拆成可比對的欄位，面談時逐條對照。
  -- 只存一段 JD 文字的話，AI 每次都要自己重新解讀，結果會不一致。
  years_min     INTEGER,
  must_skills   TEXT,                    -- 逗號分隔
  salary_min    INTEGER,
  salary_max    INTEGER,
  salary_unit   TEXT DEFAULT 'MONTH',
  locations     TEXT,                    -- 逗號分隔
  employment    TEXT,                    -- 正職 / 派遣
  onboard_by    TEXT,                    -- 客戶希望到職日
  jd_url        TEXT,
  notes         TEXT,                    -- 顧問備註：這個客戶在意什麼、踩過什麼雷
  status        TEXT NOT NULL DEFAULT 'open',

  -- 候選人在「反問時間」最常問的，官網上都沒有。
  -- 顧問把答案先填在這裡，阿財才回答得出來——
  -- 答不出來就只能推給下一階段，那會讓對方覺得這場面談沒什麼用。
  team_size        TEXT,                 -- 目前編制，例：BIM 團隊 6 人，這次補 2 位
  interview_rounds TEXT,                 -- 用人單位要面幾次，例：2 次
  interview_who    TEXT,                 -- 分別跟誰面，例：一面人資＋用人主管，二面協理
  has_test         TEXT,                 -- 有無測驗與內容，例：Revit 實作 40 分鐘
  faq_notes        TEXT                  -- 其他可以直接回答候選人的事（自由文字）
);
