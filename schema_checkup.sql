-- AI 履歷健檢（阿福）專用資料表　2026-08-10
--
-- 🚨 為什麼要獨立一組表，不能塞進 applications：
--   顧問後台的漏斗（應徵→談完→送客戶→客戶面試→到職）是判斷職缺好壞的依據。
--   來做履歷健檢的人**沒有應徵任何職缺**，塞進 applications 會讓每一個漏斗數字失真。
--   （規格：memory/project_resume_checkup.md「跟阿財完全分開」那一節）
--
-- 可以共用的只有基礎設施：檔案存檔沿用既有的 files + file_chunks 切塊機制，
-- 這裡只存關聯。大腦（阿福 vs 阿財）與資料一律分開。
--
-- ⚠️ 全部是 CREATE TABLE / ADD COLUMN，沒有重建或修改任何既有的表。
-- ⚠️ 執行方式：
--    set -a; . ~/.config/workflow-os/cf.env; set +a
--    cd ~/工作流程技能包/step1ne-recruit
--    npx --yes wrangler d1 execute step1ne-recruit --remote --file=schema_checkup.sql

CREATE TABLE IF NOT EXISTS checkups (
  id            TEXT PRIMARY KEY,
  created_at    TEXT NOT NULL,           -- 台北時間字串

  -- 基本資料。只要三格：多一欄就少一些完成率，而這是主動上門的人，不要嚇跑他。
  name          TEXT NOT NULL,
  email         TEXT NOT NULL,
  phone         TEXT,
  current_title    TEXT,                 -- 選填：目前職稱
  current_industry TEXT,                 -- 選填：目前產業
  note          TEXT,                    -- 他想特別讓顧問知道的事

  -- 履歷正文。檔案本身走 checkup_files → files，這裡放解析後的純文字，
  -- 讓阿福不用每個回合都重新解析 PDF。
  resume_file_id TEXT,                   -- 主履歷的 files.id（方便查詢，檔案關聯仍以 checkup_files 為準）
  resume_text    TEXT,
  resume_readable INTEGER,               -- 1 可讀 / 0 抽不出文字（掃描影像式 PDF）
  resume_note    TEXT,                   -- 抽不出來的原因，給顧問看，不要跟本人解釋
  parsed_at      TEXT,

  -- 🚨 阿福一定要問出來的三個數字。問不到就留 NULL，報告寫「未取得」，
  --    **絕對不准用推測填**——這三個數字是開價的依據，猜錯比空白傷害大。
  led_headcount     TEXT,                -- 帶過幾人
  budget_scale      TEXT,                -- 負責多少預算／營收
  crowdfunding_raised TEXT,              -- （做群募的）募資金額與達標率

  -- 流程狀態
  status        TEXT NOT NULL DEFAULT 'new',
                -- new        收到了，還沒解析
                -- parsed     履歷已抽成文字，可以開始對談
                -- talking    阿福對談中
                -- reported   報告已產出
                -- delivered  報告已交付本人
                -- closed     結案
  consent_at    TEXT,                    -- 送出時間即同意時間（表單上只有一行小字，不做勾選同意）

  -- 收費：這一版先不收費（就服法對「向求職者收費」的適用範圍還沒確認，
  -- 見 memory/project_resume_checkup.md）。欄位先留著，流程走得通就好。
  fee_status    TEXT NOT NULL DEFAULT 'free',   -- free / pending / paid / waived
  fee_amount    INTEGER,
  fee_note      TEXT,
  paid_at       TEXT,

  -- 出口：接 candidate-led-bd 用的匿名人選卡（不含姓名、現職公司、學校）。
  -- 產出方式見 make_checkup_report.py --anon-card。
  anon_card_json TEXT,
  anon_card_at   TEXT,

  -- 報告
  report_html_path TEXT,
  report_pdf_path  TEXT,
  report_at        TEXT,

  -- 來源歸因
  utm_source    TEXT,
  utm_medium    TEXT,
  utm_campaign  TEXT,
  referrer      TEXT,

  handled_by    TEXT,
  handled_note  TEXT,
  updated_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_checkup_status  ON checkups(status);
CREATE INDEX IF NOT EXISTS idx_checkup_created ON checkups(created_at);
CREATE INDEX IF NOT EXISTS idx_checkup_email   ON checkups(email);

-- 附件。一個人可以同時上傳履歷、作品集與其他檔案。
-- 檔案內容本身沿用 files + file_chunks，這張表只記關聯與「這是哪一種檔」。
CREATE TABLE IF NOT EXISTS checkup_files (
  checkup_id  TEXT NOT NULL,
  file_id     TEXT NOT NULL,             -- files.id
  kind        TEXT,                      -- resume 履歷 / portfolio 作品集 / other 其他
  created_at  TEXT NOT NULL,
  PRIMARY KEY (checkup_id, file_id)
);

CREATE INDEX IF NOT EXISTS idx_checkup_files ON checkup_files(checkup_id);

-- 連結欄位（可多個）：作品集網站、LinkedIn、個人網站。
-- 分成一張表而不是一個逗號字串，是因為之後要一條一條標「抓到了沒、抓到什麼」。
CREATE TABLE IF NOT EXISTS checkup_links (
  checkup_id  TEXT NOT NULL,
  idx         INTEGER NOT NULL,
  url         TEXT NOT NULL,
  label       TEXT,                      -- 本人自己標的（作品集／LinkedIn／個人網站）
  fetched_at  TEXT,
  fetch_note  TEXT,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (checkup_id, idx)
);

-- 阿福的對談紀錄。刻意跟 messages（阿財的面談逐字稿）分開，
-- 兩邊的大腦與報告格式都不同，混在一起遲早會有人拿錯資料去產報告。
CREATE TABLE IF NOT EXISTS checkup_messages (
  id          TEXT PRIMARY KEY,
  checkup_id  TEXT NOT NULL,
  role        TEXT NOT NULL,             -- afu 阿福 / user 本人 / system 系統事件
  content     TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkup_msg ON checkup_messages(checkup_id, created_at);
