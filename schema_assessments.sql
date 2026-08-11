-- 人格特質測驗結果
--
-- 為什麼不繼續塞在 applications 表：
-- 2026-08-07 之前只有 DISC 四個數字（disc_d/i/s/c），直接掛在 applications 上。
-- 現在要存 Big Five 五維、Grit、原始作答，欄位會爆炸，而且測驗是「一次性事件」，
-- 跟應徵資料的生命週期不同（應徵資料會被顧問改，測驗結果不該被改）。
--
-- ⚠️ 計分性質不一樣，不要混用：
--   Big Five／Grit 是 **normative（常模）** 計分——分數可以跨人比較，可以排序。
--   DISC 是 **ipsative（強迫選擇）** 計分——分數只在同一個人內部有意義，
--   **理論上不能拿來跨人比較**（說「A 的 D 比 B 高」在統計上是無效的）。
--   所以 DISC 只當「描述這個人的工作風格」用，永遠不進分數、不排序、不當篩選門檻。

CREATE TABLE IF NOT EXISTS assessments (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id TEXT NOT NULL,

  -- Big Five（Mini-IPIP 改寫，每維 4 題，1–5 分，存平均值）
  -- 題本來源：IPIP（International Personality Item Pool）為公共領域，可自由使用。
  -- 中文題目是依構念自行改寫成職場情境，**不是官方中文版**，
  -- 所以沒有台灣常模——報告裡一定要標明這件事，不要講得像有標準化分數。
  b5_o           REAL,   -- 開放性 Openness：學新東西的意願
  b5_c           REAL,   -- 盡責性 Conscientiousness：工作態度＋執行力（跨職種預測效度最高的一維）
  b5_e           REAL,   -- 外向性 Extraversion
  b5_a           REAL,   -- 親和性 Agreeableness
  b5_n           REAL,   -- 情緒穩定性的反向：分數高＝情緒起伏大（抗壓相關）

  -- Grit-S（Duckworth & Quinn 2009，8 題，1–5 分）
  -- 補 Big Five 沒有的「長期不放棄」。對派遣缺與業務缺特別相關——
  -- 那兩種缺流失的原因通常不是能力，是撐不撐得住。
  grit           REAL,
  grit_interest  REAL,   -- 興趣持續性（4 題，反向計分）
  grit_effort    REAL,   -- 努力持續性（4 題）

  -- DISC（沿用原本自寫的 20 組題本，ipsative，只當描述用）
  disc_d         INTEGER,
  disc_i         INTEGER,
  disc_s         INTEGER,
  disc_c         INTEGER,
  disc_primary   TEXT,

  answers_json   TEXT,   -- 原始作答，留著以後要重算或檢查作答品質
  quality_flag   TEXT,   -- 作答品質警訊：'straight_line'（全選同一個）／'too_fast'／NULL
  seconds_taken  INTEGER,

  created_at     TEXT NOT NULL DEFAULT (datetime('now','+8 hours'))
);

CREATE INDEX IF NOT EXISTS idx_assess_app ON assessments(application_id);

-- 面談時段預約
--
-- 為什麼要有名額上限：原本是 datetime-local 讓候選人自由填時間，
-- 20 個人可以全部填同一個時間點，那等於沒有分流。
-- 改成固定時段 × 名額上限，額滿的時段直接不給選。
--
-- 名額怎麼定：阿財的 MAX_PARALLEL=3（同時最多生成 3 則回覆），
-- 一場面談 40–60 分鐘，所以一個 30 分鐘的時段開 2 個名額，
-- 讓相鄰時段的面談重疊時仍有餘裕。

CREATE TABLE IF NOT EXISTS interview_bookings (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id TEXT NOT NULL,
  slot_at        TEXT NOT NULL,   -- 'YYYY-MM-DD HH:MM'，台北時間
  created_at     TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  cancelled_at   TEXT             -- 取消不刪除，留著才知道有人改過時間
);

CREATE INDEX IF NOT EXISTS idx_book_slot ON interview_bookings(slot_at);
CREATE INDEX IF NOT EXISTS idx_book_app  ON interview_bookings(application_id);
