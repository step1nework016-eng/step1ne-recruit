-- 漏斗加「報到」欄位（錄取之後的下一關）。總指揮交辦，2026-09-24。
-- 只加不改。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=migrations/2026-09-24_job_card_onboarded.sql

ALTER TABLE job_card_profile ADD COLUMN onboarded_n INTEGER NOT NULL DEFAULT 0;
