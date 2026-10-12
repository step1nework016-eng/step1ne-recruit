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

-- 2026-10-12 第二版：截圖／照片履歷「看得清楚嗎」的檢查結果（一筆應徵一列）。
-- 由 parse_resumes.py 在 OCR 完寫入；應徵表單／補履歷頁（GET /apply/resume-check/:token）
-- 與 LINE 收件（step1ne-recruit）讀它決定要不要請候選人「再截一次圖」。
--   state：pending（等 OCR）／ok／unreadable（第一次字太少，已請他重截）／accepted_low（第二次還是太少，收下並通知顧問）
--   tries：字太少的次數（同一組檔案只算一次）；file_id：被判定的那一組的第 1 張（resume_file_id）
CREATE TABLE IF NOT EXISTS resume_read_checks (
  application_id TEXT PRIMARY KEY,
  state          TEXT NOT NULL DEFAULT 'pending',
  tries          INTEGER NOT NULL DEFAULT 0,
  file_id        TEXT,
  pages          INTEGER,
  chars          INTEGER,
  cjk            INTEGER,
  source         TEXT,              -- line / web
  updated_at     TEXT,
  line_notified_at TEXT,
  flagged_at     TEXT
);
