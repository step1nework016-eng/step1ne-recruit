-- 2026-10-22 社群草稿「今天想搭的話題／關鍵字」（選填，例：颱風天看職缺）
-- 顧問排稿時填，social_post_agent.py 產稿時當開頭鉤子用；重新產一次沿用同一列所以會保留。
-- NULL＝沒填，產稿行為跟以前完全一樣。
ALTER TABLE social_post_queue ADD COLUMN topic_hook TEXT;
