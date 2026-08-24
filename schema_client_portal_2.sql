-- 用人需求表 portal 第二批：聯絡人稱呼、寄信紀錄
ALTER TABLE client_companies ADD COLUMN contact_name TEXT;    -- 對方窗口稱呼，例如「林小姐」
ALTER TABLE client_companies ADD COLUMN last_emailed_at TEXT; -- 上次寄送補件連結的時間
