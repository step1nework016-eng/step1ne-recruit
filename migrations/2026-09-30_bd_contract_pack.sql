-- 2026-09-30 客戶開發卡片「寄公司介紹＋合約」：只新增表，不動既有欄位
CREATE TABLE IF NOT EXISTS bd_contract_files (
  id          TEXT PRIMARY KEY,
  company     TEXT NOT NULL,          -- 這份合約是哪家客戶的
  file_id     TEXT NOT NULL,          -- files.id（實體檔在 R2）
  filename    TEXT,
  mime        TEXT,
  size        INTEGER,
  uploaded_by TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bd_contract_files_company ON bd_contract_files(company);
-- 一封信帶哪些附件（bd_outreach.cv_file_id 只放得下一個）
CREATE TABLE IF NOT EXISTS bd_mail_attachments (
  id          TEXT PRIMARY KEY,
  outreach_id TEXT NOT NULL,
  file_id     TEXT NOT NULL,
  kind        TEXT,                   -- company_intro / contract
  filename    TEXT,
  sort        INTEGER DEFAULT 0,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bd_mail_attachments_outreach ON bd_mail_attachments(outreach_id);
