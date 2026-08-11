-- 顧問自助新增職缺：收件佇列（2026-08-10）
--
-- 為什麼要有這張表：在此之前，新增職缺的唯一路徑是「顧問把 JD 丟給 Claude，
-- Claude 手動跑 publish_job.py」。顧問自己在手機上沒有任何入口，
-- 而且顧問傳過來的原始檔（PDF／截圖／LINE 貼的文字）沒有任何地方留存——
-- 事後想回頭查「這個薪資當初客戶是怎麼寫的」就查不到了。
--
-- 這張表就是那個入口與那份底稿。原始檔本身沿用既有的 files／file_chunks
-- 分段存檔機制（saveResume 那一套），這裡只存關聯。
--
-- ⚠️ 全部是 CREATE TABLE / ADD COLUMN，沒有重建任何既有的表。
-- ⚠️ 執行方式（請顧問或有權限的人自己跑，這支腳本不會自動執行）：
--    cd ~/工作流程技能包/step1ne-recruit
--    npx --yes wrangler d1 execute step1ne-recruit --remote --file=jobintake/schema_job_intake.sql

CREATE TABLE IF NOT EXISTS job_intakes (
  id            TEXT PRIMARY KEY,
  created_at    TEXT NOT NULL,          -- 台北時間字串
  submitted_by  TEXT,                   -- 哪位顧問送的（後台表單填）

  -- 顧問在表單上選的兩個分類。兩個都不可為空——判錯會直接影響頁面呈現與法遵。
  service_line     TEXT NOT NULL,       -- dispatch 派遣 / permanent 正職代招 / executive 中高階
  client_relation  TEXT NOT NULL,       -- signed 已簽約 / unsigned 未簽約 / private 朋友私人協助

  -- 由 client_relation 自動帶入，顧問不用一個一個設（規則見 memory/client_relation_types.md）
  --   signed   → client_named=1, ai_disclosure='always',  brand_mode='step1ne'
  --   unsigned → client_named=0, ai_disclosure='never',   brand_mode='step1ne'
  --   private  → client_named=1, ai_disclosure='never',   brand_mode='none'
  client_named     INTEGER,
  ai_disclosure    TEXT,
  brand_mode       TEXT,                -- step1ne / none（報告掛不掛品牌）

  -- 內部欄位，絕不對外輸出
  client_code      TEXT,
  client_name      TEXT,                -- 真實公司名。只出現在內部端點與後台。

  -- 四種來源，顧問可以同時給好幾種
  raw_text      TEXT,                   -- 純文字敘述（顧問直接打或從 LINE 貼過來）
  source_url    TEXT,                   -- 職缺連結（104／客戶官網／Google Doc）
  note          TEXT,                   -- 顧問給總指揮的補充交代

  -- 流程狀態
  status        TEXT NOT NULL DEFAULT 'new',
                -- new         收到了，還沒擬 JD
                -- drafting    總指揮擬稿中
                -- pending     已送 Telegram 待顧問決定
                -- approved    顧問按了「核准發布」，等本機發布
                -- published   已發布上線
                -- rewrite     顧問按了「重寫」，要重擬
                -- rejected    顧問按了「拒絕發布」
                -- failed      擬稿失敗
  draft_json    TEXT,                   -- 擬好的職缺規格（publish_job.py 吃的那份 JSON）
  filter_json   TEXT,                   -- 禁刊過濾器的完整結果（擋掉什麼、待確認什麼）
  review_text   TEXT,                   -- 實際送到 Telegram 的送審訊息全文
  tg_message_id INTEGER,                -- 送審訊息的 message_id，回呼時要編輯同一則
  decided_by    TEXT,                   -- 誰按的按鈕
  decided_at    TEXT,
  rewrite_note  TEXT,                   -- 按「重寫」之後顧問補的意見
  published_slug TEXT,                  -- 發布後的 slug
  updated_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_intake_status  ON job_intakes(status);
CREATE INDEX IF NOT EXISTS idx_intake_created ON job_intakes(created_at);

-- 顧問上傳的原始檔（PDF／圖片）。檔案內容本身走 files + file_chunks，
-- 這張表只記「哪個 intake 有哪些檔」。一份 intake 可以有多個檔。
--
-- ⚠️ 原始檔一律保留，不隨 intake 被拒絕而刪除——顧問要能回溯
-- 「當初客戶給的是什麼」，那份底稿比擬出來的 JD 更重要。
CREATE TABLE IF NOT EXISTS job_intake_files (
  intake_id   TEXT NOT NULL,
  file_id     TEXT NOT NULL,            -- files.id
  kind        TEXT,                     -- pdf / image / other
  created_at  TEXT NOT NULL,
  PRIMARY KEY (intake_id, file_id)
);

CREATE INDEX IF NOT EXISTS idx_intake_files ON job_intake_files(intake_id);

-- jobs 表補欄位。
-- client_relation 依 memory/client_relation_types.md 記載已於 2026-08-10 加過；
-- 若該次沒跑成功，把下面這行的註解拿掉再執行一次（重複執行會報 duplicate column，
-- 那個錯誤是安全的，代表欄位已存在）。
-- ALTER TABLE jobs ADD COLUMN client_relation TEXT;

-- 職缺頁是從哪一份收件單來的。有了這欄，日後看到線上某個職缺，
-- 可以直接回頭找出顧問當初上傳的原始 PDF／截圖。
ALTER TABLE jobs ADD COLUMN intake_id TEXT;
