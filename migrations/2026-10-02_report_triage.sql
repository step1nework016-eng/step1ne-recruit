-- 2026-10-02 Jacky：人選中心「快速判斷」分頁。顧問看小卡就按 推／要打電話／不推。
-- 只是內部記號：不改 reports.consultant_decision（那個會牽動推薦流程與階段），
-- 也不碰 screen_decision（declined 會觸發婉拒信）。一個應徵一筆，按新的蓋掉舊的。
CREATE TABLE IF NOT EXISTS report_triage (
  application_id TEXT PRIMARY KEY,
  decision       TEXT NOT NULL,          -- push / call / no
  decided_by     TEXT,
  decided_at     TEXT NOT NULL,
  note           TEXT
);
