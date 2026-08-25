-- 招募形式快速評估工具（enterprise.step1ne.com）2026-08-25 簡化重做後的提交紀錄
--
-- 舊版評估工具用 hm_cases 那張表，payload 是一整包給 AI 引擎用的複雜 JSON
-- （fields/review/assessment...）。2026-08-25 整套重做成純前端固定規則、
-- 拿掉 AI 分析後，那個 payload 形狀完全用不上了——硬塞進 hm_cases 只會讓
-- 兩邊誰在讀寫哪個欄位變得混亂。改開一張跟新工具資料形狀一致的小表，
-- 欄位直接對應表單四個欄位＋三題答案＋判斷結果，query 起來也單純。
CREATE TABLE IF NOT EXISTS assessment_submissions (
  id           TEXT PRIMARY KEY,
  company_name TEXT NOT NULL,
  industry     TEXT NOT NULL,
  job_title    TEXT NOT NULL,
  salary_text  TEXT NOT NULL,
  duration     TEXT NOT NULL,   -- long_term / project_based
  employer     TEXT NOT NULL,   -- direct_hire / step1ne_hire
  talent_type  TEXT NOT NULL,   -- general / scarce
  mode         TEXT NOT NULL,   -- dispatch / contract / headhunt / direct_recruit
  mode_label   TEXT NOT NULL,   -- 中文結果，顧問後台直接顯示用
  status       TEXT NOT NULL DEFAULT 'new',  -- new / contacted / closed，顧問手動更新
  created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assessment_submissions_created
  ON assessment_submissions(created_at DESC);
