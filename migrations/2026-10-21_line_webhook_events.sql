-- 2026-10-21 LINE webhook 去重（src/line_dedupe.js；webhook 處理時也會 CREATE IF NOT EXISTS，這份是正式紀錄）
CREATE TABLE IF NOT EXISTS line_webhook_events (
  event_id      TEXT PRIMARY KEY,
  line_user_id  TEXT,
  event_type    TEXT,
  is_redelivery INTEGER DEFAULT 0,
  status        TEXT NOT NULL,          -- processing／done／failed
  attempts      INTEGER NOT NULL DEFAULT 1,
  reply_sent    INTEGER NOT NULL DEFAULT 0,  -- 回覆或補送推播有一則成功就是 1
  redeliveries  INTEGER NOT NULL DEFAULT 0,  -- 之後又收到幾次重送（被擋掉的）
  first_seen_at TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
