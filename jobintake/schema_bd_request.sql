-- 顧問的開發需求：反向開發真正的入口。
--
-- ⚠️ 2026-08-11 修正過一次方向。原本做成「挑一份履歷 → 找公司」，
--    但顧問腦子裡的順序是反的：他是先想「我要開發做這種缺的客戶」，
--    才由系統去人才庫撈得上用場的人。入口做錯，顧問就不會用。
CREATE TABLE IF NOT EXISTS bd_requests (
  id          TEXT PRIMARY KEY,
  created_at  TEXT NOT NULL,
  submitted_by TEXT,
  role_family TEXT NOT NULL,      -- 要開發哪一種職缺（例：MEP／廠務工程師）
  industry    TEXT,               -- 想切的產業
  region      TEXT,               -- 地區
  service_line TEXT,              -- dispatch / direct / executive
  target_count INTEGER DEFAULT 6, -- 要幾家
  note        TEXT,               -- 顧問交代
  status      TEXT NOT NULL DEFAULT 'new',  -- new/working/done/failed
  batch_id    TEXT,
  result_note TEXT,
  updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bdreq_status ON bd_requests(status);

-- 開發信的回信。Cloudflare Email Routing 收到之後寫進來。
CREATE TABLE IF NOT EXISTS bd_replies (
  id          TEXT PRIMARY KEY,
  created_at  TEXT NOT NULL,
  outreach_id TEXT,               -- 對到哪一封開發信
  from_email  TEXT,
  from_name   TEXT,
  subject     TEXT,
  body        TEXT,
  intent      TEXT,               -- interested / not_now / no / auto_reply / unknown
  handled     INTEGER DEFAULT 0,
  handled_by  TEXT,
  reply_draft TEXT,               -- AI 擬的回覆
  updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bdreply_outreach ON bd_replies(outreach_id);

-- 給 bd_outreach 補欄位：從哪一張需求單來的、配到哪一位人選
ALTER TABLE bd_outreach ADD COLUMN request_id TEXT;
ALTER TABLE bd_outreach ADD COLUMN auto_sent INTEGER DEFAULT 0;
