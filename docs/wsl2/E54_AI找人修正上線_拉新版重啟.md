# E54｜AI 找人修正（甲＋乙）上線：拉新版、重啟 ai_worker、重評還原的人（2026-10-28）

## 結論先講
Mac 推了 commit `b85894d`（Jacky 核准的「甲＋乙」）。這台要做三件事：拉新版、重啟 ai_worker、把今天還原的 59 位跑一次重評。

## 改了什麼（這台會跑到的）
- `ai_worker.py` 的 sourced_verify：Cake 抓到登入牆／網站選單、或沒有對應職缺條件 → 標 `verify_status='fetch_failed'`／`'no_job'`，**等第不動**（原本會被改成 D）。
- `sourcing_quality.py`：必要條件切法（清單格式、硬軟拆開、人格特質歸電話確認）。夜間找人（nightly_pipeline）也共用。
- `talent_sourcing_daily.py`：每輪寫一筆 `sourcing_runs`（0 人也算找過）、跳過後台設「不找」的職缺、90 秒內失敗換下一個職缺。
- `talent_sourcing_agent.py`：五人校準沒過照樣找，最多 10 位，標「方向待確認」。
- `regrade_sourced.py --restored`：重評今天還原的人。

## 請 WSL2 做
1. `cd ~/claude-projects/工作流程技能包/step1ne-recruit && git pull`，確認 `git log --oneline -3` 有 `b85894d`。
2. 跑測試：`python3 tests/sourcing_quality_split_test.py`，最後一行要是「失敗 0 項」。
3. 確認沒有人在面談（`python3 ../_tools/d1_readonly.py "SELECT COUNT(*) n FROM applications WHERE interview_state='active'"` 是 0），再重啟 ai_worker（`systemctl --user restart` 這台跑 ai_worker 的那個服務；先 export crontab 那行 PATH）。找人排程（talentsourcing／nightly-sourcing）是定時起新程序，不用重啟，下一輪自動用新版。
4. 重評今天從誤判 D 救回來的人（59 位、一位一個 sonnet、不開工具）：
   先試 `python3 regrade_sourced.py --restored --dry-run --limit 5` 看結果合理，再跑 `python3 regrade_sourced.py --restored`。
   跑完 `SELECT COUNT(*) FROM sourced_candidates WHERE json_extract(raw_json,'$.regrade_pending')=1 AND status='new' AND fit IS NULL AND owner IS NULL` 應該接近 0。
5. 明天 09:00 排班跑完後看 log：被排到的職缺要出現「已記成『找過』」那行，而且 `SELECT target_job,status,candidates_discovered_count FROM sourcing_runs WHERE search_strategy_version='daily-scheduler-v1'` 有新的一筆。

## 回報
寫到 `docs/wsl2/回報/E54_日期時間.md` 並 push：第 1、2 步輸出、重啟時間、重評前後等第分布（regrade 最後一行）、明天排班的那筆 sourcing_runs。
