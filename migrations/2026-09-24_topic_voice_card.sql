-- 2026-09-24：話題語氣卡（topic_voice_card）
-- 只用在話題文（process_topic → generate_draft_topic）。招募文照舊用 voice_card。
-- 第一版只能手寫：topic_voice_card_src='manual' 且內容不是空的才生效；
-- 沒有話題卡 → 話題文沿用 voice_card（跟加這兩欄之前一模一樣）。
ALTER TABLE social_accounts ADD COLUMN topic_voice_card TEXT;
ALTER TABLE social_accounts ADD COLUMN topic_voice_card_src TEXT;
