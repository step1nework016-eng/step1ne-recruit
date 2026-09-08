-- 通用設定表（2026-09-07，部署設定介面第一版）
--
-- 目的：把寫死指向「Jacky本人」的幾個值（GitHub發布帳號、Telegram話題ID）
-- 變成可以在後台設定頁改的值，不用改程式碼、不用重新部署。
-- key/value 純附加式，讀不到某個key就照舊用程式碼裡的預設值，不影響現況。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=schema_site_settings.sql

CREATE TABLE IF NOT EXISTS site_settings (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TEXT
);
