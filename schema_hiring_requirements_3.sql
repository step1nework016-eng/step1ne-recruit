-- 用人需求表統一流程 — 第三批欄位（2026-09-07，顧問反映用人需求表預覽卡片
-- 沒有完整呈現HR填的內容＋想加「這個需求怎麼來的」補充說明）
--
-- hiring_reason（招募原因）目前只有4個固定選項（新增職缺/離職遞補/擴編/專案需求），
-- 沒有配對的自由文字補充欄位——比照 salary_note 配 salary_min/max 的做法，
-- 加一個純文字欄位讓顧問補充細節（例如選了「專案需求」，寫是哪個專案）。
--
-- ALTER TABLE ADD COLUMN，附加式，不動任何既有資料。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=schema_hiring_requirements_3.sql

ALTER TABLE jobs ADD COLUMN hiring_reason_note TEXT;   -- 招募原因補充（自由文字，搭配 hiring_reason 的固定選項用）
