-- 職缺卡卡片牆要在不點開的情況下直接看到「客戶面試」「錄取」兩個數字。
-- 總指揮交辦，2026-09-24。只新增欄位，不動任何既有欄位。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=migrations/2026-09-24_job_card_wall_counts.sql

ALTER TABLE job_card_profile ADD COLUMN client_interviewed_n INTEGER NOT NULL DEFAULT 0;  -- 送客戶後真的進到客戶端面試的人數（不含我方結案/失聯）
ALTER TABLE job_card_profile ADD COLUMN hired_n INTEGER NOT NULL DEFAULT 0;               -- 錄取方向的結果人數（OFFER 起算，見 _HIRE_DIRECTION_STAGES）
