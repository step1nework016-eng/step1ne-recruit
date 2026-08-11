-- 2026-08-04 新增。原本阿財的 applications.status 只到 approved／declined／redirect／done——
-- 也就是「顧問決定要不要往下走」為止。送件給客戶之後的一切（面試、offer、入職、保證期）
-- 資料庫裡連欄位都沒有，所以案子卡在客戶那邊三週不會有任何人發現。
--
-- 欄位不是自己編的，照 pipeline-care-tracker/references/data_model.md 的「核心欄位」與
-- 「階段對照表」建。⚠️ 階段代碼是固定的七個，不要為了某個特殊案子多開一個——
-- 代碼一多，停滯偵測規則就對不起來。

CREATE TABLE IF NOT EXISTS placements (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  -- ⚠️ 一定要 TEXT。applications.id 是 UUID 字串（例如 f89121c4-3ea5-…），不是整數。
  -- 2026-08-04 第一版建成 INTEGER，插進去會被 SQLite 悄悄轉型，之後 JOIN 對不起來。
  application_id TEXT,                        -- 對得回阿財的 applications；顧問自己找的人可為空
  candidate_name TEXT NOT NULL,
  job_slug       TEXT,
  job_title      TEXT NOT NULL,               -- data_model：必要欄位
  client_name    TEXT NOT NULL,               -- data_model：必要欄位

  -- 只能是這七個：SUBMITTED / INTERVIEWING / OFFER_PENDING / ONBOARDING /
  --              GUARANTEE / CLOSED_WON / CLOSED_LOST
  stage          TEXT NOT NULL DEFAULT 'SUBMITTED',
  -- 「進入本階段的日期」。data_model 明說這是唯一「沒有就真的做不到」停滯偵測的欄位。
  stage_since    TEXT NOT NULL DEFAULT (date('now','+8 hours')),
  owner          TEXT,                        -- 負責顧問，單人使用可留空
  next_followup  TEXT,                        -- 顧問自己排的下次跟進日；有值就優先，不要被系統算的蓋掉

  onboard_date   TEXT,                        -- 入職日，保證期從這天起算
  guarantee_days INTEGER,                     -- 30／60／90／120，依商務條件
  care_log       TEXT,                        -- 已關懷過的日期，JSON 陣列，避免同一時間點重複提醒

  note           TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  updated_at     TEXT NOT NULL DEFAULT (datetime('now','+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_placements_stage ON placements(stage, stage_since);
CREATE INDEX IF NOT EXISTS idx_placements_client ON placements(client_name);

-- 接觸紀錄。補流程圖上那兩個紫色破口：
--   B5 實際接觸客戶——沒有任何地方記錄接觸過誰、談到哪一步
--   P7 實際發出接觸給候選人——發了誰、誰回了、誰已讀不回，都沒記
-- 兩者結構一樣，用 target_type 區分，不要開成兩張表（查「這個人碰過幾次」時才不用 union）。
CREATE TABLE IF NOT EXISTS outreach (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  target_type TEXT NOT NULL,                  -- 'client'（開發客戶）或 'candidate'（接觸人選）
  target_name TEXT NOT NULL,
  company     TEXT,
  job_slug    TEXT,                           -- 為了哪個職缺接觸的，開發客戶時可為空
  channel     TEXT,                           -- email／linkedin／104／phone／intro／line
  sent_at     TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  -- sent：發出去了　replied：有回　no_reply：已讀不回或沒反應　declined：明確婉拒　meeting：約到了
  outcome     TEXT NOT NULL DEFAULT 'sent',
  replied_at  TEXT,
  note        TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now','+8 hours'))
);
CREATE INDEX IF NOT EXISTS idx_outreach_target ON outreach(target_type, target_name);
CREATE INDEX IF NOT EXISTS idx_outreach_outcome ON outreach(outcome, sent_at);
