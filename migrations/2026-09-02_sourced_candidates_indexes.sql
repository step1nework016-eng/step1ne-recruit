-- 2026-09-02 加：/admin/sourced GET 每次呼叫要跑 9+ 支 GROUP BY／COUNT
-- 查詢（status/category/source/job_slug/grade/recruitability_class 各一個
-- facet），sourced_candidates 表本身沒有索引，這些查詢都是全表掃描——
-- D1 是照「掃了幾列」算讀取額度，這是 2026-09-01 D1 免費方案每日讀取
-- 額度被打到 2200 萬筆那次稽核出來的第三個發現（排序中高）。
--
-- 這裡只加索引，不改任何查詢邏輯本身——WHERE／GROUP BY 用到的欄位有索引
-- 之後，D1 可以直接用索引算 COUNT，不用整張表掃過一遍，風險最低。
--
-- 執行方式（D1 額度重置、確認正常後再跑）：
--   cd ~/工作流程技能包/step1ne-recruit
--   npx wrangler d1 execute step1ne-recruit --remote --file migrations/2026-09-02_sourced_candidates_indexes.sql

CREATE INDEX IF NOT EXISTS idx_sourced_status ON sourced_candidates(status);
CREATE INDEX IF NOT EXISTS idx_sourced_category ON sourced_candidates(category);
CREATE INDEX IF NOT EXISTS idx_sourced_source ON sourced_candidates(source);
CREATE INDEX IF NOT EXISTS idx_sourced_job_slug ON sourced_candidates(job_slug);
CREATE INDEX IF NOT EXISTS idx_sourced_grade ON sourced_candidates(grade);
CREATE INDEX IF NOT EXISTS idx_sourced_rc ON sourced_candidates(recruitability_class);
-- 主列表排序＋分頁用到的欄位，加複合索引讓 ORDER BY ... LIMIT ... OFFSET
-- 也能吃到索引，不用先把符合條件的列全掃出來再排序。
CREATE INDEX IF NOT EXISTS idx_sourced_status_created ON sourced_candidates(status, created_at DESC);
