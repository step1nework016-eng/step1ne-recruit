-- 職缺卡加「面談過幾人、送幾人給客戶、有幾筆結果」。總指揮交辦，2026-09-24。
--
-- Jacky 要求卡片上看得到量級，不是只有比例——命中率 83% 聽起來一樣，
-- 但「6 筆裡 5 筆有結果」跟「100 筆裡 5 筆有結果」信任度完全不同。
--
-- 只新增欄位，不動任何既有欄位。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=migrations/2026-09-24_job_card_counts.sql

ALTER TABLE job_card_profile ADD COLUMN interviewed_n INTEGER NOT NULL DEFAULT 0;   -- 阿財面談過幾人（有 reports 的 applications）
ALTER TABLE job_card_profile ADD COLUMN submitted_n INTEGER NOT NULL DEFAULT 0;     -- 送客戶幾人（placements 筆數）
ALTER TABLE job_card_profile ADD COLUMN settled_n INTEGER NOT NULL DEFAULT 0;       -- 有明確結果幾筆（錄取方向或客戶婉拒，不分等第）
ALTER TABLE job_card_profile ADD COLUMN excluded_n INTEGER NOT NULL DEFAULT 0;      -- 我方結案／失聯，不計入命中率分母的筆數
