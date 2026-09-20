# Step1ne Pre-call Production Integration Pack v2.0

> 日期：2026-09-17
> 狀態：Phase 1 正式規格包
> 對象：Claude Code / Codex / Backend / Frontend

## 閱讀順序

1. `audit/PRECALL_INTEGRATION_AUDIT.md`
2. `docs/00_Step1ne_候選人快速電洽公式_v1.0.md`
3. `docs/06_ADHD_UIUX規格_v1.0.md`
4. `docs/07_Production_AI_Output_Schema_v1.0.md`
5. `docs/08_API_Adapter_Contract_v1.0.md`
6. `docs/09_開發交接與QA_v1.0.md`
7. `docs/01～05` 作 AI 行為細節參考
8. `references/UI_REFERENCE_PRECALL_CARD.html`

## Phase 1

只做 Read-only Pre-call Card。

禁止自行進入：
- Call Mode DB 寫入
- Gate Result persistence
- Post-call Decision
- Pipeline 更新
- Candidate Forward
- Talent Pool routing
- Coaching

## Source of Truth

- Job：`jobs`
- Candidate × Job：`applications`
- Resume：既有 resume extraction
- Existing fallback：`applications.call_prep_md`

## 正式 Contract

- Data：`07_Production_AI_Output_Schema_v1.0.md`
- API：`08_API_Adapter_Contract_v1.0.md`
- QA / Handoff：`09_開發交接與QA_v1.0.md`

## 正式欄位名稱

Question → Gate：
- `validates_gate_id`
- `why_it_matters`

不要再使用：
- `validates`
- `validates_gate`
- `why`

## Status Mapping

- `pass` → `matched`
- `partial` → `unknown`
- `unknown` → `unknown`

`partial` 不得直接轉 `unmatched`。

## Pending Product Decisions

仍需專案 owner 最終確認：
1. Phase 1 是否立即新增 `applications.precall_card_json` 作 cache
2. 顧問修改 AI 問題是否 Phase 1 即支援
3. 電話前是否保留弱化版「下一步路徑」
4. `partial` UI copy 最終字樣

在未確認前，不要自行擴張 scope。
