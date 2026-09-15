-- Step1ne 週文章草稿審核閉環。2026-09-14 Jacky 要求「給我審核」，
-- 跟 social_post_queue 同一套模式（草稿 → TG 按鈕核准 → 本機真的套用上線）。
CREATE TABLE IF NOT EXISTS article_drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_slug TEXT,           -- 選題來源的職缺 slug（給人看，不一定等於 related_job_slug）
  article_slug TEXT,       -- 文章網址 slug（/articles/<article_slug>/）
  title TEXT,
  description TEXT,
  keywords TEXT,
  og_title TEXT,
  og_description TEXT,
  related_job_slug TEXT,   -- 文章結尾要連去的職缺頁
  draft_md TEXT,           -- 全文（含 front matter 以外的正文＋FAQ＋查證來源）
  status TEXT,             -- drafted / approved / published / skipped / error
  tg_message_id TEXT,
  tg_thread_id TEXT,
  requested_at TEXT,
  approved_at TEXT,
  approved_by TEXT,
  published_at TEXT,
  url TEXT,
  error TEXT
);
