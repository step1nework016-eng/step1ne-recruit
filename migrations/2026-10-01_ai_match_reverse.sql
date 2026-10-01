-- 2026-10-01 AI 配對（反向配對＋AI 配對頁＋寄信邀請）。只加不刪。
-- 推薦紀錄多記：從哪條路來、哪一次反向配對、誰發的 LINE、Email 邀請狀態
ALTER TABLE candidate_job_recommendations ADD COLUMN match_source TEXT;      -- NULL/post_interview＝面談後找替代職缺；reverse_job_open＝職缺開出來回頭找人才庫
ALTER TABLE candidate_job_recommendations ADD COLUMN reverse_run_id TEXT;
ALTER TABLE candidate_job_recommendations ADD COLUMN outreach_sent_by TEXT;  -- 誰按了發 LINE
ALTER TABLE candidate_job_recommendations ADD COLUMN email_status TEXT;      -- sending / sent / failed
ALTER TABLE candidate_job_recommendations ADD COLUMN email_sent_at TEXT;
ALTER TABLE candidate_job_recommendations ADD COLUMN email_sent_by TEXT;
ALTER TABLE candidate_job_recommendations ADD COLUMN email_to TEXT;
ALTER TABLE candidate_job_recommendations ADD COLUMN email_message_id TEXT;
ALTER TABLE candidate_job_recommendations ADD COLUMN email_error TEXT;

-- 每個職缺「開出來」那一次的反向配對紀錄。id = job_slug|episode，
-- 兩台機器（Mac／WSL2）同時想排同一個職缺時，PRIMARY KEY 只會讓一台成功。
CREATE TABLE IF NOT EXISTS job_reverse_match_runs (
  id            TEXT PRIMARY KEY,
  job_slug      TEXT NOT NULL,
  episode       TEXT,          -- initial／reopen:<closed_at>／manual:<時間>
  mode          TEXT NOT NULL, -- auto／manual／baseline（上線當下已開的職缺，只記錄不跑）
  status        TEXT NOT NULL, -- queued／done／skipped／failed
  requested_by  TEXT,
  ai_job_id     TEXT,
  pool_size     INTEGER,
  ai_suggested  INTEGER,
  saved         INTEGER,
  result_json   TEXT,
  created_at    TEXT NOT NULL,
  done_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_jrmr_job ON job_reverse_match_runs(job_slug);

-- 給人選本人看的「為什麼想到您」一句話（reasons 是寫給顧問看的評估，例如「目前待業中…」，不能直接引用給人選）
ALTER TABLE candidate_job_recommendations ADD COLUMN candidate_why TEXT;
