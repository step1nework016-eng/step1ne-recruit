-- 資料模型合併 Phase 1：客戶/顧問改「事實」欄位（PORTAL_FIELDS）後，
-- 自動觸發背景腳本用 AI 把行銷包裝文案（duties/must/plus/why/benefits/faq
-- 這些頁面要的結構化欄位）從事實重新生成，不用顧問手動謄一次。
-- 這個欄位是那個自動化流程的觸發旗標。
ALTER TABLE jobs ADD COLUMN jd_needs_ai_draft INTEGER DEFAULT 0;
