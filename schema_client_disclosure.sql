-- 客戶揭露與候選人二次同意（2026-08-10 上線執行）
--
-- 三個問題各補一欄：
-- 1. 初步面談是 AI 進行，要不要跟客戶講？各客戶態度不同，不能寫死。
-- 2. 客戶還沒簽約時，職缺頁不能寫出公司名，但系統原本沒有地方記這件事。
-- 3. 網站個資說明寫著「提供給用人企業前會另外徵得您的同意」，
--    但沒有欄位記錄這個同意——等於現在按下推薦給客戶就違反自己的條款。
--
-- 以下已在 remote D1 (step1ne-recruit) 實際執行完成，保留於此供重建與查閱。
-- 全部是 ADD COLUMN，沒有重建任何表。

ALTER TABLE jobs ADD COLUMN ai_disclosure TEXT;
  -- always    = 一律對客戶揭露初步面談由 AI 進行
  -- by_client = 依個案決定，上架前先問顧問
  -- never     = 不揭露，流程改用中性描述
  -- NULL      = 尚未決定，不要預設成 always

ALTER TABLE jobs ADD COLUMN client_named INTEGER;
  -- 1 = 已與客戶簽約，職缺頁可具名
  -- 0 = 尚未簽約，必須匿名（改用產業描述，見 step1ne-job-posting 技能 Phase 2）

ALTER TABLE jobs ADD COLUMN client_code TEXT;
  -- 內部客戶代號，用來區分不同客戶的同名職缺。
  -- ⚠️ 絕不對外顯示：不進頁面 HTML／schema／網址／職缺卡／申請表選單／
  --    候選人信件／AI 面談逐字稿。只出現在顧問後台與內部報表。

ALTER TABLE applications ADD COLUMN client_consent_at TEXT;
  -- 候選人同意「把資料提供給特定用人企業」的時間（台北時間字串）。
  -- 與 consent_at（一般個資告知同意）不同：那是收資料的同意，
  -- 這是對外提供的同意。推薦給客戶前必須有值。

-- 回填（2026-08-10，依 Jacky 指示）：
--   UPDATE jobs SET ai_disclosure='never',  client_named=0 WHERE slug='bim-engineer';
--   UPDATE jobs SET ai_disclosure='always', client_named=1 WHERE slug<>'bim-engineer' AND status<>'closed';
-- client_code 這次未填，全部維持 NULL。
-- 兩筆 status='closed' 的職缺（game-phone-cs-evening-zhonghe、mobile-game-cs-neihu-mid）
-- 不在回填範圍，維持 NULL，待顧問確認。
