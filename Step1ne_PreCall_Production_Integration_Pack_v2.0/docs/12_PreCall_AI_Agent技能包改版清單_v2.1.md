# Step1ne Pre-call v2.1｜AI Agent 技能包改版清單

> 目的：把現有 Pre-call AI 從「AI 判斷摘要」升級為「顧問可直接照著電洽的口頭作戰卡」。
> 原則：Hard Gate 邏輯仍保留，但 UI / Agent Output 要改成顧問實際使用順序。

---

# 一、需要「大改」的技能包

## 03｜AI 必問 3 題生成規則

### 現況
只允許 Must Ask 最多 3 題。

### 要改
改成兩層：

- Top Questions：1～3
- Extra Questions：0～3

每題新增：

- `title`
- `goal`
- `lead_in`
- `question`
- `backup_probe`
- `record_hint`
- `validates_gate_id`

### 原則

第一屏仍只有 Top 3。

Extra Questions 預設弱化／折疊。

---

## 06｜ADHD UI/UX 規格

### 現況
Candidate → Call Goal → Hard Gate → Must Ask → Flag。

### 要改

主流程固定成：

1. 開場
2. 已知／不要重問
3. Top 3
4. 補問
5. 60 秒職缺介紹
6. 薪資／地點／條件
7. 收尾

Hard Gate / AI Flag 降級為輔助資訊。

Desktop：
左→右、上→下。

Mobile：
1→7 單欄。

---

## 07｜Production AI Output Schema

### 要新增

`conversation_flow`

包含：

- opening
- known_do_not_ask
- top_questions
- extra_questions
- job_pitch_60s
- conditions
- closing
- phone_sidecar

既有：
- call_goal
- hard_gates
- must_ask_questions
- ai_flags

不可直接刪除。

需要 backward compatibility。

---

# 二、需要「中度修改」的技能包

## 00｜候選人快速電洽公式

要補進正式流程：

- 開場時主動說「哪些已經知道、不重問」
- 顧問先補能力缺口
- 再做 60 秒 Job Pitch
- 再講薪資／地點／條件
- 最後確認意願

Exit Checklist 保留。

---

## 01｜AI 生成規則：這通電話只要確認

核心不變。

但 `Call Goal` 要能驅動：

- opening
- Top 3
- extra questions
- job pitch
- closing

Call Goal 不再只是 UI 標題。

---

## 04｜AI Flag 風險提示規則

判斷邏輯大致不變。

UI role 改變：

AI Flag 不再是主流程卡片。

它是：

> 顧問注意事項 / 輔助工具

Primary Flag 最多 1 個仍保留。

---

## 08｜API Adapter Contract

Endpoint 不一定需要改。

但需要確認：
- AI worker payload 能提供 transcript / existing known facts
- response 能回 `conversation_flow`
- schema validation 支援新增欄位
- fallback 舊 schema 仍可顯示

不新增 Phase 2 API。

---

## 09｜開發交接與 QA

新增 QA：

- Step 1～7 順序清楚
- 顧問不會不知道從哪開始
- 已知內容不重問
- Top 3 真的是最重要
- Extra Questions 不是硬塞
- 60 秒 Job Pitch 可直接口說
- Closing 可直接使用
- Mobile 1→7 正常

---

# 三、可以「基本不動」的技能包

## 02｜Hard Gate 通用判斷引擎

核心邏輯不需要重寫。

仍然負責：

Job requirements
→ candidate evidence
→ matched / unknown / unmatched
→ 排出需要驗證的 Gate

改變的是：

**Hard Gate 不再直接當 UI 主角。**

它改成驅動：

- Call Goal
- Top Questions
- Extra Questions
- AI Flag

---

## 05｜電話後路徑規則

Phase 1.1 不動。

因為：
- 還沒進 Phase 2 / 3
- 不做 post-call persistence
- 不做 route decision

保留給後面。

---

# 四、建議新增 1 個專用技能包

建議新增：

`11_AI_顧問口頭電洽流程生成規則_v1.0.md`

專門負責：

## Input

- Job facts
- Hard Gates
- Candidate Resume
- AI interview / transcript
- Existing known facts
- Call Goal

## Output

1. Opening
2. Known / Do Not Ask
3. Top 3
4. Extra Questions
5. Job Pitch 60s
6. Conditions
7. Closing
8. Phone Sidecar

---

# 五、為什麼建議新增 11，而不是全部塞進 03

03 的責任應該保持：

> 問題生成。

而新版還多了：

- 開場
- 不要重問
- 職缺介紹
- 條件整理
- 收尾
- 電話超短版

這已經不是單純「問題生成」。

因此獨立一個：

**Conversation Flow / Consultant Script Skill**

會比較乾淨。

---

# 六、Agent 技能責任分工

```text
02 Hard Gate Engine
        ↓
01 Call Goal
        ↓
03 Question Engine
        ↓
04 AI Flag
        ↓
11 Consultant Conversation Flow
        ↓
07 Production Schema
        ↓
06 UI
```

---

# 七、最重要的 Agent 原則

AI Agent 不應再只想：

> 「我判斷這個候選人符合什麼？」

而要改成：

> 「顧問電話接起來後，怎麼用最少時間把真正未知的東西問清楚？」

所以：

**AI 判斷是底層。**
**顧問行動才是輸出。**
