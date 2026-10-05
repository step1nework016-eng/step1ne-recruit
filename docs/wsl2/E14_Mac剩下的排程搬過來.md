# E14｜WSL2：把 Mac 剩下的 Step1ne 排程接過來（2026-10-05 19:20）

## 結論先講
Jacky 要 Mac 不要再跑 Step1ne 的東西（Mac 8GB 會變慢）。
Mac 這邊 10/5 19:15 已經停掉 22 個你這台早就在跑的重複排程（E4 第 1～3 批＋ai_worker＋article_publish_tick）。
剩下 4 個只有 Mac 在跑，請你這台裝起來；你回報裝好、跑過一輪，Mac 才會停自己的那份。

## 要裝的 4 個（先 `git pull`，程式都在 step1ne-recruit/）

| 名稱 | 程式 | 多久跑一次 | 備註 |
|---|---|---|---|
| mailboxpoll | `mailbox_poll.py` | 每 5 分鐘 | 讀 official@ 收件匣（只讀、不標已讀）。兩台搶 D1 鎖 `cron:mailbox_poll`，同時跑不會重複 |
| mailoutbox | `mail_outbox_send.py` | 每 60 秒 | 用 GoDaddy 寄一對一信。每封先 `UPDATE … status='sending'` 搶到才寄，兩台同時跑不會重寄 |
| candquestions | `candidate_questions_digest.py` | 每天 21:00 | |
| jobcardsync | `job_card.py` | 每天 09:10、11:10、13:10、15:10、17:10 | E4 卡住的那個，排程補給你了 |

用你現在 Step1ne 排程的同一種方式（systemd user timer）。WorkingDirectory 都設 step1ne-recruit/。

## 信箱密碼：要 Jacky 本人在 WSL2 輸入，你不要碰
mailboxpoll 跟 mailoutbox 需要 `~/.config/workflow-os/step1ne-mailbox.env`。
**不要從 Mac 複製、不要叫 Jacky 貼到對話裡。** 請在回報裡寫一行指令給 Jacky 在 WSL2 終端機自己跑，例如：

```bash
read -s -p "official@ 密碼：" P; printf 'MAIL_IMAP_HOST=imap.secureserver.net\nMAIL_IMAP_USER=official@step1ne.com\nMAIL_IMAP_PASS=%s\n' "$P" > ~/.config/workflow-os/step1ne-mailbox.env; chmod 600 ~/.config/workflow-os/step1ne-mailbox.env; unset P
```
（Mac 版 env 就只有這 3 個欄位，寄信程式也讀同一組。）
⚠️ IMAP 主機一定是 `imap.secureserver.net`，`imap.titan.email` 會登入失敗。

收件進度檔：在 WSL2 建 `~/.config/workflow-os/step1ne-mailbox.state.json`，內容
`{"uidvalidity": "2", "last_uid": 1181}`（Mac 10/5 19:10 讀到這裡）。沒建也沒關係，第一次只會讀當天的信，後台用 Message-ID 去重。

## 不用搬的（Jacky 那邊已經知道）
- `com.step1ne.remind.*`：一次性提醒，發完自己刪，幾乎不吃資源，留 Mac。
- `socialpost`：照 10/2 的規矩「WSL2 主力、Mac 備援」，Mac 留著。
- `geotest`、`weeklyarticle`：跟 AIJOB 共用，不搬。
- 阿財（Mac 的 `com.aijob.interview`）：要不要停，等 Jacky 決定，你先不用動。

## 回報
寫到 `docs/wsl2/回報/E14_日期時間.md` 並 push：
1. 4 個 timer 的 `systemctl --user list-timers` 輸出
2. candquestions、jobcardsync 手動跑一次的最後 10 行 log
3. 給 Jacky 輸入密碼的那一行指令；他輸入後 mailbox_poll 跑一次的輸出（看到 `checked` 或處理幾封）
4. 一句話：「可以停 Mac 的 4 個」或卡在哪裡
