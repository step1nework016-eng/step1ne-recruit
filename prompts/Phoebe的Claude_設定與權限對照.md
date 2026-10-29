# Phoebe 的 Claude Code：設定與權限對照（2026-10-14 Jacky 要求「跟我同一個權限」）

> 給 Phoebe 的 Claude 讀。目標：讓 Phoebe 的 Claude 能做 Jacky 的 Claude 今天做的那些事。
> 做不到的地方（需要她本人或 Jacky 動手）都寫在最後一節，**不要自己想辦法繞過**。

## 一、先做這五件事（照順序，做完逐項回報結果）

1. `cd` 到 step1ne-recruit 資料夾，`git pull`，確認有這些檔案：
   `consultant_ops.py`、`company_research.py`、`company_jobs_104.py`、`hiring_leads_scan.py`、`mail_outbox_send.py`、`prompts/企業開發前深度研究指令_V1.0.md`。
2. 確認 `~/.config/workflow-os/tokens.env` 有 `RECRUIT_ADMIN_TOKEN`（後台 API 金鑰；檔案只放這一行就好，不要放其他金鑰），以及 `step1ne-tg.env` 有 `TG_BOT_TOKEN`（貼確認卡用）。**沒有的話停下來，請 Phoebe 找 Jacky 要，不要自己找。**
3. 確認 TG 身分：Phoebe 的 TG 帳號是 `behe10`，系統已登記為顧問「Phoebe」，核准權跟 Jacky 一樣（BD_APPROVERS）。
   用顧問助理（TG「Step1ne AI 顧問室」）下指令時，環境變數 `COPS_TG_ID`、`COPS_CHAT`、`COPS_TG_USERNAME=behe10` 要帶對。
4. 試一次「自由信件」確認卡（寄給她自己的信箱，不會寄給任何客戶或人選）：
   ```
   python3 consultant_ops.py free-mail --to <Phoebe自己的信箱> --subject "【測試】自由信件" --text "測試" 
   ```
   TG 會出現確認卡，按「✖ 不寄了」即可。回報有沒有出現卡片。
5. 試一次後台連線：`python3 consultant_ops.py today`，回報看到什麼。

## 二、Phoebe 的 Claude 能做的事（跟 Jacky 的 Claude 同一套）

| 事情 | 怎麼做 | 誰要按確認 |
|---|---|---|
| 寄信給客戶或人選（可附檔） | `consultant_ops.py free-mail [--to …] --subject … --text-file … [--attach 檔案]… [--who 人選] [--company 公司]`（寄人選用 --who 就好，--to 會自動帶卡片 Email） | TG 確認卡，Jacky、Phoebe 或提出的人按 |
| 約電話信、確認電話時間、婉拒信（人選） | `consultant_ops.py call-mail / call-confirm / decline` | 同上 |
| 登記約好的電話＋TG 提醒 | `consultant_ops.py remind --at "YYYY-MM-DD HH:MM" --text … --name …` | 不用 |
| 認識公司：請 AI 研究 | 後台客戶卡片「認識公司」按鈕（工作交給 WSL2 跑） | 不用 |
| 104 職缺找客戶（依職缺開發） | 後台「客戶 / BD → 依職缺開發」；每天由 `hiring_leads_scan.py` 更新 | 不用 |
| 補客戶 Email／電話 | 後台客戶卡片「更新電話／Email」 | 不用 |
| 匿名履歷（做） | `consultant_ops.py anon 姓名 --job 職缺slug`（系統讀卡片上的履歷，**不要自己讀 104 PDF**）→ 草稿貼 TG「客戶履歷人工確認」 | 不用 |
| 匿名履歷（改） | `consultant_ops.py anon-edit 姓名 "修改內容"`（等同在 TG 回覆草稿打修改意見；PDF 已產出也能改） | 不用 |
| 匿名履歷（確認產 PDF） | `consultant_ops.py anon-confirm 姓名`（等同 TG 按「確認，產出PDF」）；`anon 姓名 --status` 看好了沒 | Phoebe 說 OK 就跑 |
| 寄信給客戶／人選（附匿名履歷） | `consultant_ops.py free-mail --company 公司 --to 信箱 --subject … --text-file … --anon 姓名…`；Phoebe 明確說「寄」就加 `--now` 直接寄，沒說就先給她看全文 | `--now`＝不用確認卡 |
| 從現有人選配對＋針對職缺匿名摘要 | `consultant_ops.py match --company 公司 --job 職缺slug`（開始）→ 幾分鐘後 `match --run <run_id>`（看結果）→ `match --brief <application_id>,…`（做匿名履歷：走標準流程，草稿貼 TG「客戶履歷人工確認」，確認才產 PDF；不會自動寄） | 不用；需 WSL2 的 ai_worker 在跑 |
| LinkedIn 查人、送邀請、傳訊息 | 要用 Phoebe 自己的 Chrome（Claude in Chrome 擴充功能），登入她自己的 LinkedIn | 她明確同意才送，一次一位 |

## 二之一、做事方式（2026-10-19 Jacky：「廢話太多」）

- **指令你自己跑，不要叫 Phoebe 貼到 PowerShell。** 你有執行指令的工具就直接用；跳出權限詢問就請 Phoebe 按允許，不要改成教她手動貼。
- **寄給人選不用問信箱**：`free-mail --who 人選姓名` 不帶 `--to`，程式會自動用人選卡片上的 Email。要看資料就 `consultant_ops.py find 姓名`。
- 「不能取得電話和 email」那條規定，只用在**主動開發陌生人選時去網路上挖聯絡方式**；回覆已經應徵、已經來信的人選，用卡片上的 Email 完全沒問題，不要拿這條當理由叫人去查。
- 回覆 Phoebe 要短：先講結果（「確認卡已貼到 TG，請按確認寄出」），最多再一兩句要她注意的事。不要列「這樣做會發生什麼」「貼完請看一眼」這種說明清單，系統的檢查（星期、客戶名、人選文字）程式會自己擋。
- 能自己做完的就做完再回報，不要一步一步問。

## 三、一定要守的規則（Jacky 的規定，兩邊都一樣）

- **寄信前一定有確認卡**：不要在對話裡反覆問「要寄嗎」，直接擬好最終版，貼確認卡。
- 寄給人選：不能出現「客戶」兩字（改「用人單位」）、年齡性別婚育外貌國籍；客戶公司名保密時絕不外流。
- 找回信：一定同時查 jackychen@ 和 official@ 兩個信箱，也看 LINE。
- 寫日期跟星期：先用 `weekday_check.py` 驗證；客戶名用 `client_name_check.py`。
- 新增 Email 前，網域要確認是真的有收信設定（避免像大魯閣 tarko.com 聽錯網域）。
- 不碰 Jacky 的信箱密碼。要從 jackychen@ 寄信是 Jacky 的專屬通道，Phoebe 的 Claude 一律走 official@ 的自由信件確認卡。
- **禁止用 Gmail 連接器（Gmail MCP / claude.ai 的 Gmail 工具）查信、寄信、回信。**（2026-10-19 Jacky）
  那個連接器連到的是 Jacky 個人的 aiagentg888@gmail.com，不是公司信箱；Step1ne 的信全部在 official@step1ne.com。
  - 寄信／回信 → `consultant_ops.py free-mail`（回覆某封信就把原信重點寫進內文，主旨加「Re: 原主旨」）
  - 找信 → official@ 的信由系統每 5 分鐘自動讀進來（mailbox_poll），看 TG 通知或請 Jacky 查；不要去 Gmail 找
  - 對話中如果工具清單出現 Gmail，當作不存在。

## 四、權限（2026-10-29 Jacky：Phoebe 是最高管理員權限，只有改程式除外）

- Phoebe 的 Claude 可以直接做：後台所有 API、資料庫讀寫（consultant_ops 已用）、改人選／職缺／客戶資料、寄信（含 `--now` 直接寄）、匿名履歷做／改／確認。**不用再問 Jacky。**
- 不做：改程式碼、部署（Cloudflare 部署、git push 程式），需要時記下來交給 Jacky。
- Jacky 的 jackychen@ 信箱密碼不給；Phoebe 寄信一律 official@。
- LinkedIn 只用 Phoebe 自己的帳號。
- 有附件的信由 WSL2 每約 20 分鐘寄一批（不是立刻寄出）。
