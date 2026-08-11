-- 面談投入度
--
-- 為什麼要有這張表：2026-08-06 顧問說「有一部分的人沒認真看待這場面談，
-- 就像我們打電話過去、但他在另一端不知道在幹嘛，有沒有認真很難判斷」。
--
-- 先用既有資料驗證過兩個候選訊號：
--   ✅ 回覆字數——7 個人 7 個準（平均 ≥37 字全部活著，≤13 字全部掉了）
--   ❌ 回覆速度——分不出來（掉的人 188/252 秒，但活著的呂皓宇 222 秒夾在中間，
--      而且所有人平均都要 2–4 分鐘，代表大家都在一邊做別的事）
--
-- 字數是「答得認不認真」，這張表補的是「人在不在」——切走幾次、離開多久。
-- 那是顧問打電話時最想知道、但看不到的東西。
--
-- ⚠️ 這是行為訊號，不是評分。**不要拿來自動刷掉候選人**——
-- 有人開兩個視窗查資料再回答，那是認真，不是分心。給顧問看，讓人判斷。

CREATE TABLE IF NOT EXISTS engagement (
  application_id TEXT PRIMARY KEY,
  away_count     INTEGER NOT NULL DEFAULT 0,   -- 切換到別的分頁／視窗幾次
  away_seconds   INTEGER NOT NULL DEFAULT 0,   -- 累計離開秒數
  longest_away   INTEGER NOT NULL DEFAULT 0,   -- 最久一次離開幾秒
  paste_count    INTEGER NOT NULL DEFAULT 0,   -- 貼上幾次（整段貼上可能是範本或 AI 生成）
  paste_chars    INTEGER NOT NULL DEFAULT 0,   -- 貼上的總字數
  active_seconds INTEGER NOT NULL DEFAULT 0,   -- 頁面在前景的秒數
  updated_at     TEXT NOT NULL DEFAULT (datetime('now','+8 hours'))
);
