-- 候選人歷程事件（只加不改的流水帳）
--
-- 為什麼要有這張表：2026-08-06 顧問問「阿財可以面但人選拒絕、顧問判斷不符合，
-- 這些狀況也會有紀錄吧？」——查了才發現**沒有地方記**。
-- 現況是狀態散在四個欄位、兩張表：
--   applications.status / interview_state / screen_decision、reports.consultant_decision、placements.stage
-- 而且**沒有任何一欄能寫「為什麼掉的」**。
--
-- 流失原因才是有價值的東西。7 個人裡 5 個卡在未送件，
-- 如果不知道是「顧問還沒看」還是「人選拒絕」還是「條件不符」，就永遠不知道該修哪裡。
--
-- 設計原則：
-- 1. **只加不改。** 每次狀態變動就 INSERT 一筆，不 UPDATE 舊的。
--    候選人會來回（拒絕後又回心轉意、被刷掉後轉推別的缺），用單一狀態欄記不住這些。
-- 2. **流失一定要有原因。** reason 是流失事件的必填欄，不知道就寫「未知」，
--    不要留空——留空跟「還沒問」分不出來。
-- 3. **誰記的要留下。** 顧問記的、阿財自動記的、總指揮代記的，事後查得出來。

CREATE TABLE IF NOT EXISTS pipeline_events (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id TEXT,                    -- 對應 applications.id（TEXT UUID）
  candidate_name TEXT NOT NULL,           -- 冗餘存一份，人名比 id 好查
  job_slug       TEXT,
  stage          TEXT NOT NULL,           -- 發生在漏斗的哪一段，見下方
  event          TEXT NOT NULL,           -- pass 往前／drop 掉了／hold 卡住／back 回頭
  reason         TEXT,                    -- **drop 一定要填**，見下方
  detail         TEXT,                    -- 顧問講的原話，越具體越好
  recorded_by    TEXT,                    -- 誰記的（顧問名字／阿財／總指揮）
  happened_at    TEXT NOT NULL DEFAULT (datetime('now','+8 hours')),
  created_at     TEXT NOT NULL DEFAULT (datetime('now','+8 hours'))
);

CREATE INDEX IF NOT EXISTS idx_pe_app    ON pipeline_events(application_id);
CREATE INDEX IF NOT EXISTS idx_pe_name   ON pipeline_events(candidate_name);
CREATE INDEX IF NOT EXISTS idx_pe_stage  ON pipeline_events(stage);
CREATE INDEX IF NOT EXISTS idx_pe_when   ON pipeline_events(happened_at);

-- ── stage 的值（漏斗順序）──
--   applied            投遞進來
--   screened           AI 初篩
--   consultant_review  顧問看報告、決定要不要推
--   interview_invited  已發面談邀請給人選
--   interviewed        阿財面談完成
--   submitted          送件給客戶
--   client_interview   客戶面試
--   offer              客戶發 offer
--   onboard            到職
--   guarantee          保證期追蹤

-- ── event 的值 ──
--   pass  這一段通過，往下一段走
--   drop  在這一段掉了（**reason 必填**）
--   hold  卡住／等待中（例如等客戶回覆、等人選考慮）
--   back  回頭（例如被刷掉之後改推別的缺）

-- ── reason 的值（只在 drop / hold 時填）──
-- 人選端：
--   candidate_declined      人選拒絕（拒面談、拒 offer 都算，detail 寫清楚是哪一種）
--   candidate_no_response   人選沒回應／失聯
--   candidate_other_offer   去了別家
--   candidate_left_early    保證期內離職
-- 顧問端：
--   consultant_rejected     顧問判斷不符合
--   consultant_pending      顧問還沒處理（**這個要特別留意，是我們自己卡住**）
-- 客戶端：
--   client_rejected         客戶不要
--   client_cancelled        客戶取消職缺／凍結
--   client_no_response      客戶沒回覆
-- 條件面：
--   salary_gap              薪資談不攏
--   location_gap            地點或通勤問題
--   shift_gap               班別作息配不上
--   experience_gap          經驗不足
--   info_incomplete         資料不齊，還不能判斷
--   unknown                 就是不知道（**不要為了填而編一個原因**）
