# E4 排程搬家｜jobcardsync 補裝回報（2026-09-30）

## 前置

`git pull --rebase origin main`，確認 HEAD 為 `de63789`（18:00 日報加「明天要跟進的客戶」；
medic 在非 Mac（WSL2）跳過 launchctl 檢查）。順便確認：批次二回報裡提到的
`check_schedules` 呼叫 macOS 專用 `launchctl` 在 WSL2 報錯的問題，已經在這個 commit 修好，
不用再處理。

## jobcardsync（`job_card.py --sync`）

拿到 Jacky 給的完整時刻表：**每天 09:10、11:10、13:10、15:10、17:10、19:10 各跑一次**。

建了 `step1ne-jobcardsync.service` + `.timer`，`.timer` 用六條 `OnCalendar=` 各自對應一個
時間點。`systemd-analyze --user verify` 通過，`enable --now` 後 `list-timers` 確認下次
執行時間是今天 19:10（今天最近一個還沒過的時段），沒有被意外提早觸發。

開機自動復活：沿用已開啟的 `loginctl enable-linger jack`。

## 有沒有改到程式

沒有改 `step1ne-recruit` 裡任何現有程式碼，只新增本機 systemd unit 檔案（不在版控範圍內）
跟這份回報。

## 進度更新

原本 20 支 Mac 排程，加上這支之後：
- 已確認可以跟 Mac 說停掉的：11 支（同前三批回報）
- 新裝、等排定時間自然驗證的：12 支（前三批的 11 支 + 這支 jobcardsync）
- 待驗證才能說可以停 Mac 的：articlepublish
- 刻意不搬：weeklyarticle、geotest

全部 20 支已經有對應處理，只剩 articlepublish 等真實流量驗證。
