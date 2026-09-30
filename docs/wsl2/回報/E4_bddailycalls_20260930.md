# E4 排程搬家｜補裝 bddailycalls（2026-09-30）

## 前置

`git pull --rebase origin main`，拉到 `b1be024`（每天 08:15 推每位顧問今天要打的 45 家，
到期跟進＋照熱度排）。

## 新增排程：bddailycalls（`jobintake/bd_daily_calls.py`）

建了 `step1ne-bddailycalls.service` + `.timer`，`OnCalendar=*-*-* 08:15:00`。
`systemd-analyze --user verify` 通過，`enable --now` 後 `list-timers` 確認下次執行時間是
**明天（2026-10-01）08:15**，今天這個時段已經過了，沒有被意外提早觸發。

開機自動復活：沿用已開啟的 `loginctl enable-linger jack`。

## 有沒有改到程式

沒有改 `step1ne-recruit` 裡任何現有程式碼，只新增本機 systemd unit 檔案（不在版控範圍內）
跟這份回報。
