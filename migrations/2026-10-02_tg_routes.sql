-- 2026-10-02 Jacky：TG 拆成社群／人選／客戶三個群組。通知要發去哪個群組、哪個主題，改成查這張表，
-- 兩台電腦（Mac／WSL2）跟各個 Worker 都讀同一份，不用各自改設定檔。沒有這筆就照舊發原本的大群組。
CREATE TABLE IF NOT EXISTS tg_routes (
  key        TEXT PRIMARY KEY,   -- social（社群群組本身）、social_shared、social_article、social_topics …
  chat_id    TEXT NOT NULL,
  thread_id  INTEGER,
  note       TEXT,
  updated_at TEXT
);
-- 已經發出去的審核訊息在哪個群組（切換前發的在舊群組，按鈕／排程發文要對回原本那則）
ALTER TABLE social_post_queue ADD COLUMN tg_chat_id TEXT;
