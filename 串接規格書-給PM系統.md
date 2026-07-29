# 應徵資料串接規格 — 給 pm.aijob.com.tw 開發者／AI

> 把這份整份貼給負責 pm 系統的人或 AI 即可，不需要另外說明。
> 有任何欄位對不上或行為跟這份描述不符，請直接回報，不要自行猜測補值。

---

## 一句話

Step1ne 官網 `step1ne.com/apply/` 的應徵表單，資料存在一個獨立的 API。
**請你們主動來拉**，寫進 pm 系統的「人才庫」。

我們刻意不做「推送到你們」——因為我們不知道你們的認證方式與資料結構，
由你們拉的話，時機、重試、欄位對應都在你們可控範圍內，出錯時你們也看得到 log。

---

## ⚠️ 先講主檔歸屬（這段最重要）

pm 系統裡可以新增客戶、職缺、人才；官網表單也會產生應徵者。
**如果兩邊都能編輯同一筆資料，幾週之內就會分不出哪一邊是對的。**

所以先定義清楚：

| 資料 | 主檔（唯一可編輯的地方） | 另一邊的角色 |
|---|---|---|
| **客戶** | pm 系統 | 官網完全不碰 |
| **職缺** | **pm 系統** | 官網表單向 pm 拉清單 |
| **人才／應徵者** | **pm 系統** | 官網 API 是**收件匣**，只進不改 |

### 這代表什麼

**官網這邊的 `applications` 表是 append-only 的收件記錄。**
它記錄「某人在某個時間點填了什麼」，同步到 pm 之後就不再改動——
連狀態也不會回頭改。那是原始憑證，不是工作用的資料。

**顧問所有的編輯、標記、處置都在 pm 做。**
我們這邊不提供編輯介面，也不會覆蓋你們的任何欄位。

⚠️ **請不要把同步當成「兩邊保持一致」。**
它是「把收件匣的新信搬進來」。搬進來之後那份資料就歸 pm 管，
官網那份只是備份，兩邊本來就會不一樣，那是正常的。

---

## 需要你們提供的一個端點：職缺清單

官網表單要讓應徵者選職缺。**目前是讀官網自己的靜態檔，那會跟 pm 裡的職缺對不起來。**

請提供一個唯讀端點，讓官網表單能拉到 pm 裡「開放中」的職缺：

```
GET https://pm.aijob.com.tw/api/public/jobs
```

回傳（欄位名可以調整，請告訴我們實際的）：

```json
[
  { "id": "j_123", "slug": "bim-engineer", "title": "BIM工程師（派遣・大型建廠專案）",
    "location": "苗栗縣銅鑼鄉", "status": "open" }
]
```

**只回「開放中」的職缺，而且不要含客戶名稱。**
那個端點會被公開的表單頁呼叫，客戶身分不能外流。

需要的話可以用一組固定 token 保護，我們會放在伺服器端呼叫而非前端。

⚠️ **`slug` 請跟官網 `step1ne.com/jobs/<slug>` 對齊。**
對不上的話，應徵者從職缺頁點「我要應徵」進來時無法自動帶入職缺。

---

## 之後會需要（現在不用做，先讓你們知道）

### 建立行程：約真人面談

流程的最後一步是「顧問看完 AI 初談報告 → 決定要約真人面談」。
到那一步時，我們會需要把行程寫進 pm 的**行程與會議**。

**pm 已經有行事曆，所以我們不會另外接 Google Calendar。**
多一個行事曆的結果一定是兩邊對不起來，跟人才庫是同一個問題。

到時候會需要一個這樣的端點（規格可以由你們定，這只是需求描述）：

```
POST /api/internal/calendar/events
{
  "title": "初談 — 王大明（BIM工程師）",
  "start": "2026-08-05 14:00:00",
  "duration_min": 30,
  "attendee_consultant": "<顧問識別>",
  "link_to": { "type": "candidate", "id": "<pm 的人才 id>" },
  "note": "AI 初篩報告：<連結或摘要>"
}
```

還會需要一個「讀顧問空檔」的端點，這樣候選人才不會約到顧問已經有事的時間：

```
GET /api/internal/calendar/free?consultant=<識別>&from=...&to=...
```

⚠️ **這兩個都不急。** 要等 AI 初談做出來、真的有報告要看的時候才有意義。
先做前面的職缺清單與應徵同步就好。

---

## 你需要的三個東西

| | 值 |
|---|---|
| **API 位址** | `https://step1ne-recruit-api.aiagentg888.workers.dev` |
| **驗證** | HTTP header：`Authorization: Bearer <SYNC_TOKEN>` |
| **SYNC_TOKEN** | 由 Step1ne 另行提供，**不要寫進前端或版控** |

---

## 端點一：取應徵清單（增量）

```
GET /export/applications?since=<上次的 next_since>&limit=100
Authorization: Bearer <SYNC_TOKEN>
```

**回傳**

```json
{
  "ok": true,
  "count": 1,
  "next_since": "2026-07-29 12:58:06",
  "applications": [
    {
      "id": "74914a51-4f2d-4d3f-a1cd-41f7cea31291",
      "created_at": "2026-07-29 12:58:06",
      "job_slug": "bim-engineer",
      "job_title": "BIM工程師",
      "name": "王大明",
      "email": "someone@example.com",
      "phone": "0912345678",
      "expected_salary": "55K",
      "available_date": "2026-09-01",
      "location_ok": "可接受，無問題",
      "resume_url": null,
      "note": null,
      "utm_source": "threads",
      "utm_medium": "social",
      "utm_campaign": "bim-2607",
      "referrer": null,
      "interview_mode": "now",
      "remind_at": null,
      "status": "ready",
      "consent_at": "2026-07-29 12:58:06",
      "has_resume_file": 0
    }
  ]
}
```

### 增量同步怎麼做

1. 第一次呼叫不帶 `since`（等同從頭拉）
2. 把回傳的 `next_since` 存起來
3. 下次呼叫帶 `since=<上次存的值>`

⚠️ **請用 `next_since`，不要自己算頁碼。**
頁碼會因為中間有新資料插入而錯位，時間游標不會。

⚠️ **`id` 是穩定不變的 UUID，請當成唯一鍵做 upsert。**
同一筆重複拉到時要更新而非新增——網路重試會發生這件事。

### 建議的同步頻率

每 10–15 分鐘一次就夠。應徵量不會大到需要即時。

---

## 端點二：取履歷檔

```
GET /export/resume/<application_id>
Authorization: Bearer <SYNC_TOKEN>
```

回傳**檔案本身**（PDF 或 Word 的 binary），帶 `Content-Type` 與 `Content-Disposition`。

⚠️ **只有 `has_resume_file` 為 `1` 時才呼叫。** 為 `0` 代表沒有上傳檔案，
這時要看 `resume_url`（候選人自己貼的雲端連結，可能為 null）。

履歷檔刻意不夾在列表裡——夾在裡面會讓每次同步都傳一堆用不到的大檔案。

---

## 欄位說明（會影響你怎麼建表）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | UUID 字串 | **唯一鍵**，做 upsert 用 |
| `created_at` / `consent_at` / `remind_at` | `YYYY-MM-DD HH:MM:SS` | **台北時間，沒有時區後綴。** 存進去時請當成 UTC+8 |
| `job_slug` | 字串 | 對應 `step1ne.com/jobs/<slug>`。可能是 `unspecified`（候選人選「請顧問幫我評估」） |
| `job_title` | 字串 | 應徵當下的職缺名稱快照。**職缺日後改名不會回頭改這裡** |
| `interview_mode` | `now` / `later` | 候選人想現在做 AI 初談，或晚點被提醒 |
| `remind_at` | 時間或 null | `later` 時才有值 |
| `status` | 見下表 | |
| `utm_*` / `referrer` | 字串或 null | **來源歸因，請務必保留** |
| `has_resume_file` | 0 / 1 | 決定要不要呼叫端點二 |

### `status` 目前的值

| 值 | 意思 |
|---|---|
| `ready` | 已填表，等著做 AI 初談 |
| `scheduled` | 已填表，約了晚點提醒 |

⚠️ **之後會新增 `interviewed`、`reported` 等值。**
請用「未知值就原樣存下並照常顯示」的寫法，不要用列舉硬擋——
硬擋的話我們一加新狀態，你們這邊就會開始丟資料。

---

## 三件請務必照做的事

### 1. `utm_source` / `utm_medium` / `utm_campaign` 一定要存

這幾欄是判斷「哪條招募管道有效」的唯一依據。
社群、SEO、廣告、LINE 社群四條管道都導到同一張表單，
少了這幾欄就分不出廣告錢花得值不值得。

### 2. 個資要有存取控制

這些是求職者的個人資料，包含 Email、電話、履歷。
候選人同意的範圍是「Step1ne 顧問查閱」——**請限制在顧問角色可見**，
不要放進任何公開頁面或不需登入的 API。

`consent_at` 是他同意的時間戳，請一併保留，那是法律要求。

### 3. 同步是單向的，而且刻意如此

pm 系統裡的編輯不會傳回來，我們這邊也不會覆蓋你們的任何欄位。

**這不是還沒做完，是設計。** 雙向同步在沒有明確主檔的情況下，
一定會走到「兩邊互相覆蓋、最後誰也不知道哪個版本才對」。

同步進來之後，那筆資料就歸 pm 管。
如果之後真的需要把 pm 的處置結果回報給官網（例如 AI 初談要知道這人已被否決），
請先告訴我們，那會用「pm 呼叫我們的一個特定端點」的方式做，不是欄位同步。

---

## 測試方式

先用這個確認接得通（把 `<SYNC_TOKEN>` 換掉）：

```bash
curl -s "https://step1ne-recruit-api.aiagentg888.workers.dev/export/applications?limit=5" \
  -H "Authorization: Bearer <SYNC_TOKEN>"
```

預期看到 `{"ok":true,"count":N,"next_since":"...","applications":[...]}`。

沒帶 token 會回 `{"ok":false,"error":"unauthorized"}`，401。

---

## 給接手這件事的 AI 的補充

實作時請注意：

1. **失敗要看得到。** 同步失敗時請寫進你們的錯誤紀錄或通知，
   不要 catch 之後靜默略過——這種同步一旦無聲失敗，通常幾週後才會有人發現。

2. **記下上次的 `next_since`，而且要持久化。** 存在記憶體裡的話，
   服務重啟就會從頭拉一次，雖然 upsert 不會出錯，但會浪費且掩蓋真正的問題。

3. **先跑一次不寫入的 dry run**，把欄位對應印出來給人看過，再開始真的寫資料庫。

4. **不要幫空欄位補預設值。** `null` 代表候選人沒填，那本身是資訊——
   補成空字串或「未提供」之後，就分不出「沒填」跟「填了空白」。
