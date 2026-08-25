-- 用人需求表 portal 第二版（2026-08-25）
--
-- 三個改動的資料庫底層：
-- 1. 新增職缺不再「先建立空殼進 pending_review」，改成 client_draft
--    （客戶自己填、隨時可編輯、不進顧問審核清單），填完按「送出審核」
--    才變 pending_review（鎖住不能再改，等顧問核准/拒絕）。
--    jobs.status 本來就是自由字串，不用改欄位型別，這裡只是新增一個
--    合法值，程式碼那邊會處理。
--
-- 2. 一鍵匯入：跟現有 job_intakes（顧問自助新增職缺收件）不是同一件事——
--    job_intakes 的 draft_job.py 產的是「對外行銷 JD」，這裡要的是
--    「把客戶原始檔案的內容對應進 51 個結構化欄位」，兩者輸出格式完全
--    不同，所以另開一張表，不要混進 job_intakes。
--    處理方式比照 job_intakes：Worker 只負責收件跟存檔，本機排程
--    （portal_import_tick.py）呼叫 Claude Code CLI 做解析，寫回
--    parsed_json，前端再讓客戶確認/編輯後才真的存進 jobs 表。
CREATE TABLE IF NOT EXISTS portal_imports (
  id            TEXT PRIMARY KEY,
  job_slug      TEXT NOT NULL,          -- 對應 jobs.slug，匯入結果最後要回填到哪一筆
  company_id    TEXT NOT NULL,          -- 對應 client_companies.id，供權限比對
  source_type   TEXT NOT NULL,          -- file / url / text
  source_ref    TEXT,                   -- file_id（多檔用逗號分隔）或 url 或原始文字
  status        TEXT NOT NULL DEFAULT 'pending',
                -- pending   剛收到，等本機排程處理
                -- parsing   本機排程正在處理
                -- parsed    解析完成，等客戶確認
                -- applied   客戶確認後已寫回 jobs 表
                -- failed    解析失敗（例如讀不到內容），error_note 說明原因
  parsed_json   TEXT,                   -- 解析出的 51 欄位 JSON（PORTAL_FIELDS 子集）
  error_note    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_portal_imports_status ON portal_imports(status);
CREATE INDEX IF NOT EXISTS idx_portal_imports_job     ON portal_imports(job_slug);

-- 匯入來源檔案跟 job_intake_files 同樣的做法，走 files/file_chunks，
-- 這張只記關聯，一筆匯入可以有多個檔（例如 JD 本文 + 薪資表兩份檔案）。
CREATE TABLE IF NOT EXISTS portal_import_files (
  import_id   TEXT NOT NULL,
  file_id     TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (import_id, file_id)
);
