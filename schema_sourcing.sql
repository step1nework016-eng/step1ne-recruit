-- Step1ne 免費候選人搜尋規則引擎 —— 基礎設施表
-- 2026-08-23 建立。對應 SOURCING_RULES.md 的防偷懶機制：
-- 每一次 sourcing session 一筆 sourcing_runs，每一組 query 一筆 search_audit_log，
-- 讓「這次到底有沒有真的搜過」可以被回放證明，而不是只有 AI 自己說有搜過。
--
-- 這份 .sql 檔案是留存記錄用（跟 repo 裡其他 schema_*.sql 一致），
-- 實際建表已經直接對 D1 remote 執行過，這裡不需要再跑一次
-- （CREATE TABLE IF NOT EXISTS 是安全的，重跑也不會壞事）。

CREATE TABLE IF NOT EXISTS sourcing_runs (
    sourcing_run_id TEXT PRIMARY KEY,      -- 格式：STEP1NE-{JOB_SLUG縮寫}-{YYYYMMDD}-{三位數序號}
    target_job TEXT,
    started_at TEXT,
    finished_at TEXT,
    search_strategy_version TEXT,
    query_family_count INTEGER,            -- 用了幾種 Query Family（EXACT_TITLES / ADJACENT_TITLES / SKILL_RESPONSIBILITY / TARGET_COMPANY）
    query_variant_count INTEGER,            -- 總共跑了幾組 query
    source_type_count INTEGER,              -- 用了幾類來源（Search Engine / 官網 / LinkedIn / Cake / Conference / 協會 / 新聞 / 年報ESG / PDF）
    candidate_chain_attempted INTEGER,       -- 0/1，有沒有做過 seed expansion（從已找到的人/公司往外展開）
    diminishing_return_condition INTEGER,    -- 0/1，是否已達邊際效益遞減（連續多組新增趨近於0）
    status TEXT,                            -- SEARCH_SATURATED / SEARCH_INCOMPLETE
    candidates_discovered_count INTEGER,
    evidence_sufficient_count INTEGER,
    match_candidate_count INTEGER,
    stop_reason TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS search_audit_log (
    id TEXT PRIMARY KEY,
    sourcing_run_id TEXT,                   -- FK → sourcing_runs.sourcing_run_id（邏輯外鍵，D1/SQLite 不強制）
    query TEXT,                             -- 實際下的搜尋字串，原文照抄，不摘要
    query_family TEXT,                      -- EXACT_TITLES / ADJACENT_TITLES / SKILL_RESPONSIBILITY / TARGET_COMPANY
    result_depth INTEGER,                   -- 這組 query 實際檢查到第幾筆結果 / 第幾頁
    results_checked INTEGER,                -- 這組 query 總共看了幾筆
    candidates_discovered TEXT,              -- JSON 字串陣列，這組 query 新發現的候選人
    duplicates INTEGER,                     -- 這組 query 撞到幾個已存在的候選人
    noise INTEGER,                          -- 分類為 NOISE 的結果數
    auth_required INTEGER,                  -- 分類為 AUTH_REQUIRED 的結果數（LinkedIn/Cake 登入牆）
    new_evidence TEXT,                       -- JSON 字串陣列，這組 query 補到的新證據（不一定是新候選人，也可能是既有候選人的新事實）
    started_at TEXT,
    finished_at TEXT
);
