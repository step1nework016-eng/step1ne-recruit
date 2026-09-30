# E4 補充回報｜dashboard 加入新排程＋ __pycache__ 造成自動更新失敗（2026-09-30）

## 1. WSL2 本機 dashboard（http://localhost:5088/）已加入新排程

`~/aijob-automation/dashboard.py`（本機檔案，不在版控內；舊版備份 `dashboard.py.bak-0930`）：

- **常駐區新增 2 張卡**：阿財面談(WSL2)★（`python3 interview_daemon.py`）、職缺入口匯入
  （`portal_import_tick.py --loop`）。這兩支是 systemd 常駐服務，沿用原本的 pgrep 監控。
- **新增「定時排程（systemd timer）」區塊，共 20 支**：report、callreport、jdaidraft、bd、
  closesync、medic、bddailycalls、topicresearch、talentsourcing、candidatesummary、
  bdfollowup、jobcardsync、bdfollowupai、bddailyreport、nightly-bd、nightly-sourcing、
  morning-summary、threadsrefresh、jobcopy、bdexpansion。每張卡顯示頻率、下次執行、上次執行、
  最近幾行 log；上次執行失敗或 timer 沒啟用會標紅，標題列會統計異常數。
- **為什麼不用 pgrep**：這些是「跑完就結束」的一次性工作，pgrep 會一直誤判成沒在跑。
  改讀 systemd 自己的紀錄（`systemctl --user show` 取 Result／ActiveState，
  `list-timers --output=json` 取實際的下次／上次時間，systemd 259 支援）。
- **驗證**：用 `env -i`（模擬 cron、沒有 `XDG_RUNTIME_DIR`）測過，20 支都抓得到；實際重啟線上
  dashboard 後，在瀏覽器確認頁面渲染正常（20 支定時排程 · 全部正常）。
  `systemctl --user` 在 cron 環境需要 `XDG_RUNTIME_DIR`，程式內已自行補上。
- 之後每新增一支 timer 型排程，要在 `dashboard.py` 的 `TIMERS` 清單手動加一行，
  不會自動偵測。

## 2. 發現：`__pycache__` 被版控追蹤，造成自動更新反覆失敗（建議 Mac 那邊處理）

**現象**：dashboard 上看到阿福、AI工作佇列、阿財三支在 17:41～17:42 出現「自動更新檢查失敗：
`git pull origin main --quiet` returned non-zero exit status 1」。錯誤內容是：
`jobintake/__pycache__/*.pyc` 有本機修改，`git pull` 因此中止（Please commit your changes
or stash them）。

**原因**：`jobintake/__pycache__/*.pyc`（以及 `jobtpl/__pycache__/`）是**被 git 追蹤的檔案**。
只要在這台跑過 `jobintake/` 底下的程式（timer 排程每幾分鐘就跑一次），Python 就會重寫這些
`.pyc`，工作目錄變髒；下一次 `autoupdate.py` 做 `git pull` 時，如果上游剛好也改到同一批
`.pyc`，就會被「本機有未提交修改」擋下。17:52 之後恢復是因為我手動 `git checkout --
jobintake/__pycache__` 清掉了。

**風險**：只要有人（或任何排程）再改動並推送 `.pyc`，這台的自動更新就會再卡一次，
而且是靜默失敗（只有 log 一行警告），程式會停在舊版本。今天新增了十幾支每幾分鐘就跑的
`jobintake/` 排程，觸發頻率比以前高很多。

**建議（要不要做由 Jacky／Mac 決定）**：
1. 把 `__pycache__/` 加進 `.gitignore`，並 `git rm -r --cached` 移除已追蹤的 `.pyc`
   （Mac 那邊之前提過想加，還沒加）。這是根本解法。
2. 或在這台的 systemd unit／crontab 加 `Environment=PYTHONDONTWRITEBYTECODE=1`，讓程式
   不再寫 `.pyc`（只治本機，不改上游）。

這次沒有動任何版控內容，等你決定。

## 3. 順帶提醒

- 今晚 22:00 夜間開發客戶（`step1ne-nightly-bd.timer`）會第一次「正式排程」跑，上限 30 家
  （驗收時是 3 家）。明天 08:30 早報會有結果。

## 有沒有改到程式

沒有改 `step1ne-recruit` 裡任何程式碼；只改本機的 `dashboard.py`（不在版控內）跟新增這份回報。
