-- 2026-08-04：履歷 × JD 初篩。流程圖上「收履歷」與「AI 面談」中間那個紅框。
--
-- 為什麼獨立一張表而不是塞進 applications：
-- 評分是可以重跑的（校準規則之後想重評），塞進主表就只剩最後一次結果，
-- 看不出「規則改了之後判斷差多少」——而那正是校準時唯一需要看的東西。
CREATE TABLE IF NOT EXISTS screenings (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id TEXT NOT NULL,
  job_slug       TEXT,
  score          INTEGER,          -- 0–100
  verdict        TEXT,             -- strong / yes / hold / no
  summary        TEXT,             -- 一句話結論
  strengths      TEXT,             -- JSON 陣列
  risks          TEXT,             -- JSON 陣列
  questions      TEXT,             -- JSON 陣列，針對風險點的面談追問
  hard_fail      TEXT,             -- 沒過客戶硬性條件的話，寫哪一條
  model          TEXT,
  rubric_ver     TEXT,             -- 用哪一版評分標準跑的，校準後會變
  created_at     TEXT NOT NULL DEFAULT (datetime('now','+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_screen_app ON screenings(application_id, created_at);
