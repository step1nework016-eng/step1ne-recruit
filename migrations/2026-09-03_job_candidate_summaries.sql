-- 候選人在社群貼文下留言問職缺，顧問要私訊回覆——這張表存的是
-- 「第一輪職缺摘要」預先產好的版本，顧問回覆時直接複製，不用每次
-- 現場叫AI重寫一次。LINE連結故意留佔位符{{LINE_LINK}}，因為內容
-- 職缺無關的部分（摘要本身）不分顧問，只有結尾要接的LINE OA連結
-- 因人而異，前端複製時再把佔位符換成當下選定的顧問帳號的line_link。
CREATE TABLE IF NOT EXISTS job_candidate_summaries (
  job_slug TEXT PRIMARY KEY,
  summary_text TEXT,
  generated_at TEXT
);
