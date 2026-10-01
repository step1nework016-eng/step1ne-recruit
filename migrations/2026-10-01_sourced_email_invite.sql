-- 2026-10-01 外部人選（sourced_candidates）Email 邀請＋TG 待判斷卡。只新增，不動舊表。
-- 已在線上 D1 執行過（CREATE ... IF NOT EXISTS，重跑無害）。
CREATE TABLE IF NOT EXISTS sourced_email_invites (
  id TEXT PRIMARY KEY, sourced_id TEXT NOT NULL, job_slug TEXT NOT NULL, to_email TEXT NOT NULL,
  subject TEXT, why TEXT, status TEXT NOT NULL,          -- sending / sent / failed
  sent_by TEXT, via TEXT, sent_at TEXT, message_id TEXT, error TEXT, created_at TEXT NOT NULL);
-- 同一個信箱＋同一個職缺只會有一列＝一個人一個職缺只寄一封
CREATE UNIQUE INDEX IF NOT EXISTS ux_sourced_email_invites_email_job ON sourced_email_invites(to_email, job_slug);
CREATE INDEX IF NOT EXISTS ix_sourced_email_invites_sent ON sourced_email_invites(status, sent_at);
CREATE TABLE IF NOT EXISTS sourced_tg_cards (
  sourced_id TEXT PRIMARY KEY, job_slug TEXT, chat_id TEXT, message_id INTEGER, pushed_at TEXT, trigger TEXT,
  action TEXT, action_by TEXT, action_at TEXT);      -- action: sent / unfit / skipped
CREATE INDEX IF NOT EXISTS ix_sourced_tg_cards_pushed ON sourced_tg_cards(pushed_at);
