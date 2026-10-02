-- 2026-10-02 Jacky：人選卡片「拿這位人選去開發客戶」。AI 找到的公司先放「人選敲門名單」，
-- 顧問篩過按「加入開發進度」才進 bd_outreach——不跟已經電洽過的開發對象混在一起。
CREATE TABLE IF NOT EXISTS cand_bd_runs (
  id             TEXT PRIMARY KEY,
  application_id TEXT NOT NULL,
  requested_by   TEXT,
  status         TEXT NOT NULL,      -- queued / done / failed
  brief          TEXT,               -- 匿名人選重點（不含姓名、不含目前任職公司），一行一點
  error          TEXT,
  ai_job_id      TEXT,
  created_at     TEXT NOT NULL,
  done_at        TEXT
);
CREATE TABLE IF NOT EXISTS cand_bd_targets (
  id          TEXT PRIMARY KEY,
  run_id      TEXT NOT NULL,
  company     TEXT NOT NULL,
  angle       TEXT,                  -- 同業／在徵類似職缺／擴編展店／其他
  why         TEXT,                  -- 為什麼想到這家（給顧問看）
  job_hint    TEXT,                  -- 可能對應的職缺
  existing    TEXT,                  -- client／bd（已經是客戶或已在開發進度）
  status      TEXT NOT NULL,         -- new / picked / dropped
  decided_by  TEXT,
  decided_at  TEXT,
  outreach_id TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cbt_run ON cand_bd_targets (run_id);
