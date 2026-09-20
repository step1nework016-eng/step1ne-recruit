# 06｜ADHD UI/UX 規格 v1.0

> 模組：Universal Pre-call Card  
> 用途：定義 Step1ne 候選人電洽卡在「電話前 / 電話中 / 電話後」三種狀態下的 UI/UX、資訊層級、操作方式與 ADHD 友善原則。  
> 核心原則：**第一眼只顯示現在需要處理的資訊；其他資訊折疊。**

---

# 1｜產品目標

這個介面不是履歷閱讀器，也不是完整 ATS 詳情頁。

它的角色是：

> **顧問在 60～90 秒內看懂重點，開始打電話，通話中快速記錄，通話後立即知道下一步。**

設計必須優先服務：

1. 快速理解
2. 少打字
3. 少切頁
4. 少思考「現在要看哪裡」
5. 讓 AI 先做 80%，顧問只確認與修正 20%

---

# 2｜整體流程

```text
候選人 + JD
↓
AI 產生 Pre-call Card
↓
顧問快速確認
↓
開始電洽
↓
Call Mode
↓
快速勾選 + 紀錄
↓
電話結束
↓
AI 更新 Gate / Flag
↓
Decision
↓
下一步行動
```

---

# 3｜三個 UI 狀態

整個介面固定有三種主要狀態：

## State A｜電話前 Pre-call

用途：

- 60～90 秒快速看完
- 知道這通電話要確認什麼
- 知道今天只問哪 3 題
- 知道最大的風險
- 開始打電話

---

## State B｜電話中 Call Mode

用途：

- 只保留真正要問的內容
- 快速勾選
- 快速輸入短筆記
- 避免通話中讀大量資料

---

## State C｜電話後 Decision

用途：

- 看 AI 建議分流
- 確認 Gate 結果
- 看下一步
- 顧問可覆寫 AI 建議

---

# 4｜ADHD 核心設計原則

---

## 原則 1｜一次只處理一件事

電話前：

> 我要確認什麼？

電話中：

> 下一題是什麼？

電話後：

> 下一步去哪？

不同階段不要同時塞入所有資訊。

---

## 原則 2｜主畫面最多 5 個資訊區

電話前第一屏最多：

1. 候選人摘要
2. 🎯 這通電話只要確認
3. 🚪 Hard Gate
4. 🔥 必問 3 題
5. ⚠️ AI Flag

不要再增加其他主卡。

---

## 原則 3｜重要資訊永遠在上面

優先序：

```text
Call Goal
↓
Unknown Gate
↓
Must Ask
↓
AI Flag
↓
Secondary Info
```

---

## 原則 4｜能勾選就不要打字

優先使用：

- Checkbox
- Segmented Control
- Status Chip
- 快速選項
- 一鍵套用

文字輸入只用於：

- 補充證據
- 特殊備註
- 候選人原話

---

## 原則 5｜預設折疊次要資訊

預設折疊：

- 完整履歷
- 已符合 Gate
- Nice-to-have
- Low Risk Flag
- 備用追問
- 完整 AI Reasoning
- 歷史通話紀錄

---

## 原則 6｜不做虛假精準

禁止：

- Match 83%
- Fit Score 72
- 五顆星
- A/B/C Rating
- AI 推薦分數

允許：

- 已符合
- 待確認
- 明確不符
- 可推薦
- 補資料
- 暫停推薦

---

# 5｜State A：電話前 Pre-call

---

## 5.1 頂部 Candidate Header

顯示：

- 姓名
- 目標職缺
- 現職
- 相關年資
- 地點

範例：

```text
王小明
BIM 工程師候選人

BIM 助理工程師｜1.5 年｜新竹
```

右側：

- `查看履歷`
- `更多`

不要把完整履歷內容攤在主畫面。

---

## 5.2 🎯 這通電話只要確認

此區為第一優先。

格式：

```text
🎯 這通電話只要確認

確認是否能推「BIM 工程師」

[ Revit 實作 ] [ 機電整合 ] [ 銅鑼 / 無塵室 ]

AI 判斷原因
履歷有 Revit 經驗，但實作深度與案場接受度尚未確認。
```

視覺層級：

1. Decision 最大
2. Validation Point 次大
3. Why 弱化

---

## 5.3 🚪 Hard Gate

主畫面最多：

**3 個**

每個 Gate 顯示：

- 名稱
- 狀態
- 一句證據

狀態：

- ✅ 已符合
- ❓ 待確認
- ⛔ 明確不符

範例：

```text
Revit 實作
❓ 待確認
履歷有工具名稱，但未看到獨立建模證據
```

---

## 5.4 已符合 Gate

預設收折：

```text
✓ 已確認 2 項
```

點開才看。

目的：

不要讓已經解決的資訊占用注意力。

---

## 5.5 🔥 今天只問這 3 題

此區應是電話前第二大視覺區。

每題顯示：

```text
① 你現在用 Revit，通常是自己從零建模，還是接既有模型修改？

驗證：Revit 實作深度
```

每題提供：

- 主問題
- 驗證 Gate
- 備用追問（折疊）

---

## 5.6 ⚠️ AI Flag

主畫面最多：

**1 個**

格式：

```text
⚠️ 需確認

Revit 實作深度不明
履歷只有「協助建模」，未看到獨立負責證據。

→ 電話先確認
```

若沒有 Flag：

整區不出現。

不可顯示：

> 沒有風險

避免無效佔位。

---

## 5.7 開始電洽 CTA

主 CTA：

**開始電洽**

位置：

- 桌機：右上或右下 Sticky
- 手機：底部 Sticky Button

點擊後：

進入 Call Mode。

---

# 6｜State B：Call Mode

---

## 6.1 Call Mode 核心原則

進入 Call Mode 後：

**自動隱藏大部分 Pre-call 資訊。**

只保留：

- 候選人姓名
- 目標職缺
- 3 個問題
- 快速筆記
- Gate 狀態
- 結束通話

---

# 6.2 顯示結構

```text
王小明｜BIM 工程師
通話中

Q1
你現在用 Revit，通常是自己從零建模，還是接既有模型修改？

[ 已確認符合 ]
[ 還要追問 ]
[ 不符合 ]

筆記：
＿＿＿＿＿＿＿＿＿＿

Q2
...

Q3
...
```

---

# 6.3 每題快速操作

每題提供三種狀態：

- ✅ 已確認符合
- ❓ 還要追問
- ⛔ 不符合

後台對應：

```text
matched
unknown
unmatched
```

---

# 6.4 備用追問

預設收折：

> `需要追問？`

點開才顯示：

```text
例如：你自己通常會負責到哪一段？
```

---

# 6.5 快速筆記

每題最多一個短筆記欄。

Placeholder：

> 記候選人原話或關鍵證據

避免做大型文字編輯器。

---

# 6.6 通話中快捷區

可提供：

- `候選人提到新需求`
- `新增問題`
- `查看履歷`
- `暫存`

但全部放次要區。

不要搶主視覺。

---

# 6.7 通話時間

可顯示：

```text
04:32
```

用途：

- 幫助控制通話長度
- 後續 Coaching

不要做成強烈倒數計時。

---

# 6.8 Exit Checklist

通話接近結束時，可展開：

```text
電話結束前

☐ 現職 / 最近工作
☐ 產業 / 專案
☐ 核心工作
☐ 實際技能
☐ 看機會原因
☐ 下一份需求
☐ 地點 + 薪資 + 到職
☐ 下一步
```

預設不要一直佔畫面。

---

# 7｜State C：電話後 Decision

---

# 7.1 電話結束按鈕

CTA：

**結束通話並整理**

AI 執行：

1. 更新 Gate
2. 更新 Flag
3. 摘要答案
4. 產生路徑建議

---

# 7.2 決策卡

第一眼只顯示：

```text
🟠 補資料

機電整合經驗仍未確認，其餘核心 Gate 已通過。

下一步
☐ 補確認 MEP 實作
☐ 收完整履歷
```

---

# 7.3 五種路徑

- ✅ 可推薦
- 🟠 補資料
- 🔁 轉其他職缺
- 🗂 人才池
- ⏸ 暫停推薦

---

# 7.4 顧問 Override

AI 建議旁一定有：

**修改判斷**

顧問可改：

- 路徑
- 原因
- 下一步

系統需記錄：

```text
AI 建議
顧問最終決定
```

兩者不可覆蓋彼此。

---

# 8｜Desktop Layout

建議最大寬度：

**960～1100px**

---

## 桌機電話前

兩欄：

### 左側 65%

- Call Goal
- Hard Gate
- Must Ask

### 右側 35%

- Candidate Summary
- AI Flag
- CTA

---

## 桌機 Call Mode

建議：

單欄為主。

原因：

通話中不要讓顧問視線左右跳動。

---

# 9｜Mobile Layout

手機版：

全部單欄。

優先順序：

```text
Candidate
↓
Call Goal
↓
Must Ask
↓
Gate
↓
Flag
↓
CTA
```

底部固定：

**開始電洽 / 結束通話**

---

# 10｜字級規格

建議：

## Desktop

- Page Title：24–28px
- Decision：20–24px
- Section Title：16–18px
- Question：16px
- Body：14px
- Helper Text：12–13px

---

## Mobile

- Page Title：20–22px
- Decision：18–20px
- Section Title：16px
- Question：16px
- Body：14px

---

# 11｜間距規格

卡片內距：

**16–20px**

區塊距離：

**12–16px**

卡片圓角：

**12–16px**

避免：

- 過度密集
- 過度留白
- 太多框線

---

# 12｜顏色規則

顏色只能代表：

- 主操作
- 狀態
- 風險

不要每張卡不同顏色。

建議狀態語意：

- Positive：已符合 / 可推薦
- Warning：待確認 / 補資料
- Risk：不符合 / 暫停
- Neutral：人才池 / 次要資訊

---

# 13｜Icon 規則

Icon 只用於：

- 幫助快速辨識區塊
- 不做裝飾

建議：

- 🎯 Call Goal
- 🚪 Gate
- 🔥 Must Ask
- ⚠️ Flag
- ✅ Recommend
- 🔁 Alternative
- 🗂 Pool
- ⏸ Pause

---

# 14｜AI 產生內容的 UI 限制

AI 不可產生超過：

### Call Goal
1 句

### Validation Points
3 個

### Hard Gate
主畫面 3 個

### Must Ask
3 題

### Flag
1 個

### Decision 原因
1 句

### 下一步
3 個 Action

---

# 15｜空狀態

---

## 沒有 AI Flag

不顯示 Flag 卡。

---

## 沒有足夠資料

顯示：

```text
目前資料不足以產生完整 Pre-call Card

缺少：
・工作內容
・目標職缺 JD

[補資料]
```

不要硬生成。

---

# 16｜Loading 狀態

AI 分析中：

顯示 Skeleton。

不要顯示：

> AI 正在深度分析你的候選人...

避免多餘敘事。

---

# 17｜Error 狀態

若 AI 失敗：

```text
部分內容無法產生

已保留：
✓ 候選人資料
✓ JD

未產生：
・Hard Gate
・必問 3 題

[重新產生]
```

---

# 18｜禁止設計

禁止：

- 履歷全文塞主畫面
- 超過 5 張主卡
- 每個區塊不同顏色
- 多層 Tabs
- 過多 Modal
- 需要顧問大量打字
- AI 長篇 reasoning
- 百分比 Match Score
- 星等
- 排名
- 複雜雷達圖
- 大型 Dashboard
- 電話中跳出大量通知

---

# 19｜AI 與顧問分工

---

## AI 負責

- 摘要候選人
- 找 Hard Gate
- 找 Unknown
- 生成必問 3 題
- 產生 Flag
- 通話後更新狀態
- 建議 Decision

---

## 顧問負責

- 確認 AI 有沒有抓錯
- 問問題
- 判斷候選人語境
- 修正 Gate
- 決定是否推薦
- 決定下一步

---

# 20｜一次操作完成原則

高頻操作需盡量 1 click：

- 開始電洽
- Gate → matched
- Gate → unknown
- Gate → unmatched
- 展開備用追問
- 結束通話
- 接受 AI 建議
- 修改決策

---

# 21｜Accessibility / 易讀性

必須：

- 狀態不可只靠顏色
- 搭配文字或 Icon
- 最小點擊區域 44px
- 文字對比清楚
- 手機不需橫向捲動
- Keyboard 可操作核心功能

---

# 22｜前端狀態 Schema

```yaml
ui_state:
  mode: "pre_call | in_call | post_call"

  expanded_sections:
    resume: false
    matched_gates: false
    backup_probes: []

  active_question: 1

  consultant_edits:
    call_goal: false
    gates: []
    questions: []
    decision: false
```

---

# 23｜元件建議

```text
PreCallPage
├── CandidateHeader
├── CallGoalCard
├── HardGateList
│   └── HardGateItem
├── MustAskList
│   └── QuestionCard
├── AIFlagCard
└── StartCallButton

CallModePage
├── CallHeader
├── CallTimer
├── QuestionStepper
│   └── QuestionCard
│       ├── GateStatusControl
│       ├── BackupProbe
│       └── QuickNote
├── ExitChecklist
└── EndCallButton

PostCallPage
├── DecisionCard
├── GateSummary
├── NextActions
├── AlternativeRoleCard
└── ConsultantOverride
```

---

# 24｜QA Checklist

---

## 電話前

- [ ] 5 秒內看得懂電話目標
- [ ] 主畫面不超過 3 個 Gate
- [ ] 問題不超過 3 題
- [ ] Flag 不超過 1 個
- [ ] 已符合 Gate 預設收折
- [ ] 完整履歷不佔主畫面

---

## 電話中

- [ ] 下一題永遠清楚
- [ ] 每題可以 1 click 更新狀態
- [ ] 備用追問預設隱藏
- [ ] 筆記欄夠短
- [ ] 不需要切頁找問題
- [ ] 可以查看 Exit Checklist

---

## 電話後

- [ ] 第一眼只看到一個 Primary Route
- [ ] 原因只有一句
- [ ] 下一步最多 3 個
- [ ] 顧問可以 Override
- [ ] AI 與顧問決策皆有紀錄

---

# 25｜完成標準

這套 UI 若設計成功，顧問應該可以：

### 電話前
60～90 秒看完。

### 電話中
幾乎不用思考介面操作。

### 電話後
30 秒內完成分流。

---

# 26｜核心一句話

> **不是讓顧問看更多資訊，而是讓顧問更快知道現在該看什麼。**

---

# 27｜版本紀錄

| 版本 | 日期 | 說明 |
|---|---|---|
| v1.0 | 2026-09-17 | 建立電話前 / 電話中 / 電話後三狀態 ADHD UI/UX 規格、Desktop / Mobile、互動限制、元件結構與 QA Checklist |
