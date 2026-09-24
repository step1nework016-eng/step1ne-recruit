-- 既有客戶擴單提醒：已簽約客戶太久沒有新職缺，要有人記得回頭問一句。
--
-- ⚠️ 2026-09-24 建這張表之前先踩到一個雷：想拿 jobs.updated_at 當「最近一次
-- 有新職缺」的依據，結果發現 2026-09-21 21:19-21:22 之間有 32 筆 jobs 的
-- updated_at 被同一支批次腳本在 2-3 秒間隔內逐筆改過（backfill 之類的欄位補值），
-- 那不是「客戶真的來了新職缺」，是資料庫維護動作。照著它判斷會整批誤判成
-- 「客戶最近才有新職缺」。改用 portal_imports.created_at（客戶自己送單的時間）
-- 跟 job_intakes.created_at（顧問建單的時間）這兩個「真正的動作時間」當依據，
-- 這兩張表沒有被那次批次動作碰過。
--
-- 代價：目前只有 3/11 簽約客戶在這兩張表裡查得到真正的日期，
-- 其餘 8 家只知道「系統裡有沒有職缺」，不知道正確的最後日期——
-- confidence 欄位就是記這件事，reliable 才能拿日期去跟 60 天比，
-- estimated/unknown 只能先用「有沒有職缺」判斷，不能說「幾天沒新職缺」。
CREATE TABLE IF NOT EXISTS bd_expansion_watch (
  client_id       TEXT PRIMARY KEY,   -- 對應 client_companies.id
  client_name     TEXT NOT NULL,
  last_job_at     TEXT,               -- 目前查得到最可信的「最後一次有新職缺」時間，查不到留 null
  last_job_source TEXT,               -- portal_imports / job_intakes / jobs_table_only / none
  confidence      TEXT NOT NULL,      -- reliable / estimated / unknown
  status          TEXT NOT NULL DEFAULT 'ok',  -- ok / due / reminded
  last_reminded_at TEXT,
  note            TEXT,
  updated_at      TEXT NOT NULL
);
