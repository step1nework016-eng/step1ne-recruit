# WSL2 那台｜Step1ne 背景排程從 Mac 搬過來（分三批）

**白話**：Mac（8GB）同時掛了 26 個 Step1ne 背景排程，整台慢到不能用。Jacky 決定全部搬到這台 WSL2。
規則只有一條：**一次搬一個，在這台確認跑順了，才請 Mac 那邊停掉**。兩台同時跑同一個排程，可能會重複寄信、重複發 TG。

⚠️ **你（WSL2 這台）不要碰 Mac，也不要改資料庫裡的業務資料**。Mac 那邊的停用由 Mac 的 Claude 處理：你寫回報、push 之後，Mac 那邊 pull 就知道哪些可以停。

## 共通做法（每一個都照這個做）

1. `cd ~/claude-projects/工作流程技能包/step1ne-recruit && git pull --rebase origin main`
2. 先查這台有沒有已經在跑同一支程式（`systemctl --user list-units --all | grep step1ne`、`ps aux | grep <檔名>`），有的話記下來，不要重複裝。
3. 照下表的「時間／頻率」建 systemd user service。常駐類型（下表寫「常駐」）用 `Restart=always`；定時類型用 `.timer`，`OnCalendar=` 寫成跟下表一樣的時間，`Persistent=true`。
   - 每一個 service 都要加 `Environment=TZ=Asia/Taipei`，以及 `WorkingDirectory=%h/claude-projects/工作流程技能包/step1ne-recruit`。
   - PATH 要包含 `which claude` 所在的資料夾（很多支會呼叫 claude CLI）。
   - log 寫到 `%h/aijob-automation/logs/<名稱>.log`。
4. **手動跑一次**（常駐的看 log 有沒有正常起來；定時的用 `systemctl --user start <名稱>.service` 觸發一次），確認 log 沒有錯誤。
   - 有 `--dry-run` 參數的，優先用 dry-run。
   - 會寄信、會發 TG、會部署網站的那幾支（下表有標 ⚠️），**不要手動觸發**，只確認 service 載入成功，等排定的時間自然跑一次。
5. 每搬完一批，寫一份回報到 `docs/wsl2/回報/E4_第N批_<日期>.md`，列出：
   - 哪幾個已經在這台跑順了（寫「可以停 Mac」）
   - 哪幾個卡住了、原因是什麼（例如缺憑證、缺套件）

   寫完 git add、commit、push。
6. 開機自動復活：常駐類型要跟 socialpost 一樣，掛進「登入時喚醒」那支 VBS 的清單。

## 第一批｜最吃資源（常駐或每 1～5 分鐘就跑、會呼叫 claude）

| 名稱（Mac 上的） | 程式 | 時間／頻率 |
|---|---|---|
| aiworker | `ai_worker.py` | 常駐（客戶履歷、逐字稿評分等 AI 工作佇列）※ 記憶裡寫這台之前可能已經有在跑，先查 |
| clientreport | `client_report_tick.py --once` | 每 60 秒（會用 Chrome 產 PDF；deliver.py 你們這邊已經修過 win_out_path） |
| report | `report_tick.py` | 每 5 分鐘 |
| callreport | `consultant_call_report_tick.py --once` | 每 5 分鐘 |
| jdaidraft | `jobintake/jd_ai_draft_tick.py` | 每 90 秒 |
| jdregen | `jobintake/jd_regen_tick.py` | 常駐 |
| portalimport | `jobintake/portal_import_tick.py --loop` | 常駐 |
| articlepublish | `article_publish_tick.py` | 常駐 ⚠️（會上線文章，需要網站 repo 的推送權限） |

## 第二批｜頻繁的小工作

| 名稱 | 程式 | 時間／頻率 |
|---|---|---|
| bd | `jobintake/bd_tick.py` | 每 5 分鐘 ⚠️（開發信流程） |
| closesync | `close_sync.py --deploy` | 每 10 分鐘 ⚠️（會部署 step1ne.com，需要網站 repo 與 wrangler 憑證；部署前一定要跑 predeploy.sh 客戶名守門員） |
| medic | `step1ne_medic.py` | 每 30 分鐘 |
| checkup | `run_checkup.sh` | 常駐（阿福）※ 記憶裡寫這台可能已經有，先查 |
| bddailyreport | `jobintake/bd_consultant_daily.py` | 每天 18:00 ⚠️（發 TG，可以跑 `--dry-run`） |

## 第三批｜每天、每週各一次

| 名稱 | 程式 | 時間 |
|---|---|---|
| candidatesummary | `jobintake/candidate_summary_tick.py` | 每天 09:10 |
| jobcardsync | `job_card.py --sync` | 每天 09:10、11:10…（照 Mac 的 plist；Mac 那邊可以把完整時間表寫給你） |
| topicresearch | `jobintake/topic_research_tick.py` | 每天 08:30 |
| talentsourcing | `talent_sourcing_daily.py` | 每天 09:00 |
| bdfollowup | `jobintake/followup_tick.py` | 週一到週五 09:30 ⚠️（追信） |
| bdexpansion | `jobintake/bd_expansion_tick.py` | 週一 09:45 |
| jobcopy | `job_copy_loop.sh` | 週一 09:00 |
| threadsrefresh | `refresh_threads_token.sh` | 週一 08:00 |
| placementtracker、talentsourcingtg、socialpost | — | **這台已經有了**（2026-09-17 搬過）。只要確認還在跑，然後寫進回報「可以停 Mac」 |

以下兩支是 AIJOB 共用的腳本，這次**不搬**：`weeklyarticle`、`geotest`。

## 回報
每一批寫一份，要照上面第 5 點的格式；有驗收沒測到的，要寫明「沒測到」。
