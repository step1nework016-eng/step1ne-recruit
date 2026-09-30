# E4 排程搬家｜補裝 bdfollowupai（2026-09-30）

## 前置

`git pull --rebase origin main`，拉到 `2a35cfc`（AI 跟進建議：過總機用真實發生過的事當
理由，不編造）。

順便發現這次 pull 帶回 `logs/articlepublish.log` 新增內容——是 **Mac 那台**的
articlepublish 產生的（log 裡有 macOS 專屬的 `MallocStackLogging` 訊息），成功發布了兩篇
文章並 push 到官網 repo（`8a0a26d`、`a5001a5`）。這證明「clone 官網 repo → git push deploy」
這套機制本身確實可行，但**這是 Mac 那台跑出來的結果，不是這台**——WSL2 這邊的
articlepublish 還是要等它自己真的遇到一次待發布文章才能確認，這裡先不改口。

## 新增排程：bdfollowupai（`jobintake/bd_followup_ai.py`）

檢查過程式碼：一次性 tick 腳本（沒有 `while True`、沒有 argparse），沒有找到任何直接發送
Telegram／簡訊的呼叫，看起來是產生 AI 跟進建議寫進資料庫的性質，不是會議自動對外聯絡。

建了 `step1ne-bdfollowupai.service` + `.timer`，`OnCalendar=*-*-* 17:30:00`。
`systemd-analyze --user verify` 通過，`enable --now` 後 `list-timers` 確認下次執行時間是
**明天（2026-10-01）17:30**——今天這個時段已經過了，沒有被意外提早觸發。

開機自動復活：沿用已開啟的 `loginctl enable-linger jack`。

## 有沒有改到程式

沒有改 `step1ne-recruit` 裡任何現有程式碼，只新增本機 systemd unit 檔案（不在版控範圍內）
跟這份回報。
