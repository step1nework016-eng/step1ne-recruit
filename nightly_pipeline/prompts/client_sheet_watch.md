# Step1ne 每個工作日 10:00／16:00：用 Chrome 巡檢遊戲橘子的用人需求表

你在 Windows 那台電腦的 Claude 桌面版裡跑，這是排程任務，旁邊沒有人。
任務：打開客戶的用人需求試算表，**照人眼看到的畫面**記下「現在還在招的缺」，交給程式跟上次比對，有變化就推 TG。
你**只看資料，不改試算表、不關職缺、不上架職缺、不聯絡任何人**。

表單網址：https://docs.google.com/spreadsheets/d/1UXyQKuZlm5n3Hbl2-dB759qjSMfjXXzeyjaQxHwqZD4/edit

---

## 為什麼一定要看畫面（最重要）

客戶不會通知我們缺有沒有變，是直接改表：**不招的缺會被隱藏——整個分頁藏起來，或分頁裡某幾列藏起來**。
用 Google Drive 讀檔或匯出，會把隱藏的列一起讀出來，等於把不招的缺當成在招（2026-10-01 就差點這樣算錯）。
所以：**只算「看得到的分頁」裡「看得到的列」**。

---

## 瀏覽器規則

0. 先用 `ToolSearch` 載入 `select_browser`，再呼叫它，deviceId 寫死 `7d76c8d4-d2ea-47d3-b19e-a99ff8db6deb`（Windows 這台的 Chrome）。
   不可選別的 Chrome、不呼叫 `switch_browser`、不問人。失敗（那台沒連線）→ 不改用別台，直接結束，回覆寫「指定的 Chrome 沒有連線，今天沒有巡檢」。
1. 用自己新開的分頁（`tabs_create_mcp`），不要動原本開著的分頁。結束時關掉你開的分頁。
2. 試算表是「僅供檢視」。**不要點任何會修改內容的地方**，不要按「要求編輯權限」。
3. 打不開、要登入、顯示沒有權限 → 不要嘗試登入，直接結束，回覆寫清楚看到什麼畫面。

---

## 第一步：哪些分頁看得到

打開表單，等 5 秒，用 javascript 執行：
```js
[...document.querySelectorAll('.docs-sheet-tab')].map(t => ({name: t.innerText.trim().replace(/^0/, ''), hidden: t.offsetParent === null}))
```
- `hidden: false` 的是**看得到的分頁** → 第二步要逐一看
- `hidden: true` 的是**被隱藏的分頁** → 只記名字，放進 `hidden_tabs`，內容不要讀

## 第二步：每個看得到的分頁，哪些列看得到

點分頁標籤切過去，等 2 秒，對**左邊列號那一欄**做放大截圖（`zoom`，region 大約 `[0, 120, 900, 870]`），自己看列號：
- 列號連續（1、2、3…）＝都看得到
- **列號跳號＝中間被隱藏**。例如出現 2、4、8，代表 3、5、6、7 被隱藏，那幾列不算
- 第 1 列是表頭；表頭以下、看得到、而且**有填職位名稱**的列才是「在招的缺」
- 畫面放不下就往下捲再截，直到出現連續的空白列

## 第三步：讀每個看得到的缺的完整內容

格子裡的字常常被截斷，**一定要點開每一格讀完整內容**：
1. 用 `find` 找到左上角的「名稱方塊」（顯示目前儲存格位址的那一格，例如 A1）
2. 對它 `triple_click` → `type` 儲存格位址（例如 `C8`）→ 按 `Return`
3. 用 javascript 讀上方公式列的完整文字：
   ```js
   (document.querySelector('#t-formula-bar-input .cell-input') || document.querySelector('#t-formula-bar-input')).innerText
   ```
4. 表頭那一列也要讀（第 1 列每一欄的名稱），讀到的內容用「表頭名稱」當欄位名

每一列都要讀「職位名稱」以及其他所有有內容的欄位。

## 第四步：整理成 JSON

```json
{
  "sheet_id": "1UXyQKuZlm5n3Hbl2-dB759qjSMfjXXzeyjaQxHwqZD4",
  "client_company_id": "co_a732b59e",
  "hidden_tabs": ["被隱藏的分頁名稱", "…"],
  "tabs": [
    {"name": "優服客服", "rows": [
      {"row": 8, "辦公室": "內湖", "部門/組別": "支付服務組", "職位名稱": "支付客服", "班段": "中班", "上班時段": "14:00~23:00", "薪資": "…完整內容…"}
    ]}
  ]
}
```
- `tabs` 只放看得到的分頁；每個分頁的 `rows` 只放看得到、有職位名稱的列
- 欄位內容照公式列讀到的完整文字，不要自己改寫、不要摘要、不要省略

## 第五步：交給程式比對＋推 TG

1. 查 Windows 暫存資料夾：`cmd.exe /c echo %TEMP%`
2. 把 JSON 存成 `<暫存資料夾>\step1ne_gamania_sheet_今天日期時間.json`（UTF-8）
3. 先試跑（路徑的 `\` 全換成 `/`）：
   ```
   wsl.exe -- bash -lc "cd ~/claude-projects/工作流程技能包/step1ne-recruit && python3 nightly_pipeline/client_sheet_watch.py --file 'C:/Users/…/step1ne_gamania_sheet_….json' --dry-run"
   ```
   ⚠️ 如果試跑結果說「不見了的缺」超過 3 個，或「看得到的缺」是 0 個，**很可能是你讀錯了**（例如分頁沒切過去、截圖沒捲到）。回頭重看一次，確認無誤才繼續。
4. 正式存檔並推 TG：
   ```
   wsl.exe -- bash -lc "cd ~/claude-projects/工作流程技能包/step1ne-recruit && python3 nightly_pipeline/client_sheet_watch.py --file 'C:/Users/…/step1ne_gamania_sheet_….json' --notify"
   ```
   沒有變化時程式不會發 TG，這是正常的。**不要用其他方式發 TG。**

## 第六步：收尾

1. 用 `tabs_context_mcp` 確認你開的分頁都關掉了
2. 最後回覆一段話：看得到幾個缺、隱藏幾個分頁、程式回報了哪些變化、有沒有哪一格讀不到
