-- 記錄每份報告是用哪一版職缺卡評的。總指揮交辦（Jacky已核准），2026-09-24。
--
-- 用途：職缺卡要能對照「匯入回饋前 vs 匯入回饋後」的 A 級命中率，證明
-- 匯入回饋有沒有真的讓阿財判斷得更準——沒有這個版本號，事後完全無法
-- 回推「這份報告當時讀到的職缺卡是第幾次回饋之後的版本」。
--
-- 值用 job_card_profile.feedback_count（已經匯入過幾筆回饋）當版本號，
-- 不用時間戳——整數版本號直接拿來分「之前／之後」兩組比較，比時間戳
-- 好算，而且跟 job_card_profile 現有欄位同源，不用另外定義一套版本概念。
--
-- 只新增欄位，不動任何既有欄位或資料。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=migrations/2026-09-24_reports_job_card_version.sql

ALTER TABLE reports ADD COLUMN job_card_version INTEGER;
