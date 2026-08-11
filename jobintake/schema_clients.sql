-- 客戶關係名單：反向開發（MPC）之前一定要比對的那張表。
--
-- 為什麼需要：在這之前，系統裡唯一的客戶資料是 jobs.client_name，
-- 也就是「已經有職缺頁的客戶」。但真正會出事的是另外三種，它們一個都不在裡面：
--
--   1. 洽談中還沒簽約的（2026-08-11：AI 提議去敲帆宣，顧問正在跟他們談簽約）
--   2. 客戶的終端客戶（2026-08-11 之前：AI 提議去敲台灣美光，那是律准的終端客戶）
--   3. 明確不能碰的（同業、關係人、談崩過的）
--
-- 這三種在 D1 裡沒有任何紀錄，所以 agent 查不到，只能靠人記得——
-- 而人不會每次都記得。這張表就是要讓「記得」變成系統的事。
CREATE TABLE IF NOT EXISTS clients (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,          -- 正式全名
  aliases       TEXT,                   -- 一行一個別名／簡稱／英文名
  relation      TEXT NOT NULL,          -- signed / negotiating / end_client / past / prospect / blocked
  blocked_reason TEXT,                  -- 為什麼不能碰（end_client 要寫是誰的終端客戶）
  via_client    TEXT,                   -- end_client 專用：透過哪一家接觸到的
  owner         TEXT,                   -- 負責顧問
  note          TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clients_relation ON clients(relation);

-- 反向開發的開發紀錄。同一家公司敲過就不要再敲第二次，
-- 也讓顧問看得到「這個人選我們送去哪幾家了」。
CREATE TABLE IF NOT EXISTS bd_outreach (
  id            TEXT PRIMARY KEY,
  created_at    TEXT NOT NULL,
  batch_id      TEXT,                   -- 同一位人選的一輪開發
  candidate_ref TEXT,                   -- 內部代號，不寫姓名
  candidate_card TEXT,                  -- 匿名人選卡
  company       TEXT NOT NULL,
  why_company   TEXT,                   -- 為什麼挑這家
  contact_name  TEXT,
  contact_email TEXT,
  subject       TEXT,
  body          TEXT,
  guard_json    TEXT,                   -- 客戶名單比對結果
  status        TEXT NOT NULL DEFAULT 'draft',  -- draft/pending/approved/sent/rejected/blocked
  decided_by    TEXT,
  decided_at    TEXT,
  sent_at       TEXT,
  reply_note    TEXT,
  tg_message_id INTEGER,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bd_status ON bd_outreach(status);
CREATE INDEX IF NOT EXISTS idx_bd_company ON bd_outreach(company);
