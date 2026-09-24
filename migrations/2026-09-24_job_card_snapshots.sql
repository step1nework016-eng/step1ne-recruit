-- 每週記一次各職缺的命中率快照，累積 3 筆以上才畫趨勢線。總指揮交辦
-- （Jacky 已核准），2026-09-24。
--
-- 跟每週一 09:00 的 TG 週報同一個 Cloudflare Worker cron 寫入（見
-- step1ne-backoffice-worker/src/index.js 的 scheduled()）——不是本機腳本
-- 寫的，因為要寫入的資料（job_card_profile 的當週數字）本來就已經在 D1
-- 裡算好了，Worker 直接讀、直接寫，不用再繞回本機 Python。
--
-- 只新增表，不動任何既有表。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=migrations/2026-09-24_job_card_snapshots.sql

CREATE TABLE IF NOT EXISTS job_card_snapshots (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_date  TEXT NOT NULL,               -- YYYY-MM-DD，週報跑的那天（週一）
  job_slug       TEXT NOT NULL,
  interviewed_n  INTEGER NOT NULL DEFAULT 0,  -- 阿財面談過幾人
  submitted_n    INTEGER NOT NULL DEFAULT 0,  -- 送客戶幾人
  advanced_n     INTEGER NOT NULL DEFAULT 0,  -- 客戶面試幾人
  hired_n        INTEGER NOT NULL DEFAULT 0,  -- 錄取方向幾人
  settled_n      INTEGER NOT NULL DEFAULT 0,  -- 有明確結果幾筆
  accuracy_rate  REAL,                        -- 命中率，settled_n=0 時為 NULL（不是 0）
  created_at     TEXT NOT NULL DEFAULT (datetime('now','+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_job_card_snapshots_slug_date ON job_card_snapshots(job_slug, snapshot_date);
