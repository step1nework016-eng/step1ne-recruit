-- 2026-09-15 加：把 AI 找人才功能打包給多顧問用，需要兩件事：
--
-- 1) jobs.owner —— 讓職缺可以明確指定「歸哪位顧問負責」（consultants.id）。
--    查證過：jobs 表原本完全沒有這個欄位，顧問歸屬目前只能從
--    applications.owner 反推「這個職缺主要是誰在跑」，但反推覆蓋率很低
--    （28 個開放職缺裡只有 8 個能反推出來，18 個完全查無歸屬）。
--    加這個欄位是為了讓以後可以直接指定，不必每次都用推的。
--    先不補值（不用猜），維持 NULL，交給顧問之後在後台指定。
--
-- 2) talent_sourcing_requests —— 顧問在 Telegram 打字觸發「幫我找這個職缺的人才」
--    要排隊的地方。跟 ai_jobs 用同一套「D1 佇列 + 本機常駐 daemon 認領」的模式，
--    但刻意不共用 ai_jobs 那張表：ai_jobs 的 worker（ai_worker.py）是設計給
--    「8 分鐘內、claude -p 關掉 Bash/WebSearch 等工具」的輕量任務用的
--    （call_notes_summary、call_prep 這類文字整理），talent_sourcing_agent.py
--    是完全不同量級的任務（15-40 分鐘、需要開 WebSearch/WebFetch 工具做真的搜尋）。
--    混進同一個佇列會被 ai_worker.py 的 480 秒逾時砍斷、也會被 NO_TOOLS 擋死，
--    所以另開一支專用的 daemon（talent_sourcing_tg_worker.py）認領這張表，
--    跟 interview_daemon.py／checkup_daemon.py／ai_worker.py 各自顧自己那張表
--    是同一種既有架構，不是另外發明一套新機制。

ALTER TABLE jobs ADD COLUMN owner TEXT;

CREATE TABLE IF NOT EXISTS talent_sourcing_requests (
  id                TEXT PRIMARY KEY,
  job_slug          TEXT NOT NULL,
  requested_by      TEXT,        -- TG 顯示名稱（username 或 first_name），純記錄用
  chat_id           TEXT NOT NULL,
  thread_id         INTEGER,     -- 完成後要回覆到哪個 TG topic
  status            TEXT NOT NULL DEFAULT 'pending',   -- pending/running/done/failed
  attempts          INTEGER NOT NULL DEFAULT 0,
  worker_id         TEXT,
  candidates_found  INTEGER,
  candidates_saved  INTEGER,
  error             TEXT,
  created_at        TEXT NOT NULL,
  started_at        TEXT,
  done_at           TEXT
);
