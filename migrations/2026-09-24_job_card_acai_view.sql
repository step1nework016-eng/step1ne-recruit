-- 「阿財的理解」卡片快照。Jacky 交辦，2026-09-24。
--
-- 顧問要確認「阿財理解得對不對」，卡片內容必須是阿財面談前實際讀到的東西
-- （interview_daemon.py 的 fetch_job_understanding_sources()/job_understanding()，
-- 跟面談用同一支函式撈資料），不是另外叫 AI 生一份摘要——所以這裡存的是
-- job_card.py 呼叫那支函式產出的結構化 JSON，不是自由文字摘要。
--
-- 只在 job_card_profile 加欄位，不開新表——這份快照本質上是職缺卡的一部分
-- （職缺／題庫／職缺卡本身任一項變動都要重算），沒有獨立的生命週期。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=migrations/2026-09-24_job_card_acai_view.sql

ALTER TABLE job_card_profile ADD COLUMN acai_view_json TEXT;
ALTER TABLE job_card_profile ADD COLUMN acai_view_updated_at TEXT;
