# E4 排程搬家｜jdregen / closesync 故障排除回報（2026-09-30 19:33）

## jdregen（`jd_regen_tick.py`，最後處理停在 14:33）

**根因**：不是 crontab 設定的問題。今天稍早（修官網 repo 缺失那次）我手動重啟這支時，
是透過一般 wsl bash 呼叫（`wsl -d Ubuntu -- bash script.sh`），沒有帶上 crontab 頂部那行
`PATH=/home/jack/.nvm/versions/node/v22.22.0/bin:...`——這支程式的 `env()` 函式只是原封
不動複製 `os.environ`，不會自己修正 PATH，於是繼承到 wsl 預設 login shell 的 PATH（優先
解析到 `/mnt/c/Users/haoli/node-portable/...`）。`d1()` 呼叫的 `npx wrangler` 跑到
Windows 端那份，吐出的 stderr 是非 UTF-8 編碼（疑似 Big5），Python `subprocess.run(...,
text=True)` 要把 stderr 轉成文字時解碼失敗，整輪 `main()` 都在 `d1()` 這一步就先炸掉，
從 14:33 重啟之後、14:56 起每一輪都出現同一個 `UnicodeDecodeError`，資料庫自然停在
14:33 沒有更新過。

**修法**：用正確 PATH（`/home/jack/.nvm/versions/node/v22.22.0/bin:...`）重新
`pkill -9` + 重啟（新 PID）。重啟後（19:20:46）到寫這份回報時（19:33）都沒有再出現任何
錯誤訊息——這支跟其他 daemon 一樣「沒事不出聲」，安靜就是正常。

**crontab 不需要額外修改**：這支平常都是透過 `@reboot` / `*/5 * * * *` watchdog 啟動，
一直都吃得到 crontab 頂部那行全域 `PATH=`，今天出錯純粹是我這次手動重啟繞過了 cron 的
執行環境，不是 crontab 本身設定有問題。以後只要重啟這支，都要記得先
`export PATH=/home/jack/.nvm/versions/node/v22.22.0/bin:...` 再啟動，跟這台其他所有
daemon 的既定慣例一致。

## closesync（`close_sync.py --deploy`，bim-engineer-tongluo 沒同步到網站）

**根因**：`~/claude-projects/step1ne-stopgap-site` 這個 repo 從 clone 之後**從來沒有設定
過 git 身份**（`user.name`/`user.email` 都是空的）。`close_sync.py` 的 `--deploy` 邏輯是：
`git add -A` → `git commit` → 如果 `git commit` 的 returncode 不是 0 就印「沒有要 commit
的變更」，直接當成正常情況跳過部署。但這次失敗的真正原因不是「沒有變更」，是
`git commit` 因為身份缺失直接報錯（`fatal: empty ident name ... not allowed`，
returncode 128）——**程式的錯誤處理沒有區分「真的沒變更」跟「commit 失敗」，把兩種情況
當成同一種訊息**，才會一直誤導成「沒事」。

已手動重現過一次一模一樣的 `Author identity unknown` 錯誤，確認就是這個原因，不是猜的。

**修法**：
1. `git config user.name "Jacky Chen"` / `git config user.email "aiagentg888@gmail.com"`
   （跟 `step1ne-recruit` 那份的身份一致）。
2. 這個 repo 這次失敗殘留了本機沒 commit 的修改（`jobs/bim-engineer-tongluo/index.html`、
   `sitemap.xml`），但 Mac 那邊已經用 `7aaf69c` 正確處理過同一個職缺關閉並推上去了——
   `git reset --hard` 捨棄本機這份重複/過時的修改，改用 `git pull deploy main --ff-only`
   拉 Mac 的正式版本，避免跟 Mac 的結果打架。
3. 拉完確認 HEAD 是 `7aaf69c`，`jobs/bim-engineer-tongluo/index.html` 裡確實有
   「這個職缺已經完成招募」的關閉橫幅，是對的。

**沒有完全驗證到的地方**：19:26:45 這輪 closesync 自然觸發，顯示「沒有需要同步的職缺」——
因為 bim-engineer-tongluo 已經被上面第 2 步的 `pull` 處理掉了，這輪沒有新的待同步項目，
**沒辦法用這輪真的觸發一次 `git commit` 來驗證身份設定生效**。身份設定本身已經直接用
`git config` 確認有值，邏輯上足以解決問題，但要等下一次真的有職缺被關閉、`--deploy` 真的
跑到 commit 那一步，才能 100% 確認。

**⚠️ 一個沒有動手改的既有 bug**：`close_sync.py` 把「commit 失敗」跟「沒有變更」用同一句
訊息回報，這次能抓到問題純粹是因為我自己去重現了完整錯誤——如果之後又有其他原因讓
`git commit` 失敗（例如又有身份設定被清掉、或 pre-push hook 擋下、或磁碟空間問題），
一樣會被誤判成「沒有需要同步的職缺」而悄悄跳過部署。這是程式碼本身的錯誤處理設計問題，
沒有動手修（不確定 Jacky 要不要改動這支共用腳本的邏輯），先如實回報。

## 有沒有改到程式

沒有改 `step1ne-recruit`、`step1ne-stopgap-site` 裡任何現有程式碼。只做了：
- 手動重啟 `jd_regen_tick.py`（PID 換新）。
- `step1ne-stopgap-site` 本機 `git config` 設定身份、`reset --hard` 捨棄殘留修改、
  `pull` 到最新（`7aaf69c`）。
