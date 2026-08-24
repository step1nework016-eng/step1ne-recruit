-- 用人需求表統一流程 — 第二批欄位（比對第二份真實範本「弘昌_無派遣用語版」後新增）
--
-- 第一批（schema_hiring_requirements.sql）比對「律准BIM工程師案」範本時新增了24個欄位。
-- 這次比對弘昌範本，大部分都能重複利用第一批欄位或既有欄位（含意外發現的既有欄位
-- onboarding_prep_note），只有下面 8 個是真的沒有對應、需要新增的。
--
-- 全部 ALTER TABLE ADD COLUMN，附加式，不動任何既有資料。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=schema_hiring_requirements_2.sql

ALTER TABLE jobs ADD COLUMN dispatch_client TEXT;         -- 實際要派單位／進駐專案（跟 client_name 不同：一個是簽約對象，一個是人實際上班的地方）
ALTER TABLE jobs ADD COLUMN department TEXT;               -- 所屬部門／團隊名稱
ALTER TABLE jobs ADD COLUMN work_environment_ratio TEXT;   -- 工作環境現場比重（例：辦公室8成/現場2成）
ALTER TABLE jobs ADD COLUMN attendance_method TEXT;        -- 出勤紀錄方式
ALTER TABLE jobs ADD COLUMN interview_process TEXT;        -- 面試流程與關卡
ALTER TABLE jobs ADD COLUMN dispatch_range TEXT;           -- 專案調派範圍（跟主要地點 locations 不同，是可能被調去的其他地點）
ALTER TABLE jobs ADD COLUMN contract_terms_note TEXT;      -- 保密／競業／最低服務年限／離職預告期
ALTER TABLE jobs ADD COLUMN overtime_detail TEXT;          -- JSON：加班細項數字（平日/假日/旺季時數、計算基數、上限），比照 salary_tier_table 的做法
