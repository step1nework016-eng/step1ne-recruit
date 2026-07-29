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
