-- 2026-09-02：人選客製表單功能（起點：弘昌 BIM 工程師「用人事資料表」，
-- 做成通用機制，之後其他客戶/職缺要收類似表單直接沿用）。
--
-- job_forms：表單範本，掛在職缺底下。一個職缺可以掛多份表單。
CREATE TABLE IF NOT EXISTS job_forms (
  id TEXT PRIMARY KEY,
  job_slug TEXT NOT NULL,
  name TEXT NOT NULL,
  template_file_id TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_forms_slug ON job_forms(job_slug);

-- candidate_form_submissions：每一位人選、推薦給某家客戶時觸發的表單填寫紀錄。
-- 以 company_id 為單位（不是整個人選共用一份）——每家客戶要收的資料本身不同，
-- 也只有這個職缺對這家客戶真的有掛表單時才會生一筆。
CREATE TABLE IF NOT EXISTS candidate_form_submissions (
  id TEXT PRIMARY KEY,
  job_form_id TEXT NOT NULL,
  application_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  token TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending / sent / submitted
  sent_at TEXT,
  submitted_at TEXT,
  submitted_file_id TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cfs_token ON candidate_form_submissions(token);
CREATE INDEX IF NOT EXISTS idx_cfs_app ON candidate_form_submissions(application_id);
CREATE INDEX IF NOT EXISTS idx_cfs_app_company ON candidate_form_submissions(application_id, company_id);
