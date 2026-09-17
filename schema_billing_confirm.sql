-- 2026-09-15 加：收費進程從「顧問自己標記」升級成「客戶上傳匯款證明→顧問確認」的完整流程。
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=schema_billing_confirm.sql

ALTER TABLE placement_billing_installments ADD COLUMN client_submitted_at TEXT;   -- 客戶按下「確認送出」的時間
ALTER TABLE placement_billing_installments ADD COLUMN client_transfer_date TEXT; -- 客戶自己選的匯款日期
ALTER TABLE placement_billing_installments ADD COLUMN proof_file_id TEXT;        -- 匯款證明檔案，files 表
ALTER TABLE placement_billing_installments ADD COLUMN confirmed_received TEXT;   -- 'yes' | 'no' | NULL＝顧問還沒確認
ALTER TABLE placement_billing_installments ADD COLUMN confirmed_at TEXT;
ALTER TABLE placement_billing_installments ADD COLUMN confirmed_by TEXT;
