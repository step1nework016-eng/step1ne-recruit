-- 2026-10-02 Jacky：沒綁 LINE 的人選，要能直接寄信約電話時間（人選中心「寄信約電話」）。
-- 寄出一律顧問按；人選回信由 recruit worker 的 email.received 對回來，寫 reply_at/reply_body 並推 TG。
CREATE TABLE IF NOT EXISTS call_booking_mails (
  id             TEXT PRIMARY KEY,
  application_id TEXT NOT NULL,
  to_email       TEXT NOT NULL,
  subject        TEXT,
  body           TEXT,
  note           TEXT,                  -- 顧問加的一句話（會出現在信裡）
  sent_by        TEXT,
  sent_at        TEXT,
  status         TEXT NOT NULL,         -- sent / failed / test
  resend_id      TEXT,
  error          TEXT,
  reply_at       TEXT,
  reply_body     TEXT,
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cbm_app ON call_booking_mails (application_id);
CREATE INDEX IF NOT EXISTS idx_cbm_to ON call_booking_mails (to_email);
