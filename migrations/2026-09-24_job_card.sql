-- 職缺卡（讓阿財越評越準）。Jacky 交辦，2026-09-24。
--
-- 目的：顧問每次聽完客戶回饋就餵進來，累積成「這個職缺客戶真正要什麼」的判斷標準；
-- 阿財面談這個職缺前先讀 job_card_profile.masked_summary_text（已經濾掉客戶名／薪資／
-- 內部評語的濃縮版，避免面談變慢也避免洩漏機密）；顧問在後台看的是 full_profile_json
-- （完整版，含來源）。
--
-- job_card_events 是只增不改的經驗值帳本——每一筆匯入／推薦／錄取都是一列，
-- 現在的等級與經驗值一律用 SUM() 從這張表現算，不放在 job_card_profile 裡快取成
-- 一個會跟帳本兜不起來的數字。job_card_profile 只放「算完的結果」跟「濃縮摘要文字」，
-- 是可以整張重算重寫的衍生資料，不是真相來源。
--
-- 只新增表，不動 jobs／placements／job_expertise 任何既有欄位。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=migrations/2026-09-24_job_card.sql

CREATE TABLE IF NOT EXISTS job_card_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  job_slug      TEXT NOT NULL,
  event_type    TEXT NOT NULL,   -- feedback_import / recommend / placed / client_declined
  xp_delta      INTEGER NOT NULL,
  application_id TEXT,           -- 對得回 applications；feedback_import 事件可為空
  placement_id  INTEGER,         -- 對得回 placements；feedback_import 事件可為空
  grade         TEXT,            -- 觸發當下阿財的總分等第（優/良/中/待加強），feedback_import 可為空
  route_code    TEXT,            -- 觸發當下阿財的分流（A-E），feedback_import 可為空
  note          TEXT,            -- 人看的一句話說明，例如「顧問 Ariel 匯入客戶回饋」
  created_by    TEXT,            -- 顧問帳號／'system'（sync_events 自動產生的用 system）
  -- 防止 sync_events 每次重跑都對同一筆 placements 狀態重複記一次分：
  -- 同一個 (job_slug, event_type, placement_id) 只能有一筆 recommend／placed／client_declined。
  dedupe_key    TEXT UNIQUE,
  created_at    TEXT NOT NULL DEFAULT (datetime('now','+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_job_card_events_slug ON job_card_events(job_slug, created_at);

CREATE TABLE IF NOT EXISTS job_card_profile (
  job_slug              TEXT PRIMARY KEY,
  full_profile_json      TEXT,   -- 顧問看的完整版：{skills:[{text,source_note}],traits:[...],plus:[...],fail_reasons:[...]}
  masked_summary_text    TEXT,   -- 阿財讀的濃縮乾淨版（純文字，不含客戶名/薪資/內部評語），面談時直接塞進提示詞
  feedback_count         INTEGER NOT NULL DEFAULT 0,
  xp_total               INTEGER NOT NULL DEFAULT 0,
  level                  INTEGER NOT NULL DEFAULT 0,
  level_capped_by_gate   INTEGER NOT NULL DEFAULT 0,  -- 1＝經驗值已到Lv.10門檻但品質門檻沒過，卡在Lv.9
  advance_rate           REAL,   -- 阿財評A的人選中，送進客戶端面試的比例（樣本數不足時為 NULL）
  hire_rate              REAL,   -- 阿財評A的人選中，最終被客戶錄取的比例（樣本數不足時為 NULL）
  accuracy_rate          REAL,   -- 命中率：已有明確結果的推薦中，最終錄取的比例（含所有等第，不只A）
  decline_rate           REAL,   -- 婉拒率：已有明確結果的推薦中，被客戶明確回絕的比例
  a_grade_settled_n      INTEGER NOT NULL DEFAULT 0,  -- 阿財評A、且已送出＋已有明確結果的案例數（Lv.10 門檻的樣本數）
  updated_at             TEXT NOT NULL DEFAULT (datetime('now','+8 hours'))
);
