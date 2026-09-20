# 07｜Production AI Output Schema / JSON Contract v1.0

> 模組：Step1ne Pre-call Integration  
> 用途：正式定義 Production 環境中 Pre-call Card 的 AI 結構化輸出、欄位名稱、狀態 mapping、runtime/cache/DB 邊界，以及前後端共用 Contract。  
> 依據：`PRECALL_INTEGRATION_AUDIT.md` 與規格 00～06。  
> 核心原則：**不建立第二套 Job / Candidate / Pipeline；本 Contract 只負責把既有 Production 資料轉成 Pre-call UI 可直接使用的結構。**

---

# 1｜Production Source of Truth

本 Contract 必須遵守以下 Source of Truth：

## Job

使用既有：

`jobs`

主鍵：

`slug`

相關欄位包含：

- `title`
- `required_conditions`
- `must_skills`
- `hard_filters`
- `must_check_items`
- `client_screen_conditions`
- `main_duties`
- `nice_to_have_skills`
- `preferred_background`
- `personality_traits`
- `salary_min`
- `salary_max`
- `salary_unit`
- `salary_tier_table`
- `locations`
- `work_mode`
- `work_hours`
- `employment`
- `onboard_by`
- `urgency`
- `interview_process`

不可建立第二份 Job / JD 資料。

---

## Candidate × Job

使用既有：

`applications`

主識別：

`application_id`

職缺關聯：

`job_slug`

候選人本身目前沒有獨立 `candidates` 主表。

因此 Pre-call Card 必須以：

```text
application_id
+
job_slug
```

作為主要 context。

---

# 2｜Contract Scope

此 Contract 定義：

1. Pre-call Card runtime output
2. AI derived candidate summary
3. Call Goal
4. Hard Gate
5. Must Ask Questions
6. AI Flag
7. 前端顯示需要的 metadata
8. Production status mapping
9. cache / persistence 邊界
10. Phase 2 / Phase 3 的預留欄位

Phase 1 主要使用：

- Candidate Summary
- Call Goal
- Hard Gates
- Must Ask Questions
- AI Flags

---

# 3｜Root Object

正式 root：

```json
{
  "schema_version": "1.0",
  "application_id": "",
  "job_slug": "",
  "generated_at": "",
  "source_fingerprint": "",

  "candidate_summary": {},
  "job_context": {},
  "call_goal": {},
  "hard_gates": [],
  "must_ask_questions": [],
  "ai_flags": [],

  "meta": {}
}
```

---

# 4｜schema_version

固定：

```json
"schema_version": "1.0"
```

用途：

- 前端辨識版本
- 後續 migration
- 避免欄位改名造成 silent failure

---

# 5｜application_id

來源：

Production `applications`

不可由 AI 產生。

格式：

沿用 Production 現有 ID 型別。

用途：

- 候選人 context
- API path
- cache key
- 後續 Call Mode / Decision 關聯

---

# 6｜job_slug

來源：

`applications.job_slug`

→ `jobs.slug`

不可由 AI 推測。

用途：

- Job context
- Hard Gate source
- cache invalidation
- 前端顯示目標職缺

---

# 7｜generated_at

ISO 8601：

```json
"generated_at": "2026-09-17T14:30:00+08:00"
```

用途：

- cache
- UI 顯示「最後更新」
- debug

---

# 8｜source_fingerprint

建議由 Backend 產生。

用途：

判斷：

- Job 是否改過
- Resume 是否更新
- Pre-call cache 是否仍有效

概念：

```text
hash(
  job_slug
  + job updated_at
  + resume version / file id
  + latest relevant transcript id
)
```

AI 不產生 fingerprint。

---

# 9｜candidate_summary

## 定義

電話前快速摘要。

這些欄位大多來自履歷 AI runtime derived。

正式結構：

```json
{
  "name": "",
  "current_role": "",
  "relevant_experience": "",
  "location_summary": "",
  "source_channel": ""
}
```

---

## 9.1 name

來源：

`applications.name`

不可 AI 重寫。

---

## 9.2 current_role

來源：

履歷 runtime derived。

不要新增 DB 欄位。

如果履歷無法判斷：

```json
"current_role": null
```

不要猜。

---

## 9.3 relevant_experience

顯示用途：

例如：

```text
1.5 年 BIM 相關經驗
```

來源：

AI 根據履歷推導。

不要直接持久化。

---

## 9.4 location_summary

重要：

Production `applications.location_ok` 是布林語意。

不可拿它直接當候選人所在地。

本欄位必須：

- 從履歷／候選人已知資料 derived
- 或 null

範例：

```json
"location_summary": "新竹，目前苗栗可談"
```

---

## 9.5 source_channel

來源：

優先：

`applications.source_channel`

其次：

`utm_source`

不可自創。

---

# 10｜job_context

用途：

讓 UI 不需再自行拼 Job 基本資訊。

正式結構：

```json
{
  "title": "",
  "locations": [],
  "salary_summary": "",
  "work_mode": "",
  "work_hours": "",
  "employment": ""
}
```

此區全部由 Backend / Adapter 從 `jobs` 讀取。

AI 不修改 Job Facts。

---

# 11｜call_goal

正式結構：

```json
{
  "decision": "確認是否能推",
  "target_role": "",
  "validation_points": [],
  "reason": ""
}
```

---

## 11.1 decision

固定優先句型：

```text
確認是否能推
```

若非單一職缺：

```text
確認是否適合
```

但 Production Phase 1 預設：

候選人 context 已綁 Job，因此通常使用：

`確認是否能推`

---

## 11.2 target_role

來源：

`jobs.title`

AI 可做顯示簡化，但不可改變職缺語意。

例如：

Production：

`資深職業安全衛生工程師`

UI 可保留完整名稱。

不建議 AI 自行縮成：

`職安`

除非只是 tag。

---

## 11.3 validation_points

正式：

```json
[
  {
    "gate_id": "gate_1",
    "label": "Revit 實作"
  }
]
```

數量：

2～3 個。

禁止只輸出純字串陣列。

理由：

必須與 Hard Gate 建立 stable link。

---

## 11.4 reason

一句。

20～45 字為原則。

用途：

解釋為什麼這幾個 Gate 需要確認。

---

# 12｜hard_gates

正式結構：

```json
[
  {
    "id": "gate_1",
    "label": "",
    "category": "ability",
    "classification": "hard_gate",
    "status": "unknown",
    "source": {
      "type": "jobs.hard_filters",
      "raw_label": ""
    },
    "evidence": "",
    "verify_in_call": true,
    "priority": "high"
  }
]
```

---

# 13｜Hard Gate id

格式建議：

```text
gate_1
gate_2
gate_3
```

或 stable hash。

Phase 1 必須確保：

`must_ask_questions.validates_gate_id`

可以找到對應 Gate。

---

# 14｜Hard Gate category

允許：

```text
ability
experience
qualification
work_condition
```

對應規格 02。

---

# 15｜Hard Gate classification

允許：

```text
hard_gate
nice_to_have
pending_gate
```

注意：

主畫面通常只需要顯示：

- hard_gate
- pending_gate

Nice-to-have 可折疊或不顯示。

---

# 16｜Hard Gate status

Pre-call UI 統一使用：

```text
matched
unknown
unmatched
```

---

# 17｜Production Status Mapping

Production 現有報告使用：

```text
pass
partial
unknown
```

Pre-call Contract 不直接把 Production DB 改成新 enum。

使用 Adapter：

| Production | Pre-call Contract |
|---|---|
| `pass` | `matched` |
| `partial` | `unknown` |
| `unknown` | `unknown` |

注意：

Production 目前缺少可直接等價於：

`unmatched`

的單一通用值。

因此：

`unmatched`

只能在以下情況出現：

- AI 有明確負面證據
- 候選人電話中明確拒絕
- 現有 screening / hard_fail 有確定證據
- Adapter 可以可靠轉換

不可把：

`partial`

直接 mapping 成：

`unmatched`

---

# 18｜Hard Gate source

正式結構：

```json
"source": {
  "type": "jobs.hard_filters",
  "raw_label": "需有3年以上現場經驗"
}
```

允許 type：

```text
jobs.hard_filters
jobs.must_check_items
jobs.required_conditions
jobs.client_screen_conditions
ai_fallback
```

用途：

- audit
- debug
- 避免 AI 亂發明 Gate

---

# 19｜Hard Gate evidence

一句。

只描述已知證據。

例如：

```text
履歷有 Revit，但未看到獨立建模描述
```

不要輸出長 reasoning。

---

# 20｜verify_in_call

Boolean：

```json
true
```

若已 matched：

通常：

```json
false
```

若 unknown / unmatched 需要確認：

通常：

```json
true
```

---

# 21｜priority

允許：

```text
high
medium
low
```

主畫面排序使用。

---

# 22｜must_ask_questions

正式結構：

```json
[
  {
    "id": "q_1",
    "question": "",
    "validates_gate_id": "gate_1",
    "why_it_matters": "",
    "backup_probe": "",
    "answer_type": "experience"
  }
]
```

---

# 23｜欄位命名正式收斂

正式名稱：

```text
validates_gate_id
why_it_matters
```

以下名稱停止使用：

```text
validates
validates_gate
why
```

原因：

避免規格 / Backend / Frontend 欄位分裂。

---

# 24｜question

要求：

- 可直接口語問
- 15～45 字
- 不重問履歷明確資訊
- 不可只是 JD 改成問句

---

# 25｜validates_gate_id

必須指向：

`hard_gates[].id`

不得放 Gate label。

原因：

label 可能改字，但 ID 關係要穩定。

---

# 26｜why_it_matters

一句短文。

例如：

```text
確認是否具備獨立建模能力
```

UI 弱化。

---

# 27｜backup_probe

備用追問。

預設 UI 折疊。

允許：

```json
"backup_probe": null
```

---

# 28｜answer_type

允許：

```text
experience
responsibility
scale
condition
choice
```

用途：

- UI
- 後續 AI 回填
- Analytics

---

# 29｜ai_flags

正式結構：

```json
[
  {
    "id": "flag_1",
    "title": "",
    "category": "evidence_gap",
    "risk_level": "medium",
    "evidence_confidence": "high",
    "related_gate_id": "gate_1",
    "short_message": "",
    "recommended_action": {
      "type": "verify_in_call",
      "label": "電話先確認"
    },
    "show_on_main_card": true
  }
]
```

---

# 30｜AI Flag category

允許：

```text
hard_gate
evidence_gap
contradiction
work_condition
data_quality
```

---

# 31｜risk_level

允許：

```text
high
medium
low
```

---

# 32｜evidence_confidence

允許：

```text
high
medium
low
```

Rule：

Low confidence 不可產生 High Risk 主警示。

---

# 33｜related_gate_id

若 Flag 與 Gate 有關：

必須填。

如果是 Data Quality：

可以 null。

---

# 34｜short_message

15～45 字。

禁止長篇 AI reasoning。

---

# 35｜recommended_action

結構：

```json
{
  "type": "verify_in_call",
  "label": "電話先確認"
}
```

type 允許：

```text
verify_in_call
add_must_ask
add_backup_probe
request_data
ask_client
pause_recommendation
```

Phase 1：

主要只需要前五種。

---

# 36｜show_on_main_card

Boolean。

主畫面：

最多 1 個：

```json
true
```

其他 Flag：

```json
false
```

如果沒有值得顯示：

```json
"ai_flags": []
```

---

# 37｜meta

正式結構：

```json
{
  "generation_status": "ready",
  "used_ai_fallback_for_gates": false,
  "fallback_call_prep_available": true,
  "warnings": []
}
```

---

# 38｜generation_status

允許：

```text
ready
partial
failed
```

---

# 39｜used_ai_fallback_for_gates

如果 `jobs.hard_filters` 足夠：

```json
false
```

只有職缺資料不足才：

```json
true
```

---

# 40｜fallback_call_prep_available

若現有：

`applications.call_prep_md`

可 fallback：

```json
true
```

---

# 41｜warnings

系統層 warning。

例如：

```text
resume_missing
job_hard_filters_empty
resume_parse_failed
```

不要把 warnings 當 AI Flag 顯示。

---

# 42｜完整 Phase 1 JSON 範例

```json
{
  "schema_version": "1.0",
  "application_id": "app_123",
  "job_slug": "bim-engineer-tongluo",
  "generated_at": "2026-09-17T14:30:00+08:00",
  "source_fingerprint": "abc123",

  "candidate_summary": {
    "name": "王小明",
    "current_role": "BIM 助理工程師",
    "relevant_experience": "1.5 年 BIM 相關經驗",
    "location_summary": "新竹，苗栗可談",
    "source_channel": "line"
  },

  "job_context": {
    "title": "BIM 工程師",
    "locations": ["苗栗銅鑼"],
    "salary_summary": "35,000–43,000",
    "work_mode": "7成辦公室／3成無塵室",
    "work_hours": "08:30–17:30",
    "employment": "全職"
  },

  "call_goal": {
    "decision": "確認是否能推",
    "target_role": "BIM 工程師",
    "validation_points": [
      {
        "gate_id": "gate_1",
        "label": "Revit 實作"
      },
      {
        "gate_id": "gate_2",
        "label": "機電整合"
      },
      {
        "gate_id": "gate_3",
        "label": "銅鑼／無塵室"
      }
    ],
    "reason": "履歷有 Revit 經驗，但實作深度、機電整合與案場接受度尚未確認"
  },

  "hard_gates": [
    {
      "id": "gate_1",
      "label": "Revit 實作",
      "category": "ability",
      "classification": "hard_gate",
      "status": "unknown",
      "source": {
        "type": "jobs.hard_filters",
        "raw_label": "具備 Revit 基礎"
      },
      "evidence": "履歷有 Revit，但未看到獨立建模描述",
      "verify_in_call": true,
      "priority": "high"
    },
    {
      "id": "gate_2",
      "label": "機電整合",
      "category": "experience",
      "classification": "pending_gate",
      "status": "unknown",
      "source": {
        "type": "jobs.must_check_items",
        "raw_label": "確認是否有 MEP / 管線整合經驗"
      },
      "evidence": "履歷未看到明確機電整合內容",
      "verify_in_call": true,
      "priority": "medium"
    },
    {
      "id": "gate_3",
      "label": "銅鑼／無塵室",
      "category": "work_condition",
      "classification": "hard_gate",
      "status": "unknown",
      "source": {
        "type": "jobs.required_conditions",
        "raw_label": "可接受苗栗銅鑼與部分無塵室工作"
      },
      "evidence": "目前僅知道候選人在新竹，接受度未知",
      "verify_in_call": true,
      "priority": "high"
    }
  ],

  "must_ask_questions": [
    {
      "id": "q_1",
      "question": "你現在用 Revit，通常是自己從零建模，還是接既有模型修改？",
      "validates_gate_id": "gate_1",
      "why_it_matters": "確認 Revit 實作深度",
      "backup_probe": "你自己通常會負責到哪一段？",
      "answer_type": "responsibility"
    },
    {
      "id": "q_2",
      "question": "你有實際碰過機電、設備或管線整合嗎？自己負責到哪一段？",
      "validates_gate_id": "gate_2",
      "why_it_matters": "確認 MEP / 協作經驗",
      "backup_probe": "比較偏建築模型還是也會處理機電碰撞？",
      "answer_type": "experience"
    },
    {
      "id": "q_3",
      "question": "如果工作在苗栗銅鑼，而且有部分無塵室現場，你目前可以接受嗎？",
      "validates_gate_id": "gate_3",
      "why_it_matters": "確認地點與工作型態",
      "backup_probe": null,
      "answer_type": "condition"
    }
  ],

  "ai_flags": [
    {
      "id": "flag_1",
      "title": "Revit 實作深度不明",
      "category": "evidence_gap",
      "risk_level": "medium",
      "evidence_confidence": "high",
      "related_gate_id": "gate_1",
      "short_message": "履歷只有 Revit 工具名稱，未看到獨立建模證據",
      "recommended_action": {
        "type": "verify_in_call",
        "label": "電話先確認"
      },
      "show_on_main_card": true
    }
  ],

  "meta": {
    "generation_status": "ready",
    "used_ai_fallback_for_gates": false,
    "fallback_call_prep_available": true,
    "warnings": []
  }
}
```

---

# 43｜Runtime / Cache / DB Boundary

---

## Runtime Derived

不應新建 DB 欄位：

- `candidate_summary.current_role`
- `candidate_summary.relevant_experience`
- `candidate_summary.location_summary`
- `call_goal`
- Hard Gate classification reasoning
- `must_ask_questions`
- `ai_flags`

---

## Cache

可 cache：

- 整份 Pre-call Card JSON
- AI derived summary
- Questions
- Flags

Production Audit 建議可使用：

`applications.precall_card_json`

但 Phase 1 可先抽象化 cache interface，不強制 migration。

---

## DB Facts

必須來自 Production：

- `application_id`
- `job_slug`
- candidate name
- source_channel
- Job facts
- resume reference

---

# 44｜Phase 2 預留：Gate Result

Phase 2 才持久化。

預留結構：

```json
{
  "gate_id": "gate_1",
  "result": "matched",
  "evidence_note": "",
  "updated_by": "consultant",
  "updated_at": ""
}
```

---

# 45｜Phase 3 預留：Post-call Decision

正式方向：

```json
{
  "ai_recommendation": {
    "route": "need_more_info",
    "reason": ""
  },

  "consultant_decision": {
    "route": "",
    "reason": ""
  }
}
```

AI 建議與顧問決策：

不可覆蓋彼此。

---

# 46｜Post-call Route Enum

規格層允許：

```text
recommend
need_more_info
alternative_role
talent_pool
pause_recommendation
```

但：

**不可直接把這五態新增成 Production Pipeline 的第五套狀態。**

未來需由 Adapter 映射：

- `manual_stage`
- `screen_decision`
- `candidate_forwards`
- talent pool 相關流程

---

# 47｜API Response Wrapper

建議：

```json
{
  "ok": true,
  "data": {
    "...": "PreCallCard"
  },
  "error": null
}
```

失敗：

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "PRECALL_GENERATION_FAILED",
    "message": ""
  }
}
```

---

# 48｜Fallback Contract

如果新 Pre-call Card 生成失敗：

Backend 可回：

```json
{
  "fallback": {
    "type": "call_prep_md",
    "content": ""
  }
}
```

前端：

顯示既有電洽前準備內容。

Phase 1 不可因新功能失敗而讓候選人詳情頁 crash。

---

# 49｜Backward Compatibility

以下 Production 欄位 Phase 1 不移除：

- `applications.call_prep_md`
- `call_summary_md`
- 既有 candidate note / report logic

新 Pre-call 功能是：

**upgrade path**

不是 destructive replacement。

---

# 50｜前端使用規則

Frontend 不應自行：

- 重新判 Hard Gate
- 重新 mapping status
- 自行產生 Questions
- 自行決定 Flag Priority

Frontend 只：

- render
- sort by backend priority
- expand / collapse
- trigger regenerate

所有 mapping 集中在 Backend Adapter。

---

# 51｜Schema Validation Rules

---

## Root 必填

- `schema_version`
- `application_id`
- `job_slug`
- `candidate_summary`
- `call_goal`
- `hard_gates`
- `must_ask_questions`
- `ai_flags`
- `meta`

---

## 數量限制

### call_goal.validation_points

2～3

### hard_gates 主畫面

最多 3

### must_ask_questions

最多 3

### ai_flags 主畫面

最多 1 個 `show_on_main_card=true`

---

# 52｜強制失敗條件

以下視為 Contract 不合格：

- AI 自己產生 `application_id`
- AI 自己產生 `job_slug`
- Hard Gate 沒有 source
- Question 沒有 `validates_gate_id`
- `validates_gate_id` 找不到 Gate
- 同時使用 `validates` / `validates_gate` 等舊欄位
- `partial` 被 mapping 成 `unmatched`
- Nice-to-have 被當成 Hard Gate
- Frontend 自己做 status mapping
- 超過 3 個 Must Ask
- 超過 1 個 Primary Flag
- AI Flag 沒有 action
- Production Job Facts 被 AI 改寫成不同內容
- runtime derived candidate summary 被當成新的 Candidate source of truth

---

# 53｜Contract Ownership

## Backend / Adapter

負責：

- Production → Contract mapping
- Job source
- status mapping
- cache
- fallback
- schema validation

---

## AI Worker

負責：

- Candidate summary derived
- Call Goal
- 缺 Gate 時 fallback classification
- Must Ask
- AI Flag

---

## Frontend

負責：

- Render
- ADHD UI
- Expand / Collapse
- Retry
- Regenerate

---

# 54｜Phase 1 完成條件

Phase 1 若完成，應做到：

```text
application_id
+
job_slug
↓
Backend Adapter
↓
PreCallCard JSON v1.0
↓
Frontend Card UI
```

且：

- 不新建 Job
- 不新建 Candidate
- 不改 Pipeline
- 不持久化 Gate Result
- 不做 Post-call Decision

---

# 55｜版本紀錄

| 版本 | 日期 | 說明 |
|---|---|---|
| v1.0 | 2026-09-17 | 依 Production Integration Audit 收斂 Pre-call 正式 JSON Contract、status mapping、runtime/cache/DB boundary、fallback 與 Phase 2/3 預留結構 |
