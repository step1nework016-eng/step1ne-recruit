-- 2026-10-23 資安 F002 Stage A：記錄是哪一把權杖在開後台的門（抽樣：每 isolate 每把每小時最多一筆）
-- Stage B 要靠這張表確認 legacy-admin（舊共用 ADMIN_TOKEN）連續一週沒人用，才關掉舊鑰匙。
CREATE TABLE IF NOT EXISTS api_caller_seen (
  caller     TEXT NOT NULL,
  day        TEXT NOT NULL,
  worker     TEXT NOT NULL,
  first_seen TEXT,
  last_seen  TEXT,
  last_path  TEXT,
  last_ua    TEXT,
  samples    INTEGER DEFAULT 0,
  PRIMARY KEY (caller, day, worker)
);
