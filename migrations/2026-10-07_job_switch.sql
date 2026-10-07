-- 2026-10-07 面談前改談其他職缺（阿財自己判斷要不要改談、面談後自動判斷要不要改回）
-- 只新增，不改任何既有表：jobs 已經 100 欄（D1 上限），applications 92 欄要留給別的功能，
-- 所以改談紀錄放在兩張小表。

-- 每一次改談一列。同一筆應徵同時最多一列 status='active'。
CREATE TABLE IF NOT EXISTS application_job_switches (
  id               TEXT PRIMARY KEY,
  application_id   TEXT NOT NULL,
  orig_job_slug    TEXT NOT NULL,
  orig_job_title   TEXT,
  target_job_slug  TEXT NOT NULL,
  target_job_title TEXT,
  source           TEXT NOT NULL,          -- auto（阿財自動判斷）／manual（人工）／backfill（補登）
  reason           TEXT,                   -- 內部理由（只給顧問看）
  note_block       TEXT,                   -- 寫進 pre_interview_note 的那一段原文，取消時照原文拿掉
  created_by       TEXT,
  created_at       TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'active',
                   -- active（等面談）／cancelled（面談前取消）／checking（面談後判斷中）
                   -- kept（人選同意，維持新職缺）／reverted（人選不同意，已改回原職缺）／unclear（判斷不出來，留給顧問）
  cancelled_by     TEXT,
  cancelled_at     TEXT,
  checked_at       TEXT,
  check_result     TEXT,                   -- ORIG／TARGET／UNKNOWN
  check_note       TEXT,
  tg_message_id    TEXT,
  worker_id        TEXT
);
CREATE INDEX IF NOT EXISTS idx_ajs_app ON application_job_switches(application_id);
CREATE INDEX IF NOT EXISTS idx_ajs_status ON application_job_switches(status);

-- 面談前「要不要改談」的每一次判斷都留一列（不管有沒有改），方便事後稽核。
CREATE TABLE IF NOT EXISTS job_switch_checks (
  application_id   TEXT PRIMARY KEY,       -- 一筆應徵只判斷一次
  status           TEXT NOT NULL,          -- queued／done／error
  decision         TEXT,                   -- switched／keep／blocked:<原因>
  applied_job_slug TEXT,
  target_job_slug  TEXT,
  confidence       TEXT,
  reason           TEXT,
  raw_json         TEXT,
  created_at       TEXT NOT NULL,
  decided_at       TEXT,
  worker_id        TEXT
);
