-- 2026-09-02：加一個顧問端可見度開關——Jacky 要求先把台灣美光、帆宣系統科技
-- 這兩家客戶的資訊隱藏，顧問後台（客戶清單、推薦給客戶下拉、報告頁客戶選單，
-- 都共用 /admin/portal/companies）暫時只看得到弘昌管理顧問。之後要恢復能見度，
-- 把對應那列 hidden_from_consultants 改回 0 就好，不用刪資料。
--
-- 已經在 remote D1 直接執行過（ALTER + UPDATE），這份檔案只是留紀錄，之後
-- 重建資料庫時 schema 才不會漏掉這個欄位。
ALTER TABLE client_companies ADD COLUMN hidden_from_consultants INTEGER DEFAULT 0;

UPDATE client_companies SET hidden_from_consultants = 1
 WHERE id IN ('co_blk_micron', 'co_blk_marketech');
