# E47｜WSL2：接手 KOC/KOL（UXKB）五支程式（2026-10-22 晚）

## 結論先講
Jacky：**夜間找創作者不能在 Mac 跑**（Mac 會帶著走、8GB 撐不住，今晚 23:10 跑起來負載衝到 12）。
Mac 已做：**夜間找創作者（uxkbnightsearch, 每天 23:10）＋夜間摘要（uxkbnightdigest, 每天 09:55）已停用**（plist 改名 `.disabled`，跑到一半的那次已中止）。
另外 3 支常駐程式 Mac **還開著**，等你接好、回報能跑，Mac 再停，避免中斷服務。

## 程式在哪
- 程式碼：`git@github.com:lizkockol1688-sketch/koc-kol-backend.git`（main）。這是 `koc-kol-matching/backend`。如果這台沒有這個 repo 的讀取權限，回報裡寫「需要 deploy key」，不要用別的帳號硬拉。
- 放到 `~/claude-projects/koc-kol-matching/backend`（跟 Mac 同路徑，腳本比較不用改）。
- **金鑰檔不在 repo 裡**（`cf.env`、`line.env`、`telegram.env`，都被 .gitignore 擋掉）。**不要叫 Jacky 貼進對話或 TG**；回報裡寫一行請他用隨身碟或 scp 自己放到 `backend/` 底下、權限 600。

## 要接的 5 支（照 Mac 的 launchd 設定）
| 名稱 | 腳本（backend/scripts/） | 怎麼跑 |
|---|---|---|
| 夜間找創作者 | `run_scheduled_night_discovery.sh` | 每天 23:10（timer） |
| 夜間摘要 | `run_scheduled_night_digest.sh` | 每天 09:55（timer） |
| AI 文字轉接 | `run_ai_text_proxy.sh` | 常駐（Restart=always） |
| 需求單解析 | `run_brand_brief_parser_daemon.sh` | 常駐 |
| 報告文字產生 | `run_report_narrative_daemon.sh` | 常駐 |

- 腳本裡寫死 Mac 路徑（`/Users/user/.local/bin`、`.nvm/.../node/v22.22.0/bin`）→ 改成這台的路徑，用 systemd user service／timer。
- 夜間找創作者會呼叫 `claude --print`（bypassPermissions）並用瀏覽器分頁搜尋；記得加 `--setting-sources`（見 memory headless_claude_setting_sources，避免全域指令蓋掉任務）。瀏覽器這台沒有的話，回報缺什麼。
- `npm install` 依 backend/package.json。

## 回報
寫到 `docs/wsl2/回報/E47_日期時間.md` 並 push：
1. 5 支的 systemd 狀態
2. 3 支常駐程式有沒有正常起來（看 log 最後 10 行）
3. 夜間找創作者手動跑一次的結果（或缺金鑰／缺瀏覽器卡在哪）
4. 一句話：「Mac 可以停 aitextproxy／briefparser／reportnarrative」或卡在哪
