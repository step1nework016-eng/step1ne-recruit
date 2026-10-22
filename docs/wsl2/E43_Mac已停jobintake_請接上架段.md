# E43｜WSL2：Mac 已停 jobintake／matchworker／jdregen，請接上架段（2026-10-22 早上）

E42 回報驗收 OK（key-account-sales-neihu 線上 200）。Mac 已把 jdregen、matchworker、jobintake 三個 plist 改名停用。
**Mac 現在只剩阿財（com.aijob.interview）**。

請你：
1. `systemctl --user enable --now step1ne-jobintake.service`（三段都跑）
2. 拿掉 crontab 裡「只跑 new,rewrite」那條 jobintake（先備份 crontab）
3. 確認你這台 jd_regen 有在跑（E42 有沒有重新開回來）
4. 回報寫到 `docs/wsl2/回報/E43_日期時間.md`：三個服務狀態＋jobintake 跑一輪的最後 5 行 log
