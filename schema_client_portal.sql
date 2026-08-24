-- 用人需求表 One-Page 企業客戶入口 — 資料模型
--
-- 一家企業客戶可能對到多個 jobs（多個職缺），「專屬長期網址」綁在公司層級，
-- 不是綁在單一職缺，所以需要一個公司實體來掛 portal_token。
--
-- portal_token 不設過期，比照 applications.chat_token 現有的 bearer-link 慣例
-- （拿到連結就能填，沒有另外的密碼）；要收回存取權就在後台重新產生 token。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=schema_client_portal.sql

CREATE TABLE IF NOT EXISTS client_companies (
  id            TEXT PRIMARY KEY,     -- slug，顧問建立時手動給
  display_name  TEXT NOT NULL,        -- 對外顯示的公司名稱
  contact_email TEXT,                 -- 補件連結寄送的信箱
  portal_token  TEXT UNIQUE NOT NULL, -- 32 bytes hex
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

ALTER TABLE jobs ADD COLUMN company_id TEXT; -- 對應 client_companies.id，NULL＝還沒掛上公司入口
