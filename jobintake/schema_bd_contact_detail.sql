-- 開發進度看板企業卡片要用的聯絡細節，只新增，不動既有欄位。
--
-- 2026-09-29 Jacky 要求：寄信後要走「打電話追」，call_phone 之前只有純電訪
-- 管道的公司在填，寄信管道的公司這欄大多是空的。source_url／company_104_url
-- 是讓顧問點開企業卡片時知道「這個窗口／信箱是從哪一頁查到的」，可以自己
-- 再確認，不是只能信我們寫的。
--
-- call_phone     人資專線或總機（跟電訪管道共用同一欄，不分 email/phone）
-- source_url     當初找到這支電話／信箱的那個公開頁面（校園徵才公告、公司
--                官網、104企業頁等），查不到就留空，不要編
-- company_104_url 該公司的 104 企業頁網址，查得到就放，查不到留空
ALTER TABLE bd_outreach ADD COLUMN source_url TEXT;
ALTER TABLE bd_outreach ADD COLUMN company_104_url TEXT;
