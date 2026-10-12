-- 2026-10-12 Jacky 核准：official@ 與 jackychen@ 兩個信箱備份進系統（只讀，不改信箱）。
-- 寫入者：step1ne-recruit/mail_archive.py（只在 Mac 跑，jackychen@ 密碼只在這台）。
-- ⚠️ jackychen@ 的信只准 Jacky／Phoebe 看——後台目前只有一把共用 ADMIN_TOKEN，
--    沒有「這是誰」的身分，所以讀取 API 尚未開放，等身分機制定案再做。

CREATE TABLE IF NOT EXISTS mail_archive (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  mailbox       TEXT NOT NULL,              -- official@step1ne.com / jackychen@step1ne.com
  folder        TEXT NOT NULL,              -- in（收件）/ sent（寄件）
  imap_folder   TEXT,
  uidvalidity   TEXT,
  uid           INTEGER,
  message_id    TEXT NOT NULL,
  in_reply_to   TEXT,
  refs          TEXT,
  sent_at       TEXT,                       -- 台北時間 YYYY-MM-DD HH:MM:SS
  from_addr     TEXT,
  from_name     TEXT,
  to_addrs      TEXT,
  cc_addrs      TEXT,
  subject       TEXT,
  body_text     TEXT,                       -- 純文字，上限約 50KB
  body_truncated INTEGER DEFAULT 0,
  att_count     INTEGER DEFAULT 0,          -- 應存的附件數
  att_saved     INTEGER DEFAULT 0,          -- 實際存進 R2 的附件數
  att_skipped   TEXT,                       -- 超過 15MB 等沒存的附件說明
  is_system_copy INTEGER DEFAULT 0,         -- mail_sent_mirror 放進寄件備份的系統信副本
  link_type     TEXT,                       -- 主要對到誰：candidate / client / bd
  link_id       TEXT,
  link_name     TEXT,
  created_at    TEXT NOT NULL,
  UNIQUE(mailbox, folder, message_id)
);
CREATE INDEX IF NOT EXISTS idx_mail_archive_box_time ON mail_archive(mailbox, sent_at);
CREATE INDEX IF NOT EXISTS idx_mail_archive_time ON mail_archive(sent_at);
CREATE INDEX IF NOT EXISTS idx_mail_archive_msgid ON mail_archive(message_id);

CREATE TABLE IF NOT EXISTS mail_archive_links (
  archive_id  INTEGER NOT NULL,
  link_type   TEXT NOT NULL,               -- candidate(applications.id) / client(client_companies.id) / client_contact(client_contacts.id) / bd(bd_outreach.id)
  link_id     TEXT NOT NULL,
  link_name   TEXT,
  match_by    TEXT,                        -- email / domain
  addr        TEXT,
  PRIMARY KEY (archive_id, link_type, link_id)
);
CREATE INDEX IF NOT EXISTS idx_mail_archive_links_target ON mail_archive_links(link_type, link_id);

CREATE TABLE IF NOT EXISTS mail_archive_attachments (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  archive_id  INTEGER NOT NULL,
  part_idx    INTEGER NOT NULL,
  file_id     TEXT NOT NULL,               -- files.id（storage='r2'）
  filename    TEXT,
  mime        TEXT,
  size        INTEGER,
  created_at  TEXT NOT NULL,
  UNIQUE(archive_id, part_idx)
);
CREATE INDEX IF NOT EXISTS idx_mail_archive_att_file ON mail_archive_attachments(file_id);

-- 2026-10-12 Jacky 核准：jackychen@ 的信用「私人鑰匙」開門。每人一把，只存 SHA-256 雜湊不存原文；
-- 刪掉那一列＝作廢那把鑰匙。鑰匙原文只私訊給本人，不進任何檔案。
CREATE TABLE IF NOT EXISTS mail_private_keys (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  label        TEXT NOT NULL,              -- Jacky / Phoebe
  key_hash     TEXT NOT NULL UNIQUE,       -- sha256(鑰匙原文) hex
  created_at   TEXT NOT NULL,
  last_used_at TEXT
);
