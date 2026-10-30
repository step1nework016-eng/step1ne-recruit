-- 2026-10-30：串文發文進度（排程貼文 #1081 發到第 1 則就被中斷，之後沒辦法知道發到哪）。
-- post_progress：JSON {k: 內容指紋, done: 已發則數, first: 第一則 id, last: 最後一則 id}
-- post_attempts：這次發文第幾次嘗試（排程自動續發最多 3 次，超過交給卡住對帳）
ALTER TABLE social_post_queue ADD COLUMN post_progress TEXT;
ALTER TABLE social_post_queue ADD COLUMN post_attempts INTEGER;
