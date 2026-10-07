-- 2026-10-07 顧問助理（TG「Step1ne AI 顧問室」的🧑‍💼顧問助理 topic）
-- 只新增表，不動任何既有資料。
--
-- consultant_ops_actions：顧問助理產生的「待確認動作」。
--   助理（Mac 上的 consultant_ops.py）只能建立 status='pending' 的列並貼出確認卡；
--   真正寄信／寫進人選卡片，只發生在 step1ne-recruit Worker 的 TG 按鈕 callback（cop_ok）。
--   誰按的記在 decided_by / decided_tg_id。
CREATE TABLE IF NOT EXISTS consultant_ops_actions (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,              -- call_mail | call_confirm | decline_mail | call_summary | book_call
  application_id TEXT,
  candidate_name TEXT,
  params_json TEXT,                -- 送給後台端點的參數（寄出時原樣再送一次）
  preview_to TEXT,
  preview_subject TEXT,
  preview_text TEXT,               -- 顧問在確認卡上看到的內容；寄出前會再比對一次
  requested_by TEXT,               -- 顧問顯示名（Jacky／Phoebe…），信件署名用
  requested_tg_id TEXT,
  requested_tg_username TEXT,
  chat_id TEXT,
  thread_id TEXT,
  tg_message_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | sending | done | failed | cancelled | expired | stale
  decided_by TEXT,
  decided_tg_id TEXT,
  decided_at TEXT,
  result_json TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cops_actions_app ON consultant_ops_actions(application_id, created_at);
CREATE INDEX IF NOT EXISTS idx_cops_actions_status ON consultant_ops_actions(status, created_at);

-- consultant_reminders：電話提醒（預設提前 30 分鐘）。
--   step1ne-backoffice-worker 的 */10 cron 會撿 remind_at 到了的 pending 列發 TG，
--   先原子改成 sending 再發，多台／重試都不會重複發。取消＝改 status='cancelled'，不刪列。
CREATE TABLE IF NOT EXISTS consultant_reminders (
  id TEXT PRIMARY KEY,
  application_id TEXT,
  candidate_name TEXT,
  call_at TEXT NOT NULL,           -- 台北時間 'YYYY-MM-DD HH:MM'
  remind_at TEXT NOT NULL,         -- 台北時間 'YYYY-MM-DD HH:MM'
  message TEXT,
  target_chat_id TEXT,             -- 預設發到提出的顧問本人私訊
  fallback_chat_id TEXT,           -- 私訊發不出去（沒跟 bot 說過話）時改發這裡
  fallback_thread_id TEXT,
  requested_by TEXT,
  requested_tg_id TEXT,
  source_action_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | sending | sent | failed | cancelled
  sent_at TEXT,
  sent_to TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  cancelled_at TEXT,
  cancelled_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_cops_rem_due ON consultant_reminders(status, remind_at);
CREATE INDEX IF NOT EXISTS idx_cops_rem_call ON consultant_reminders(call_at);
