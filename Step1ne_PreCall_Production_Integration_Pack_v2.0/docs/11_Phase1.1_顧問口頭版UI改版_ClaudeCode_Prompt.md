# Step1ne Pre-call Phase 1.1｜顧問口頭版 UI v2.1 改版 Prompt

你現在負責 Step1ne Pre-call 已上線 Phase 1 的 **Phase 1.1 改版**。

這不是 Phase 2。
不要實作任何 Call Mode persistence、Gate Result DB、Post-call Decision、Pipeline 更新。

## 現況

Phase 1 已上線並完成：
- Job / Application / Resume 串接
- `precall_card` AI handler
- Pre-call Card JSON
- 現有「電洽前準備」入口
- loading / stale / fallback
- Desktop / Mobile

現在顧問真人回饋顯示：

目前 UI 太像「AI 判斷摘要」，
真正需要的是「電話接通後可以直接照順序使用的顧問作戰卡」。

新版視覺參考：

`Step1ne_PreCall_Card_v2.1_順序流程版.html`

---

# 一、核心改版原則

主流程必須有非常清楚的閱讀順序：

**左 → 右、上 → 下**

Desktop：

```text
候選人摘要
↓
這通電話只要確認
↓
1 開場
→
2 已知／不要重問
↓
3 Top 3 必問
→
4 補問
↓
5 60 秒職缺介紹
→
6 薪資／地點／條件
↓
7 收尾／確認意願
```

Mobile：

```text
1
↓
2
↓
3
↓
4
↓
5
↓
6
↓
7
```

所有主流程卡片必須顯示明確 Step Number。

---

# 二、保留目前 Production 外框

保留：
- 候選人 Drawer / 詳情面板
- 「電洽前準備」入口
- Candidate Summary
- 深色「這通電話只要確認」
- Job facts / 薪資 / 地點 / 時間
- Loading / Error / Stale / Retry / Fallback
- 現有 Backend Adapter 與 Source of Truth

不要重做整個 Candidate Drawer。
不要改 Navigation。
不要做 Dashboard。

---

# 三、主畫面資訊架構

## Step 1｜開場＋設定目的

顯示一段可直接照著說的顧問開場。

內容應包含：
- 顧問已看過履歷／AI 面談
- 今天不重問哪些內容
- 今天主要補哪些差異
- 建議通話時間

UI：
- 一段口語 script
- 不要超過約 3～4 行

---

## Step 2｜已經知道，不要重問

新增：

`known_do_not_ask`

顯示成短 chip。

例如：
- 離職原因
- Gap
- 海外帳務
- 月結
- 到職時間

目的：

避免顧問把 AI／履歷已經確認過的事情再問一次。

這一區不是 Hard Gate。

---

## Step 3｜Top 3 必問

保留 ADHD 原則：

第一層最多 3 題。

每題至少顯示：
- 題目名稱
- 顧問直接問的口語問題
- 這題要確認什麼

可以展開：
- lead-in / 怎麼接
- backup probe / 如果沒有怎麼追
- record hint / 建議記什麼

注意：

不要把 `why_it_matters` 變成一大段說明。

---

## Step 4｜補問

允許 0～3 題。

這些是：
- 重要但非 Top 3
- 有時間／答案需要再深入時使用

預設比 Top 3 弱化。

如果沒有補問：
整區不顯示。

---

## Step 5｜60 秒職缺介紹

新增一段：

`job_pitch_60s`

要求：
- 顧問可直接口頭說
- 不是 JD 全文
- 說明角色和一般同類職缺的差異
- 說明為什麼這位候選人可能有連結
- 不超過約 60 秒口語長度

可收折，但標題在主流程中必須清楚可見。

---

## Step 6｜薪資／地點／條件

全部資料必須來自 Production Job facts。

包含視職缺需要：
- salary
- location
- work hours
- work mode
- employment
- lodging
- shift
- interview process
- benefits

AI 不可自行改寫數字或 Job facts。

UI 以短 key-value 呈現。

---

## Step 7｜收尾＋確認意願

新增：

`closing_script`

內容包含：
- 簡短總結候選人優勢
- 尚待確認／可能發展的部分
- 直接詢問下一步意願

Phase 1.1 只顯示 script。

**不可寫 DB、不可更新 stage。**

---

# 四、輔助工具不再放進主流程

以下移到主流程下方：

## 電話旁超短版

顯示：
- 1～6 個極短提示
- 只讓顧問通話時快速 glance

不是另一套流程。

---

## AI Flag / Hard Gate Evidence

降級為輔助工具。

主畫面不要再讓「Hard Gate」成為最大視覺主角。

Hard Gate 仍是 AI Agent 的底層判斷來源，
但顧問主 UI 顯示的是：

> 我現在要問什麼／怎麼問。

Hard Gate / Evidence：
- 放折疊區
- 保留 debug / 顧問判斷用途

---

# 五、AI Output Contract 需要向後相容擴充

先檢查目前 Production `precall_card` JSON。

如果以下資料不存在，
請擴充 AI output，但不得破壞現有欄位。

建議新增：

```json
{
  "conversation_flow": {
    "opening": {
      "script": ""
    },

    "known_do_not_ask": [
      {
        "label": "",
        "evidence": ""
      }
    ],

    "top_questions": [
      {
        "id": "",
        "title": "",
        "goal": "",
        "lead_in": "",
        "question": "",
        "backup_probe": "",
        "record_hint": "",
        "validates_gate_id": ""
      }
    ],

    "extra_questions": [
      {
        "id": "",
        "title": "",
        "goal": "",
        "lead_in": "",
        "question": "",
        "backup_probe": "",
        "record_hint": "",
        "validates_gate_id": ""
      }
    ],

    "job_pitch_60s": {
      "script": ""
    },

    "conditions": [
      {
        "label": "",
        "value": "",
        "source": "job"
      }
    ],

    "closing": {
      "script": ""
    },

    "phone_sidecar": [
      ""
    ]
  }
}
```

---

# 六、Backward Compatibility

目前既有欄位不可破壞：

- candidate_summary
- call_goal
- hard_gates
- must_ask_questions
- ai_flags
- job_context
- meta

如果新 `conversation_flow` 不存在：

Frontend 必須仍能顯示舊 Phase 1 Card。

不要做 destructive schema replacement。

---

# 七、Question 行為調整

原本規格：
`Must Ask ≤ 3`

Phase 1.1 改成：

- `top_questions`: 1～3
- `extra_questions`: 0～3

第一屏仍最多只有 3 題。

不能因新增補問又變成資訊牆。

---

# 八、AI 必須避免重問

`known_do_not_ask` 必須根據：

- Resume 明確資訊
- 已完成的 AI 面談
- 現有 report / transcript（若 Production 已提供）
- Production 已知 candidate facts

如果證據不充分：
不要塞進 `known_do_not_ask`。

---

# 九、AI Job Pitch 原則

`job_pitch_60s`：

不能：
- 複製整份 JD
- 自創公司條件
- 自創薪資福利
- 過度推銷

應該：
- 口語
- 角色定位
- 核心工作
- 和候選人的連結
- 新的發展點

---

# 十、UI QA

Desktop：
- 顧問第一眼能知道從 Step 1 開始
- 視線自然左→右、上→下
- Step 1～7 清晰
- 輔助工具不搶主流程

Mobile：
- 1→7 單欄
- 不依賴左右閱讀
- 無 horizontal overflow

---

# 十一、禁止事項

不要：
- 新 DB table
- Phase 2 Gate Result
- Post-call Decision
- manual_stage update
- candidate_forward
- talent pool routing
- Coaching
- Match Score
- 評分雷達圖
- 自動淘汰

---

# 十二、完成後交付

產出：

`PRECALL_PHASE1_1_UI_AGENT_UPDATE_REPORT.md`

至少包含：

1. UI changes
2. AI output changes
3. Schema compatibility
4. Files changed
5. Backend changes（若有）
6. Frontend changes
7. QA
8. Regression
9. Known issues
10. Deferred
11. Git diff summary
12. Build/test result

完成後停止。

不要自行進 Phase 2。
