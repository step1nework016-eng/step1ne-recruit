-- 2026-10-21 阿財快速回覆按鈕（acai_quick_replies.py；save() 也會 CREATE IF NOT EXISTS，這份是正式紀錄）
-- 按鈕不寫進 messages 本文：逐字稿、報告、阿財自己讀的對話都看不到。開關：site_settings key='acai_quick_replies'，值 off／test／on。
CREATE TABLE IF NOT EXISTS acai_quick_replies (
  application_id TEXT NOT NULL,
  after_id       INTEGER NOT NULL,   -- 寫入阿財這輪訊息之前的最後一則 messages.id；之後有人選訊息＝按鈕失效
  options_json   TEXT NOT NULL,      -- ["可以","要看交通","不太方便"]
  created_at     TEXT NOT NULL,
  PRIMARY KEY (application_id, after_id)
);
INSERT INTO site_settings (key, value, updated_at) VALUES ('acai_quick_replies', 'test', datetime('now','+8 hours'))
  ON CONFLICT(key) DO NOTHING;
