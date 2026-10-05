# deliver_wsl2.patch（基準：origin/main `ac87c52`；套用：`git apply docs/wsl2/patches/deliver_wsl2.patch`）

1. **改了什麼**：`_find_chrome()` 多找 `/mnt/c/Program Files*/Google/Chrome/.../chrome.exe` 兩個路徑；`html_to_pdf()` 在 Chrome 路徑以 `.exe` 結尾時（WSL2 借用 Windows 的 Chrome），改把暫存 HTML 與輸出 PDF 放在 `/mnt/c/Windows/Temp/step1ne-chrome-pdf`，用 `wslpath -w` 把路徑轉成 `C:\...` 給 Chrome，印完再複製回呼叫端要的 `out_path`（另加 `import shutil`）。
2. **為什麼只有 WSL2 需要**：WSL2 沒裝 Linux 版 Chrome，只能叫 Windows 的 `chrome.exe`；它是 Windows 行程，看不到 WSL2 的 `/tmp` 與專案路徑，直接 `--print-to-pdf=/home/jack/...` 會印不出來，所以要走 Windows 看得到的資料夾再複製回來。
3. **Mac 上套用會不會有影響**：不會。Mac 的 Chrome 是 `/Applications/Google Chrome.app/...`，不以 `.exe` 結尾，`is_win_chrome=False`，新分支全部不執行；非 `.exe` 時 `win_out_path` 就等於 `out_path`，Chrome 指令列、暫存檔位置、輪詢的檔案都跟原版一樣（已用模擬 Mac 路徑實測：`--user-data-dir=/tmp/step1ne-chrome-pdf`、`--print-to-pdf=<out_path>`、一般 `file://` 網址）；唯一差別是多一次對 `out_path` 的 exists→remove（原本下一行就會做）。

## interview_lock_and_apology_20261002.patch（阿財：鎖過期被搶＋逾時後補道歉訊息）

- 問題：`LOCK_TTL_SEC=90` 比 claude 呼叫上限 `CLAUDE_TIMEOUT=240` 短。呼叫還在跑時鎖就過期，另一個處理者搶進來再回一次；原本那輪逾時後 `handle()` 的 except 還會無條件寫「系統出了點狀況，顧問會聯繫」並發 TG。2026-10-02 胡耀中（15:45 那輪）實際發生：同一則訊息被回一次正常、再收到一則道歉。
- 修法兩處：(1) `LOCK_TTL_SEC = CLAUDE_TIMEOUT + 30`；(2) except 寫道歉前先查最後一則是不是 assistant，是就只記 log、不插訊息、不發 TG。
- 這台**沒有套用**（共用工作目錄，會連帶影響所有程序，且檔案屬於上游）。`git apply --check` 已驗證可套用。套用後阿財要重啟（等沒人面談時）。
- 沒解決的：這台 claude 呼叫為什麼會卡到 240 秒（根因未查到）。

## interview_timeout_retry_20261005.patch（阿財：claude 逾時也重跑一次＋記錄耗時）
- 問題：claude 呼叫送出後沒有模型回應，等滿 240 秒才放棄，阿財直接對候選人道歉並中斷面談（10/2 胡耀中、10/5 陳南宏）。
- 修法：第一次呼叫只給 120 秒，逾時重跑，總預算仍是 240 秒；每次成功記一行耗時與嘗試次數。
- 已驗證：`git apply --check` 通過；測試副本 py_compile＋4 個假 subprocess 情境全過。**這台未套用**，等沒人在面談時由總指揮決定上線；套用後阿財要在沒人面談時重啟。
- `interview_lock_and_apology_20261002.patch` 已被上游採用（不用再套，現在套不上是正常的）；本補丁對最新 `origin/main` `apply --check` 通過。詳見 `docs/wsl2/回報/E12_阿財AI呼叫卡住_20261005-1425.md`。
