# Step1ne 每天 07:00：用 Chrome 到 104 補客戶的現開職缺與招募窗口

**本次上限：20 家**（驗收時把這個數字改成 3）

你在 Windows 那台電腦的 Claude 桌面版裡跑，這是每天早上的排程任務，旁邊沒有人。
任務：幫開發名單上的公司，到 104 看「現在開了哪些職缺」和「招募聯絡人／電話」，寫回名單，最後發一則 TG 摘要。
你**只看資料、只寫回名單，不聯絡任何人**。

---

## 瀏覽器規則（Jacky 特別交代，一條都不能破）

1. **用自己新開的分頁**（`tabs_create_mcp`）。不要動 Jacky 原本開著的任何分頁。
2. **查完一家就關掉那一家的分頁**（`tabs_close_mcp`），下一家再開新的。
3. **全部查完，把你開過的分頁關乾淨**：最後用 `tabs_context_mcp` 看一次，確認你開的分頁一個都沒留下。
4. **不登入、不應徵、不點任何「應徵」「聯絡」「儲存」「關注」「我要應徵」「聯絡公司」按鈕**，也不填任何表單。只看、只讀文字。
5. **遇到人機驗證（驗證碼、「我不是機器人」、畫面要你拼圖或選圖）就停下來**：
   不要嘗試通過，關掉分頁，把這一家記成 `"status": "captcha"`，**後面的公司也不要再查了**，直接跳到第四步寫回和回報。
6. 不要去 104 以外的網站。每家最多開 3 個頁面（公司頁、工作機會、必要時一次 104 站內搜尋）。

---

## 第一步：拿今天的名單

在終端機執行（PowerShell 或 Git Bash 都可以）：
```
wsl.exe -- bash -lc "cd ~/claude-projects/工作流程技能包/step1ne-recruit && python3 nightly_pipeline/cards_need_104.py --limit 20"
```
（上面的 20 要跟最上面「本次上限」一致。）
會拿到一個 JSON 陣列，每家有 `company`（公司正式名稱）、`company_104_url`、`hiring_head`（名單上現在寫的徵才資訊）、`priority`、`task`。
名單是空的 → 直接跳到第四步，發「今天沒有要補的公司」。

## 第二步：逐家查

每一家：
1. 開新分頁。
2. **有 `company_104_url`**：直接打開那個網址。
   **沒有**（priority 2）：打開 https://www.104.com.tw ，用上方搜尋切到「公司」，搜公司全名。
   只有**名稱完全一樣**的才算找到，把它的公司頁網址（`https://www.104.com.tw/company/…`）記下來；找不到就記 `"status": "not_found"`，關分頁換下一家。
3. 在公司頁的公司介紹裡，找「**聯絡人**」和「**電話**」（通常在公司基本資料那一區）。有寫才記，沒寫就留空，**不要猜**。
4. 點「**工作機會**」分頁，讀職缺清單：
   - 記下頁面上寫的**職缺總數**（例如「99+」或「12」）。
   - 讀**前 20 個**職缺的職稱、地點、經歷、學歷。
   - 把這 20 個**歸成幾類**，一類一行，格式照這個例子：
     `設備工程師／資深（黃光、蝕刻、薄膜）— 新竹廠、桃園廠，經歷不拘，大學／專科，要輪夜班`
     `技術開發工程師 — 新竹，2 年以上，碩士`
5. 關掉這個分頁。

## 第三步：整理成 JSON

每一家一筆（查到、卡住、找不到都要有一筆）：
```json
[
  {
    "company": "跟名單上一字不差的公司名",
    "status": "ok",
    "company_104_url": "https://www.104.com.tw/company/xxxx",
    "contact_name": "104 公司頁上的聯絡人（例：人才招募部 王小姐）",
    "contact_phone": "104 公司頁上的電話",
    "jobs_total": "99+",
    "categories": ["分類一行", "分類一行"]
  },
  {"company": "某某股份有限公司", "status": "captcha", "note": "打開工作機會就跳驗證"},
  {"company": "某某有限公司", "status": "not_found", "note": "104 搜不到同名公司"}
]
```
`status` 只能是 `ok`、`captcha`、`not_found`、`error` 其中一個。沒查到的欄位就不要給，不要編。

## 第四步：寫回名單＋發 TG 摘要

1. 先查 Windows 的暫存資料夾在哪：
   ```
   cmd.exe /c echo %TEMP%
   ```
2. 用寫檔工具把上面的 JSON 存成 `<暫存資料夾>\step1ne_104_今天日期.json`（例：`C:\Users\jack\AppData\Local\Temp\step1ne_104_20261001.json`），存成 UTF-8。
3. 先試跑一次看會改什麼（把路徑的 `\` 全部換成 `/`）：
   ```
   wsl.exe -- bash -lc "cd ~/claude-projects/工作流程技能包/step1ne-recruit && python3 nightly_pipeline/save_104.py --file 'C:/Users/jack/AppData/Local/Temp/step1ne_104_20261001.json' --dry-run"
   ```
   看起來沒問題（公司都找得到、沒有亂碼）再正式寫回並發 TG：
   ```
   wsl.exe -- bash -lc "cd ~/claude-projects/工作流程技能包/step1ne-recruit && python3 nightly_pipeline/save_104.py --file 'C:/Users/jack/AppData/Local/Temp/step1ne_104_20261001.json' --notify"
   ```
   TG 會自動發到「📬 開發信 開信・回信」主題，內容包含：查了幾家、補到幾個招募窗口、哪幾家卡住。
   **不要用其他方式發 TG 或發任何訊息。**
4. 如果寫回失敗（出現 ❌），不要重試超過 1 次；把錯誤訊息留在最後的回覆裡。

## 第五步：收尾

1. 用 `tabs_context_mcp` 確認你開過的分頁全部關掉了。
2. 最後回覆一段話（留在這次排程的紀錄裡）：
   - 查了幾家、寫回幾家、補到幾個招募窗口電話
   - 卡住的是哪幾家、為什麼（人機驗證的那家要特別寫出來）
   - 分頁是否已經全部關掉
