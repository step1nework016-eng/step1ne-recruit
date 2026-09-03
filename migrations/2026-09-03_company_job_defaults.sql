-- 用人單位反覆說「公司簡介／聯絡窗口這種每個職缺都一樣的東西，
-- 為什麼每次開新職缺都要重打一次」——加三個「公司預設值」欄位，
-- 客戶在任一職缺填過這些欄位就順手存回這裡，下次開新職缺自動帶入。
-- 跟 jobs.client_intro／client_contact_name／client_contact_phone
-- 是各自獨立的資料：這裡只是「預設值來源」，每個職缺存檔後還是可以
-- 個別覆蓋，不是鎖死共用同一份。
ALTER TABLE client_companies ADD COLUMN default_intro TEXT;
ALTER TABLE client_companies ADD COLUMN default_contact_name TEXT;
ALTER TABLE client_companies ADD COLUMN default_contact_phone TEXT;
