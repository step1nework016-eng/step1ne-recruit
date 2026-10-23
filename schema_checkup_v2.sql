-- AI 履歷健檢（阿福）── 申請表第二版欄位　2026-10-23（測試版 /checkup-beta/ 先用）
--
-- 新增「想找什麼工作／期望地點／期望薪資／學歷」四格（點選、下拉），讓阿福對談與報告讀得到，
-- 報告最後的「跟你方向接近的職缺」也拿這四格去比對。
-- 只在 checkups 加欄，不動 jobs（100 欄已滿）、不動 applications。
-- form_version：'v2'＝從新版表單進來（報告才加職缺段、阿福才給快速回覆按鈕）；舊表單一律 NULL，行為不變。
--
-- 執行：
--    set -a; . ~/.config/workflow-os/cf.env; set +a
--    npx --yes wrangler d1 execute step1ne-recruit --remote --file=schema_checkup_v2.sql

ALTER TABLE checkups ADD COLUMN want_job_types TEXT;   -- 想找什麼工作（多選，用「、」串）
ALTER TABLE checkups ADD COLUMN want_locations TEXT;   -- 期望地點（多選，用「、」串）
ALTER TABLE checkups ADD COLUMN expected_salary TEXT;  -- 期望薪資（下拉選項原文，例：月薪 4～5 萬）
ALTER TABLE checkups ADD COLUMN education_level TEXT;  -- 最高學歷（下拉選項原文）
ALTER TABLE checkups ADD COLUMN form_version TEXT;     -- 'v2'＝新版表單
ALTER TABLE checkups ADD COLUMN job_recs_json TEXT;    -- 報告最後推薦的職缺（給顧問查，含比對依據）

-- 阿福的快速回覆按鈕（比照阿財 acai_quick_replies，但分開一張表：阿福的資料不混進阿財那邊）
CREATE TABLE IF NOT EXISTS afu_quick_replies (
  checkup_id   TEXT NOT NULL,
  after_id     INTEGER NOT NULL,     -- 寫入阿福這輪訊息之前的最後一則 checkup_messages.id
  options_json TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  PRIMARY KEY (checkup_id, after_id)
);

-- 開關：off／test／on。test＝新版表單（form_version='v2'）或姓名含「測試」才給按鈕。
INSERT OR IGNORE INTO site_settings (key, value) VALUES ('afu_quick_replies', 'test');
