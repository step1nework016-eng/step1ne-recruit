-- 2026-10-21 阿財面談結尾推薦職缺（acai_job_recs.py；ensure_tables() 也會 CREATE IF NOT EXISTS，這份是正式紀錄）
-- jobs 表 100 欄已滿，不加欄位。開關：site_settings key='acai_job_recs'，值 off／test／on。
CREATE TABLE IF NOT EXISTS acai_job_recs (
  application_id TEXT NOT NULL,
  job_slug       TEXT NOT NULL,
  rank           INTEGER,
  title          TEXT,
  payload_json   TEXT,      -- 給人選看的白名單欄位＋why/confirm（另含 fit、evidence 只給顧問）
  created_at     TEXT NOT NULL,
  wanted_at      TEXT,      -- 人選按「也幫我推這個」的時間
  notified_at    TEXT,      -- TG 通知顧問的時間
  PRIMARY KEY (application_id, job_slug)
);
CREATE TABLE IF NOT EXISTS acai_job_rec_runs (
  application_id TEXT PRIMARY KEY,
  status         TEXT NOT NULL,   -- pending／ready／none／error
  mode           TEXT,            -- 當時的開關 test／on
  detail         TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT
);
INSERT INTO site_settings (key, value, updated_at) VALUES ('acai_job_recs', 'test', datetime('now','+8 hours'))
  ON CONFLICT(key) DO NOTHING;
