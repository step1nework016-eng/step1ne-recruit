-- STEP1NE 招募形式評估工具（enterprise.step1ne.com）— D1 結構
--
-- 這些表建在共用的正式 D1（step1ne-recruit / 67077b4b-42d9-4086-8d04-b3658a89cffd）。
-- 同一個庫裡還住著面談系統（applications／assessments／reports…）、
-- 人選配對（candidate_job_match）、主動開發（sourcing_runs…）。
--
-- 🚨 因此一律加 hm_ 前綴（hiring mode）。
--    原始草稿用的是 organizations／assessment_cases／internal_todos／audit_logs
--    這類通名，在 53 張既有表的庫裡遲早撞名，而且撞了也看不出是誰的表。
--    建表前已用 sqlite_master 查證過 hm_ 開頭的物件一個都不存在。
--
-- 全部 CREATE TABLE IF NOT EXISTS，只新增，不改動任何既有表的欄位。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=schema_hiring_mode.sql

-- 案件主表。
-- payload 存整包案件 JSON（案件是一個整體，整包存最不會出現半套狀態）；
-- 其餘欄位是拆出來給 CRM／報表直接下 SQL 用的，不必解 JSON。
CREATE TABLE IF NOT EXISTS hm_cases (
  id                TEXT PRIMARY KEY,
  organization_id   TEXT,
  title             TEXT NOT NULL,
  status            TEXT NOT NULL,   -- draft/extracting/needs_review/needs_clarification/
                                     -- ready_for_assessment/consultant_review/approved/exported
  audience          TEXT NOT NULL DEFAULT 'internal',  -- internal / client（客戶自助版預留）
  primary_mode      TEXT,            -- 評估完才有：full_time/project_contract/dispatch/…
  lead_email        TEXT,            -- 主網域入口頁帶進來的 Email（CRM 潛在客戶名單）
  lead_contact_name TEXT,
  lead_phone        TEXT,
  utm_source        TEXT,
  utm_medium        TEXT,
  utm_campaign      TEXT,
  referrer          TEXT,
  lead_origin       TEXT NOT NULL DEFAULT 'consultant', -- consultant / inbound
  payload           TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hm_cases_status     ON hm_cases(status);
CREATE INDEX IF NOT EXISTS idx_hm_cases_updated    ON hm_cases(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_hm_cases_lead_email ON hm_cases(lead_email);
CREATE INDEX IF NOT EXISTS idx_hm_cases_origin     ON hm_cases(lead_origin);

-- 內部待辦。兩個來源：
-- (1) 顧問覆核完由 buildTodos() 產出的「下次要問什麼」；
-- (2) 網址帶 email/utm 進來時自動建的一條「CRM 建檔」——
--     這是兩層架構（主網域留 email → 導進這個工具）的落地點，
--     沒有它，留下來的 Email 就只是躺在案件裡沒有人去跟。
CREATE TABLE IF NOT EXISTS hm_todos (
  id            TEXT PRIMARY KEY,
  case_id       TEXT NOT NULL,
  kind          TEXT NOT NULL DEFAULT 'case',   -- case / crm_intake
  title         TEXT NOT NULL,
  detail        TEXT NOT NULL,
  owner         TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'open',   -- open / doing / done
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hm_todos_case   ON hm_todos(case_id);
CREATE INDEX IF NOT EXISTS idx_hm_todos_status ON hm_todos(status);

-- 稽核軌跡。案件 payload 裡本來就帶著 auditLogs，這裡另存一份攤平的，
-- 是為了「不解 JSON 也查得到誰在什麼時候改了什麼」。
CREATE TABLE IF NOT EXISTS hm_audit_logs (
  id            TEXT PRIMARY KEY,
  case_id       TEXT NOT NULL,
  action        TEXT NOT NULL,
  actor         TEXT NOT NULL,
  before_json   TEXT,
  after_json    TEXT,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hm_audit_case ON hm_audit_logs(case_id);
