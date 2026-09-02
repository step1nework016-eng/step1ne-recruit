-- 2026-09-02：客戶版履歷改成存檔，不再每次都要重新生成。
-- 一位人選對每一家客戶各自存一份最新版（重新產生就地覆蓋，不留歷史版本
-- ——這份文件本來就是「目前最新狀態」，不是存證用途）。
CREATE TABLE IF NOT EXISTS client_formal_reports (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,              -- 'application' 或 'sourced'
  source_id TEXT NOT NULL,         -- application_id 或 sourced_candidates.id
  company_id TEXT NOT NULL,
  html TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  generated_by TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cfr_unique ON client_formal_reports(kind, source_id, company_id);
