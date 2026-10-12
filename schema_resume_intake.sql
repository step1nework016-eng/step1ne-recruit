-- 2026-10-12 應徵表單「履歷三條路」：LINE 傳履歷用的短代碼，以及應徵被擋下的原因紀錄。
-- 為什麼另開表、不加在 applications：applications 已經 93 欄（上限 100），
-- 而且代碼在「應徵紀錄還沒建立」時就要存在（候選人可能先去 LINE 傳檔，才回來按開始測驗）。

-- 一組代碼 = 一次應徵的 LINE 履歷收件單。
--   application_id 可以是 NULL：表單還沒送出時就先發代碼，送出 /apply 時才接上。
--   line_user_id：候選人在 LINE 送出「我要傳履歷 #XXXXX」那一刻記下；之後他傳的檔案/照片就歸這組。
--   file_id：第一份收到的檔案（之後的補充頁放進 applications.resume_extra_files）。
CREATE TABLE IF NOT EXISTS resume_intake_codes (
  code             TEXT PRIMARY KEY,
  application_id   TEXT,
  created_at       TEXT NOT NULL,
  expires_at       TEXT NOT NULL,
  line_user_id     TEXT,
  line_bound_at    TEXT,
  file_id          TEXT,
  file_received_at TEXT,
  extra_count      INTEGER NOT NULL DEFAULT 0,
  extra_files      TEXT              -- JSON 陣列：代碼還沒接上應徵時收到的第 2 份以後，接上時併進 applications.resume_extra_files
);
CREATE INDEX IF NOT EXISTS idx_ric_app ON resume_intake_codes(application_id);
CREATE INDEX IF NOT EXISTS idx_ric_line ON resume_intake_codes(line_user_id);

-- 應徵表單被後端擋下的原因（不存姓名/信箱/電話，只記發生了什麼）。
CREATE TABLE IF NOT EXISTS apply_block_log (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  endpoint   TEXT NOT NULL,   -- apply / apply_update / chat_gate / resume_fix
  reason     TEXT NOT NULL,   -- 原因代碼，例如 no_resume / bad_email / job_closed
  job_slug   TEXT,
  ua_kind    TEXT             -- line_inapp / ig_inapp / fb_inapp / threads_inapp / mobile / desktop
);
CREATE INDEX IF NOT EXISTS idx_abl_created ON apply_block_log(created_at);
