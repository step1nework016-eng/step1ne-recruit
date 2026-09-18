-- 阿財 P3｜2026-09-18
-- 全部是「新增」，沒有任何 DROP / ALTER COLUMN / UPDATE 既有資料。
-- Rollback：DROP 這兩張新表即可；新增的欄位留著不影響任何既有邏輯。

-- ── P3-A：AI 職缺推薦（一筆 = AI 對一位候選人推薦一個職缺）──────────────
-- 刻意不沿用既有的 candidate_job_match：那張是「對外獵才 sourcing」專用
-- （由 matching_engine.py／sourcing 流程寫入，27 筆），語意是「我們主動去外面
-- 找到的人 vs 某職缺」。這張是「已經進來面談過的人 vs 其他開放職缺」，
-- 生命週期、審核流程、欄位都不同，混在一起會讓兩邊的查詢都要一直加條件排除對方。
CREATE TABLE IF NOT EXISTS candidate_job_recommendations (
  id                       TEXT PRIMARY KEY,
  application_id           TEXT NOT NULL,
  source_job_slug          TEXT,            -- 候選人原本應徵的職缺
  recommended_job_slug     TEXT NOT NULL,   -- AI 推薦的替代職缺
  match_status             TEXT NOT NULL,   -- MATCH_CANDIDATE|POSSIBLE_MATCH|INSUFFICIENT_DATA|NOT_MATCH
                                            -- ⚠️ 刻意沿用 matching_engine.py 既有的四個值，不另造詞彙
  confidence               TEXT,            -- high|medium|low
  rank                     INTEGER,         -- 1..3
  reasons_json             TEXT,            -- 具體理由（陣列）
  blockers_json            TEXT,            -- 已知阻礙（陣列）
  missing_information_json TEXT,            -- 還需要確認什麼（陣列）
  evidence_json            TEXT,            -- 每條理由對應的原始依據（履歷/逐字稿片段）
  candidate_snapshot_json  TEXT,            -- 當下用來判斷的候選人資料快照
  job_snapshot_json        TEXT,            -- 當下用來判斷的職缺資料快照
  status                   TEXT NOT NULL DEFAULT 'pending_review',
                                            -- pending_review|approved|rejected|superseded
  consultant_decision      TEXT,            -- 顧問決定（AI 永遠不得寫這欄）
  consultant_decision_by   TEXT,
  consultant_decision_at   TEXT,
  rejection_reason         TEXT,
  source_report_id         TEXT,            -- 依據哪一份面談報告產生（idempotency key 的一半）
  model                    TEXT,
  prompt_version           TEXT,
  created_at               TEXT NOT NULL,
  updated_at               TEXT
);

-- idempotency：同一份報告 × 同一個被推薦職缺，只能有一筆。
-- 報告重跑（新 report_id）會是新的一輪，不會被這個唯一鍵擋住。
CREATE UNIQUE INDEX IF NOT EXISTS idx_cjr_report_job
  ON candidate_job_recommendations (source_report_id, recommended_job_slug);
CREATE INDEX IF NOT EXISTS idx_cjr_app     ON candidate_job_recommendations (application_id);
CREATE INDEX IF NOT EXISTS idx_cjr_status  ON candidate_job_recommendations (status);

-- ── P3-C②：未來日期的追蹤約定（「下個月再聯絡我」）────────────────────
-- 全系統在這之前沒有任何「未來要做的事」的資料模型，只有「過去發生過的事」
-- （candidate_notes）跟「固定間隔提醒」（各種 *_notified_at）。這張補上這一塊。
CREATE TABLE IF NOT EXISTS candidate_followups (
  id             TEXT PRIMARY KEY,
  application_id TEXT NOT NULL,
  due_at         TEXT NOT NULL,   -- 什麼時候該再聯絡（YYYY-MM-DD HH:MM:SS，台北時間）
  reason         TEXT,            -- 為什麼（候選人原話為主）
  source         TEXT,            -- ai_interview|consultant|system
  evidence       TEXT,            -- 逐字稿原句，讓顧問可以確認 AI 沒有腦補
  status         TEXT NOT NULL DEFAULT 'pending',  -- pending|notified|done|cancelled
  notified_at    TEXT,
  created_by     TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_cfu_due    ON candidate_followups (status, due_at);
CREATE INDEX IF NOT EXISTS idx_cfu_app    ON candidate_followups (application_id);
