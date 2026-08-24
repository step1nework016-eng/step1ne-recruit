-- 用人需求表統一流程 — 第一步：讓 jobs 表放得下完整的用人需求表資料
--
-- 背景：真實的用人需求表（見 ~/Downloads/Telegram Desktop/用人需求表_*.xlsx）
-- 分八大類，比 jobs 表現有欄位豐富很多——尤其薪資不是單一數字，是依學歷／科系
-- 分級、派遣期間再攤提的一張對照表。這裡只新增欄位，不動任何既有欄位，
-- 也不寫任何同步邏輯——那部分（Google Sheet 補齊、企業客戶回填、自動回寫）
-- 是下一步，需要另外設計，這次先不做。
--
-- 全部用 ALTER TABLE ... ADD COLUMN，SQLite 這樣做不會動到既有資料，
-- 沒填的欄位就是 NULL，舊資料完全不受影響。
--
-- 執行：
--   set -a; source ~/.config/workflow-os/cf.env; set +a
--   npx wrangler d1 execute step1ne-recruit --remote --file=schema_hiring_requirements.sql

-- 一、職缺基本資料（jobs 已有 client_name/locations/employment，這裡補缺的）
ALTER TABLE jobs ADD COLUMN client_contact_name TEXT;      -- 聯絡窗口
ALTER TABLE jobs ADD COLUMN client_contact_phone TEXT;     -- 聯絡電話
ALTER TABLE jobs ADD COLUMN headcount TEXT;                -- 需求人數（保留文字，範本常是「5名，其中3位…」這種混合敘述）
ALTER TABLE jobs ADD COLUMN work_mode TEXT;                -- 辦公型態：全實體／混合／遠端

-- 二、工作時間與條件
ALTER TABLE jobs ADD COLUMN work_hours TEXT;               -- 上班時間
ALTER TABLE jobs ADD COLUMN leave_policy TEXT;              -- 休假方式
ALTER TABLE jobs ADD COLUMN employment_period TEXT;         -- 工作期間：長期／專案／派遣期間
ALTER TABLE jobs ADD COLUMN overtime_policy TEXT;            -- 加班情形

-- 三、招募主因
ALTER TABLE jobs ADD COLUMN hiring_reason TEXT;              -- 招募原因＋補充
ALTER TABLE jobs ADD COLUMN urgency TEXT;                    -- 急迫程度

-- 四、職務內容（jobs 已有 team_size/interview_who，這裡補缺的）
ALTER TABLE jobs ADD COLUMN main_duties TEXT;                -- 主要工作職責（條列）
ALTER TABLE jobs ADD COLUMN reports_to TEXT;                 -- 直接匯報對象
ALTER TABLE jobs ADD COLUMN leads_team TEXT;                 -- 是否帶團隊、帶多少人

-- 五、用人條件—必要（硬條件）
ALTER TABLE jobs ADD COLUMN education_level TEXT;            -- 學歷要求
ALTER TABLE jobs ADD COLUMN required_conditions TEXT;        -- 必備條件（條列，非有不可）
ALTER TABLE jobs ADD COLUMN language_requirement TEXT;       -- 語言能力

-- 六、用人條件—加分
ALTER TABLE jobs ADD COLUMN nice_to_have_skills TEXT;        -- 加分技能／經驗
ALTER TABLE jobs ADD COLUMN preferred_background TEXT;       -- 偏好背景（科系優先順序等）
ALTER TABLE jobs ADD COLUMN personality_traits TEXT;         -- 期望人格特質

-- 七、薪資與福利（jobs 已有 salary_min/max/unit，這裡補「不是單一數字」的部分）
-- salary_tier_table 存 JSON：依學歷／科系分級 × 派遣攤提後薪資的完整對照表，
-- 例如律准案的「普通大學學士非相關35000／攤提後40833」這種多維度表格，
-- 塞進 salary_min/max 兩個數字會失真，只能用結構化 JSON 存完整表格。
ALTER TABLE jobs ADD COLUMN salary_tier_table TEXT;          -- JSON：[{tier, major_match, base, dispatch_adjusted}, ...]
ALTER TABLE jobs ADD COLUMN salary_structure_note TEXT;      -- 薪資結構說明文字（攤提規則等）
ALTER TABLE jobs ADD COLUMN benefits_detail TEXT;             -- 福利與誘因完整敘述
ALTER TABLE jobs ADD COLUMN dispatch_to_permanent_policy TEXT; -- 派遣／轉正條件與時程

-- 八、其他補充
ALTER TABLE jobs ADD COLUMN off_limits_note TEXT;             -- off-limits／同業限制
ALTER TABLE jobs ADD COLUMN requirement_form_updated_at TEXT; -- 這份用人需求表最後更新時間（跟 jobs.updated_at 分開，方便知道「需求本身」多久沒更新，不是職缺頁面多久沒動）
