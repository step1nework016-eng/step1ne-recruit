-- 2026-09-15 加：兩件事都是顧問在真實操作弘昌 BIM 案時發現系統缺的。
--
-- 1) 不報到（placements 新增 2 欄）：候選人已經走到「報到」這一關、雙方談好日期，
--    結果沒出現。這跟既有的「結案不續」(mark-closed / close_reason) 是不同語意——
--    close_reason 是顧問寫給候選人看的委婉說法，會直接組進候選人收到的訊息；不報到
--    發生在候選人事後失聯或直接放鴿子的情境，顧問要記的是「內部備忘」，不該也不會
--    送任何訊息給候選人。刻意分開兩個欄位，不要共用 close_reason，避免不小心把內部
--    紀錄送出去。
--
-- 2) 收費排程（client_companies 新增 1 欄＋新表 placement_billing_installments）：
--    正職代招服務常見「報到滿30天收50%、3個月收25%、6個月收25%」這種分期收款條款，
--    每家客戶合約寫的比例/天數都不同（弘昌是這個例子，不代表所有客戶都一樣）。
--    billing_schedule_json 存在 client_companies（比照 jobs.salary_tier_table 已有的
--    JSON 分級表做法，這個專案已經在用這套模式，不用發明新的）：
--      [{"seq":1,"label":"第一期","percent":50,"trigger_unit":"days","trigger_value":30},
--       {"seq":2,"label":"第二期","percent":25,"trigger_unit":"months","trigger_value":3},
--       {"seq":3,"label":"第三期","percent":25,"trigger_unit":"months","trigger_value":6}]
--    真正「這個人選這一期該不該收、收了沒」記在 placement_billing_installments——
--    在候選人報到（onboard_date 寫入）當下，依當時 client_companies 的範本快照展開成
--    一筆一筆，之後改範本不會回頭改已經展開的舊人選（合約是簽當下那份，不是永遠跟最新設定走）。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=schema_billing_and_no_show.sql

ALTER TABLE placements ADD COLUMN no_show_at TEXT;       -- 標記不報到的時間，NULL＝沒發生過
ALTER TABLE placements ADD COLUMN no_show_reason TEXT;   -- 顧問內部備忘，絕不送給候選人

ALTER TABLE client_companies ADD COLUMN billing_schedule_json TEXT; -- NULL＝這家客戶沒設定收費排程

CREATE TABLE IF NOT EXISTS placement_billing_installments (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  placement_id  INTEGER NOT NULL,   -- 對應 placements.id
  seq           INTEGER NOT NULL,   -- 第幾期，展開當時從 billing_schedule_json 複製，之後範本再改也不影響這筆
  label         TEXT NOT NULL,
  percent       REAL NOT NULL,
  due_date      TEXT NOT NULL,      -- 依 onboard_date + trigger 算出的到期日，YYYY-MM-DD
  paid_at       TEXT,               -- NULL＝未收款
  paid_by       TEXT,               -- 標記已收款的顧問
  note          TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_billing_installments_placement ON placement_billing_installments(placement_id);
