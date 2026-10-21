# E41｜WSL2：把 Mac 最後三個常駐程式接過來（2026-10-22）

## 結論先講
Jacky：這台 Mac 會隨身帶著跑、隨時關機，**Mac 上盡量不要放 AI agent**。
Mac 這邊 10/22 已經停掉你這台早就在跑的 3 個重複程式：checkup（阿福）、portalimport、articlepublish（plist 改名成 `.plist.disabled`，重開機也不會自己回來）。

⚠️ 順便說明之前為什麼「Mac 停了又跑回來」：9/30、10/5 用 `launchctl bootout` 停掉的程式，plist 還留在 `~/Library/LaunchAgents/`，Mac 一重開機就全部自動載回來。這次改成**把 plist 改名**才算真的停用。

剩下 3 個只有 Mac 在跑，請你這台裝起來。你回報裝好、跑過一輪，Mac 才停自己那份。

## 要裝的 3 個（先 `git pull`）

| 名稱 | 程式 | 怎麼跑 | 備註 |
|---|---|---|---|
| jobintake | `step1ne-recruit/jobintake/tick.py` | 常駐：每 60 秒跑一次（Mac 是 `while :; do python3 tick.py; sleep 60; done`） | 顧問送出職缺收件單 → 擬 JD → 核准後產職缺頁上線。**會 push 網站 repo**，你這台 jd_regen 已經能推 step1ne-stopgap-site，用同一套設定 |
| matchworker | `step1ne-recruit/match_worker.py` | 常駐（KeepAlive） | /match/ 幫你配職缺的第二段：用 Claude 把規則初選結果改寫成人話。需要 Claude CLI（你這台已有） |
| crawler | `~/clawd/headhunter-crawler/app.py`（repo：github.com/jacky6658/headhunter-crawler） | 常駐 Flask，port 5000，用它自己的 venv | **先問 Jacky 還需不需要**（見下方） |

用你現在 Step1ne 常駐程式的同一種方式（systemd user service，Restart=always）。

## 兩台同時跑會不會打架
- jobintake：**會**。兩台同時撿同一張收件單可能重複擬 JD／重複上線。請先確認 `tick.py` 有沒有搶單鎖（看它怎麼改 status）；沒有的話，**等 Mac 停了你再啟動**，回報裡寫「已準備好、等 Mac 停」即可，不要兩台同時跑。
- matchworker：先看 `match_worker.py` 撿工作的方式；若是 `UPDATE … WHERE status='pending'` 搶到才做就可以同時跑，否則同上等 Mac 停。

## crawler 特別注意
- 這支有本機資料：`data/`、`resumes/`、`exports/`、`credentials.json`。**不要叫 Jacky 把 credentials 貼到對話**，也不要從 Mac 複製密碼類檔案。
- 請在回報裡問 Jacky 一句：「爬蟲（headhunter-crawler）還要繼續用嗎？」——如果不用了，兩台都直接停，不搬。
- 要搬的話，資料夾怎麼過去由 Jacky 自己決定（隨身碟或雲端），你只寫清楚要哪些資料夾。

## 回報
寫到 `docs/wsl2/回報/E41_日期時間.md` 並 push：
1. 3 個 service 的狀態（`systemctl --user status` 摘要）
2. jobintake、matchworker 有沒有搶單鎖；能不能跟 Mac 同時跑
3. jobintake 跑一輪的最後 10 行 log（沒有待處理收件單也沒關係，要看到它正常結束）
4. crawler：Jacky 要不要繼續用、要搬哪些資料
5. 一句話：「可以停 Mac 的 jobintake／matchworker（／crawler）」或卡在哪裡
