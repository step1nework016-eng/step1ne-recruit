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
| 寄信給客戶或人選（可附檔） | `consultant_ops.py free-mail --to … --subject … --text-file … [--attach 檔案]… [--who 人選] [--company 公司]` | TG 確認卡，Jacky、Phoebe 或提出的人按 |
| 約電話信、確認電話時間、婉拒信（人選） | `consultant_ops.py call-mail / call-confirm / decline` | 同上 |
| 登記約好的電話＋TG 提醒 | `consultant_ops.py remind --at "YYYY-MM-DD HH:MM" --text … --name …` | 不用 |
| 認識公司：請 AI 研究 | 後台客戶卡片「認識公司」按鈕（工作交給 WSL2 跑） | 不用 |
| 104 職缺找客戶（依職缺開發） | 後台「客戶 / BD → 依職缺開發」；每天由 `hiring_leads_scan.py` 更新 | 不用 |
| 補客戶 Email／電話 | 後台客戶卡片「更新電話／Email」 | 不用 |
| 匿名履歷 | 人選卡片「製作匿名履歷」→ TG 確認 | TG 按確認 |
| 從現有人選配對＋針對職缺匿名摘要 | `consultant_ops.py match --company 公司 --job 職缺slug`（開始）→ 幾分鐘後 `match --run <run_id>`（看結果）→ `match --run <run_id> --brief <application_id>,…`（產匿名摘要，不會自動寄） | 不用；需 WSL2 的 ai_worker 在跑 |
| LinkedIn 查人、送邀請、傳訊息 | 要用 Phoebe 自己的 Chrome（Claude in Chrome 擴充功能），登入她自己的 LinkedIn | 她明確同意才送，一次一位 |

## 三、一定要守的規則（Jacky 的規定，兩邊都一樣）

- **寄信前一定有確認卡**：不要在對話裡反覆問「要寄嗎」，直接擬好最終版，貼確認卡。
- 寄給人選：不能出現「客戶」兩字（改「用人單位」）、年齡性別婚育外貌國籍；客戶公司名保密時絕不外流。
- 找回信：一定同時查 jackychen@ 和 official@ 兩個信箱，也看 LINE。
- 寫日期跟星期：先用 `weekday_check.py` 驗證；客戶名用 `client_name_check.py`。
- 新增 Email 前，網域要確認是真的有收信設定（避免像大魯閣 tarko.com 聽錯網域）。
- 不碰 Jacky 的信箱密碼。要從 jackychen@ 寄信是 Jacky 的專屬通道，Phoebe 的 Claude 一律走 official@ 的自由信件確認卡。

## 四、做不到、需要人動手的地方（不要繞過）

1. `RECRUIT_ADMIN_TOKEN` 要 Jacky 給；沒有它，後台 API 都不能用。
2. Jacky 的信箱 jackychen@（SMTP／IMAP 密碼）**不給**。Phoebe 要查 jackychen@ 的信，請 Jacky 自己看。
3. Cloudflare 金鑰（cf.env）不給；需要改後台程式或部署的事，交給 Jacky 這邊處理。
4. LinkedIn：Jacky 的 LinkedIn 登入是他的，Phoebe 的 Claude 只能用 Phoebe 自己的 LinkedIn。
5. 有附件的信，寄件程式在 Mac 上跑；Mac 關機時，有附件的信會等 Mac 開機才寄。
