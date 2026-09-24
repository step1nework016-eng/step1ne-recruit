-- 「匯入回饋前 vs 後」的 A 級命中率對照。總指揮交辦（Jacky已核准），2026-09-24。
-- 只加不改。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=migrations/2026-09-24_job_card_before_after.sql

ALTER TABLE job_card_profile ADD COLUMN a_before_n INTEGER NOT NULL DEFAULT 0;
ALTER TABLE job_card_profile ADD COLUMN a_before_hire_rate REAL;
ALTER TABLE job_card_profile ADD COLUMN a_after_n INTEGER NOT NULL DEFAULT 0;
ALTER TABLE job_card_profile ADD COLUMN a_after_hire_rate REAL;
