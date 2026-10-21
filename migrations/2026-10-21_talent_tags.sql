-- 2026-10-21 人才標籤＋自動推薦
-- jobs（100 欄已滿）／applications（95 欄）不再加欄位，一律開新表。
-- recommendation_service.ensure_tag_tables() 也會 CREATE IF NOT EXISTS，這份是正式紀錄。
-- 套用：npx wrangler d1 execute step1ne-recruit --remote --file migrations/2026-10-21_talent_tags.sql

CREATE TABLE IF NOT EXISTS candidate_tags (
  application_id TEXT NOT NULL,
  tag            TEXT NOT NULL,      -- 固定類別（recommendation_service.TAG_TAXONOMY）
  level          TEXT NOT NULL,      -- 新手可培訓／有經驗／資深主管
  confidence     TEXT,               -- high／medium／low
  region         TEXT,               -- 願意上班的地區分桶，用「、」串（北北基宜、桃竹苗…）
  region_strict  INTEGER DEFAULT 0,  -- 1＝人選明確說其他地區不考慮（才會拿來篩）
  salary_monthly INTEGER,            -- 期望月薪（台幣）
  evidence       TEXT,               -- AI 判斷依據（一句，已遮掉年齡性別等字眼）
  report_id      TEXT,               -- 依據哪一份阿財報告
  source         TEXT,               -- ai_interview／ai_backfill
  prompt_version TEXT,
  created_at     TEXT NOT NULL,
  PRIMARY KEY (application_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_candidate_tags_tag ON candidate_tags(tag);

CREATE TABLE IF NOT EXISTS job_tags (
  job_slug       TEXT NOT NULL,
  tag            TEXT NOT NULL,
  level          TEXT NOT NULL,
  job_updated_at TEXT,               -- 打標籤當下職缺的 updated_at；職缺之後改過就會重打
  source         TEXT,
  prompt_version TEXT,
  created_at     TEXT NOT NULL,
  PRIMARY KEY (job_slug, tag)
);
CREATE INDEX IF NOT EXISTS idx_job_tags_tag ON job_tags(tag);
