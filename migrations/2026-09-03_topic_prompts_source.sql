-- category 欄位已經有明確用途（一鍵發文的話題類型：general／ai），
-- topic_research_tick.py 一開始誤把「這是AI自動研究產生的」也塞進
-- category='AI研究'，跟既有的 general/ai 枚舉值衝突，會讓這筆話題
-- 在「一鍵發文」的話題下拉選單裡完全選不到（category 對不上）。
-- 改成獨立欄位追蹤來源，category 維持給既有邏輯專用。
ALTER TABLE topic_prompts ADD COLUMN source TEXT DEFAULT 'manual';
