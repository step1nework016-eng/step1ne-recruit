-- 2026-10-23 電洽先行→阿財接手（測試版）。applications 已 95 欄，流程資料放獨立表。
CREATE TABLE IF NOT EXISTS call_first_flow (
  application_id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, created_by TEXT,
  tg_chat_id TEXT, tg_thread_id INTEGER, transcript TEXT, highlights TEXT,
  asked_json TEXT, questions_json TEXT, job_mode TEXT, email TEXT,
  mail_sent_at TEXT, mail_status TEXT, line_bound_at TEXT, line_pushed_at TEXT,
  resume_notified_at TEXT, flag_mode TEXT);
-- 開關：off（預設＝岔路不出現）／test（只對 Jacky、姓名含「測試」、信只寄 aiagentg888@gmail.com）／on
INSERT INTO site_settings (key, value, updated_at) VALUES ('call_first_flow', 'test', datetime('now','+8 hours'))
  ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at;
