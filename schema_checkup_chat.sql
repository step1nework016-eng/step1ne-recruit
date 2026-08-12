-- AI 履歷健檢（阿福）── 對話功能 migration　2026-08-12
--
-- 目的：讓阿福能像阿財一樣跟本人對話，而不是只收單向表單。
-- 架構借用阿財那條線的模式（chat_token、輪詢、跨行程鎖），
-- 但資料表完全獨立，不共用 messages／applications 任何一欄。
--
-- ⚠️ 執行方式：
--    set -a; . ~/.config/workflow-os/cf.env; set +a
--    cd ~/工作流程技能包/step1ne-recruit
--    npx --yes wrangler d1 execute step1ne-recruit --remote --file=schema_checkup_chat.sql
--
-- 這支只 ALTER / CREATE，不動既有資料——除了 checkup_messages 一項例外
-- （見下方說明，2026-08-12 當下這張表在 remote D1 是 0 筆真實資料，
--  安全可以整張重建）。

-- 對談狀態欄位。命名對照 applications 的 interview_state／interview_started_at／
-- interview_ended_at／lock_expires_at，但刻意用不同的欄位名（chat_state 不是
-- interview_state），讓人一看就知道這是阿福的欄位，不會被誤改到阿財那邊的邏輯。
ALTER TABLE checkups ADD COLUMN chat_token TEXT;
ALTER TABLE checkups ADD COLUMN chat_state TEXT NOT NULL DEFAULT 'not_started';
                -- not_started 還沒開口 / active 對談中 / paused 本人離開，房間還留著 / done 已收尾
ALTER TABLE checkups ADD COLUMN chat_started_at TEXT;
ALTER TABLE checkups ADD COLUMN chat_ended_at TEXT;

-- 跨行程鎖：daemon 輪詢跟未來任何手動收尾工具都要搶這個才能動同一筆的訊息。
-- 見 interview_daemon.py 的 acquire_lock() 那段註解——同樣的問題這裡也會有。
ALTER TABLE checkups ADD COLUMN lock_expires_at TEXT;

-- 報告內容直接存一份在這裡（不只是路徑）。
-- 為什麼：Cloudflare Worker 連不到本機檔案系統，checkup_reports/*.html 是本機路徑，
-- Worker 沒辦法把它「發」給本人。報告本文通常一萬字元上下（實測 MaryLee 範本
-- 10,260 bytes），遠低於 D1 單值 2MB 上限，不需要切塊，直接存一欄，
-- 讓 /checkup-chat/<token>/report 能直接把這欄回傳給瀏覽器。
-- 本機仍然照存一份 .html／.pdf（report_html_path／report_pdf_path 兩個既有欄位），
-- 那是給顧問在 Telegram 上收 PDF、以及留檔案底稿用的，兩邊不衝突。
ALTER TABLE checkups ADD COLUMN report_html TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_checkup_chat_token ON checkups(chat_token);

-- ⚠️ 重建 checkup_messages：原本的 id 是 TEXT（uuid），輪詢用的 /poll?after=<id>
-- 需要「比大小」的游標（跟阿財的 messages.id INTEGER AUTOINCREMENT 同一套做法），
-- TEXT 型的 uuid 沒有順序可比。2026-08-12 當下這張表在 remote D1 是 0 筆真實資料
-- （這個功能從沒被真人用過），所以直接刪掉重建，不需要遷移既有資料。
-- 如果之後某天這張表已經有真實資料才要加欄位，改用 ALTER TABLE ADD COLUMN，
-- 不要再用 DROP。
DROP TABLE IF EXISTS checkup_messages;

CREATE TABLE checkup_messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  checkup_id  TEXT NOT NULL,
  role        TEXT NOT NULL,             -- afu 阿福 / user 本人 / system 系統事件（含開場暗號）
  content     TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkup_msg ON checkup_messages(checkup_id, created_at);
