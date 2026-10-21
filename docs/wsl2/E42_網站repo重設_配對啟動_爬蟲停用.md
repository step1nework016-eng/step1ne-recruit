# E42｜WSL2：修網站 repo、啟動配對、停爬蟲（2026-10-22，回 E41）

## 結論先講
謝謝 E41 回報，抓到真問題。Mac 已做的：
- `match_worker.py` 改成 `UPDATE … RETURNING id`，**沒搶到就 continue**，兩台可以同時跑（commit 已 push）。
- `bd_daily_calls.py`：`recruit.env` 沒有就讀 `tokens.env` 的 `RECRUIT_ADMIN_TOKEN`（修 10/18～21 的 401）。
- **jd_regen 暫時回到 Mac 跑**（Mac 推得上去）。Jacky 原本要 Mac 不放 agent，等你這台網站 repo 修好、回報「jd_regen 推得上去」，Mac 再停。
- crawler：**Jacky 決定停用**（8/21 後就沒在找人，已被 AI 找人取代），Mac 已停。

## 請你做（照順序）
1. `git pull`（step1ne-recruit）。
2. **停 crawler**：拿掉 crontab 的 `@reboot` 與每 5 分鐘守護兩行，結束 `app.py` 行程。資料夾留著不要刪。
3. **先停你這台的 jd_regen**（timer/service），避免跟 Mac 搶單又推失敗。
4. **啟動 matchworker**：`systemctl --user enable --now step1ne-matchworker.service`（現在可跟 Mac 同時跑）。
5. **網站 repo 重設**（`~/claude-projects/step1ne-stopgap-site`）：
   - 先備份：`git branch backup-wsl2-20261022 HEAD`（留著那 69 個本地 commit，不刪）。
   - `git fetch deploy && git reset --hard deploy/main`。
   - 確認 `.git/hooks/pre-push`（客戶名守門）還在。
   - 那 69 個 commit 不用補推：裡面的內容（JD 文案修正、上架）都可以用新版範本**從 D1 重產**。
6. **把沒上線的職缺頁重產一次**：D1 `jobs` 裡 `jd_regen_last_error LIKE '%部署失敗%'`（或 `remote contains work`）的職缺，設 `jd_regen_pending=1, jd_regen_last_error=NULL`，然後讓**你這台**的 jd_regen 跑（一次一筆）。這一步做完才算你這台推得上去。
   - 部署要用的 `step1ne-frontend-cf.env`（deploy_direct.sh 用）：如果這台沒有，**不要叫 Jacky 貼對話**，回報裡寫一行讓他自己在終端機建。
7. jobintake 的 `approved` 上架段：等第 6 步成功再接（啟用 `step1ne-jobintake.service`、拿掉只跑 new,rewrite 的那條 cron）。

## 回報
寫到 `docs/wsl2/回報/E42_日期時間.md` 並 push：
1. crawler 停了沒
2. matchworker 狀態
3. site repo 重設後 `git status -sb`（要是 `## main...deploy/main`）
4. 重產了幾個職缺頁、有沒有推成功（列 2～3 個線上網址讓 Mac 驗）
5. 一句話：「Mac 可以停 jdregen、jobintake、matchworker」或卡在哪
