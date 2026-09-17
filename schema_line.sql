-- LINE OA「查詢面試進度」功能。2026-08-12 加。
--
-- 目的：候選人自己在 LINE 官方帳號（全民獵才）查詢目前面試到哪一關，
-- 不用每次都私訊顧問「我現在到哪一關了」。
--
-- 候選人 LINE userId ↔ 手機號碼 ↔ 應徵紀錄的綁定表。
-- state 是這支功能自己的一個小型對話狀態機（跟阿財面談用的 interview_state
-- 是完全不同的兩件事，不要搞混）：
--   pending_phone：已經跟候選人要過手機號碼，還沒比對成功（或還沒問過）
--   bound：比對到至少一筆應徵紀錄，之後查詢直接回覆，不用再問一次手機號碼
-- ⚠️ 2026-08-13 改：原本只比對手機號碼，Jacky 要求改成姓名＋信箱＋手機
--    三項一起交叉比對才能綁定——手機號碼容易重複或打錯一碼就撞到別人，
--    三項全對才綁，降低「認錯人」的機率（認錯人比沒認出來更糟，
--    參考 report_tick.py 同樣的「先問再寫」精神）。
CREATE TABLE IF NOT EXISTS line_bindings (
  line_user_id     TEXT PRIMARY KEY,
  state            TEXT NOT NULL DEFAULT 'pending_phone',
  phone            TEXT,                      -- 綁定成功時記下候選人回報的手機號碼（原始輸入，非正規化）
  email            TEXT,                      -- 綁定成功時記下候選人回報的信箱（2026-08-13 加，三項比對用）
  application_ids  TEXT,                      -- JSON 陣列，比對到的 applications.id
  created_at       TEXT NOT NULL,
  bound_at         TEXT,                      -- 綁定成功的時間；未綁定則為 NULL
  updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_line_bindings_state ON line_bindings(state);

-- ⚠️ 這欄不在原始 CREATE TABLE 裡，是後來用 wrangler d1 execute --remote 直接
-- ALTER TABLE 加的，不會因為重跑這個檔案自動補上（IF NOT EXISTS 語法 D1 的
-- ALTER TABLE ADD COLUMN 不支援，只能新機器初始化時手動照這份清單跑一次）：
--   ALTER TABLE line_bindings ADD COLUMN source TEXT;          -- 2026-09 前某次加，校園徵才/社群貼文歸因用
--
-- 2026-09-16 LIFF 整合加，同樣用 ALTER TABLE 補（見 step1ne-public-worker 的
-- POST /chat/:t/liff-bind）：
--   ALTER TABLE line_bindings ADD COLUMN display_name TEXT;      -- LIFF liff.getProfile() 拿到的顯示名稱
--   ALTER TABLE line_bindings ADD COLUMN picture_url TEXT;       -- LIFF liff.getProfile() 拿到的頭像網址
--   ALTER TABLE line_bindings ADD COLUMN profile_synced_at TEXT; -- 上面兩欄最後一次同步的時間
