-- 2026-10-23 特殊職缺檢查卡（第一個套用：築樂 主管／家庭專屬司機 executive-family-driver-taipei）
-- jobs 表已滿 100 欄，開關放 site_settings：key='special_check_jobs'，value = JSON 陣列（職缺 slug）。
-- 只新增，不動舊表。

-- 每位人選一張檢查卡（AI 讀履歷產的疑點＋三個分數）
CREATE TABLE IF NOT EXISTS special_check_cards (
  application_id TEXT PRIMARY KEY,
  job_slug       TEXT,
  status         TEXT,            -- queued / ready / error
  result_json    TEXT,            -- {resume_flags:[...], scores:{...}, ...}
  needs_refresh  INTEGER DEFAULT 0,  -- 1 = 有新出席紀錄／面談結束／顧問按重算，等 ai_worker 重產
  generated_at   TEXT,
  updated_at     TEXT,
  error          TEXT,
  worker_id      TEXT
);

-- 面談出席紀錄（顧問在卡片上按「有到／放鳥」，一次約面記一筆）
CREATE TABLE IF NOT EXISTS special_check_attendance (
  id             TEXT PRIMARY KEY,
  application_id TEXT NOT NULL,
  appointment_id TEXT,            -- 對應 interview_appointments.id（系統外約的就留空）
  at             TEXT,            -- 約定的面談時間（顧問填或帶入）
  result         TEXT NOT NULL CHECK (result IN ('attended','no_show')),
  by             TEXT,
  note           TEXT,
  created_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_sc_att_app ON special_check_attendance(application_id);

INSERT OR IGNORE INTO site_settings (key, value, updated_at)
VALUES ('special_check_jobs', '["executive-family-driver-taipei"]', datetime('now','+8 hours'));
