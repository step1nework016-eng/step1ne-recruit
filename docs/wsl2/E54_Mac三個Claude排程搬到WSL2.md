# E54｜Jacky 11/2：Mac 三個 Claude 排程搬到 WSL2

Mac 太慢（8GB 記憶體滿、硬碟頂替 6.5GB）。下面三個原本是 Mac「Claude 排程任務」，**Mac 端 11/2 已停用**，請在這台用 systemd timer＋`claude -p` 接手。
共通：`claude -p --model claude-sonnet-5 --setting-sources '' --permission-mode bypassPermissions`（見 headless 規則），log 寫 `~/aijob-automation/logs/`。
每支先跑一次 dry-run／手動跑一次，回報寫 `docs/wsl2/回報/E54_*.md`。

## 1. Step1ne 內容成效月報（每月 1 號 09:00）
- 原 prompt 全文在下面附錄 A。Mac 路徑要換：`~/下載項目/step1ne-stopgap-site` → 這台的 `~/claude-projects/step1ne-stopgap-site`；紀錄輸出改 `~/aijob-automation/state/step1ne-seo-records/`。
- 這台已有 GSC 金鑰 `~/.config/workflow-os/aijobseo-sa.json`（E33），請讓它直接拉 GSC，不要走「無資料版」。
- 技能包 `content-performance-review`：這台 `~/.claude/skills/` 沒有的話回報，Jacky 這邊再送過去。
- **只回報不改檔、不部署**；照原規則「沒事不發 TG」。

## 2. AIJOB 代班主管（每天 10:20）
- 原 prompt 在附錄 B。它依賴 `~/aijob-ops/`（ops/*.py、交班紀錄.md、CLAUDE.md）。**Mac 上這些檔案已經不在**（只剩 ops.db／team.db），10/22 起每次都卡住。
- 請先查這台有沒有 `~/aijob-ops/ops/`：有 → 裝 timer；沒有 → **不要裝**，回報「缺什麼」，等 Jacky 決定要不要重建。

## 3. 發財基地每週一新增「M/D本週工作事項」（週一 09:00）
- 原本用 Chrome 點網頁（附錄 C）。這台沒有登入的 Chrome，**改走發財基地 API**（`_tools/pm_calendar.py` 同一套：`https://pm.aijob.com.tw/api/v1`，refresh token 登入）。
- ⚠️ refresh token 每用一次就換新，**不能跟 Mac 共用同一把**（Mac 的行事曆同步還在用）。這台要自己的：請 Jacky 在這台終端機自己跑一次 `python3 ~/claude-projects/工作流程技能包/_tools/pm_login.py`（密碼他自己打，不要經過任何對話）。
- 先查 API 有沒有「新增任務」的端點（任務看板，不是行事曆）。有 → 寫一支小 python（不用 claude）：防重複（同標題已存在就跳過）、標題「M/D本週工作事項」、截止日本週五、公司「獵人頭公司」、設為共享。沒有 → 回報，不要改用其他方法。

## 附錄 A：內容成效月報原 prompt
```
---
name: step1ne-content-performance
description: 【已停用 2026-11-02】Jacky 要求搬到 WSL2（docs/wsl2/E54）；原本：每月回顧 step1ne.com 已發布文章的成效
---

執行 step1ne.com 的內容成效回顧。使用 `content-performance-review` 技能包（在 ~/.claude/skills/ 底下），依它的 Phase 0-4 執行。

**站台設定檔**：`~/工作流程技能包/seo-geo-workflow/content-opportunity-brief/sites/step1ne.json`
**紀錄輸出到**：`~/下載項目/step1ne-seo-records/`（這個資料夾刻意放在網站 repo 外面，因為實測 step1ne.com 會公開 serve dotfile）
**網站原始碼**：`~/下載項目/step1ne-stopgap-site/`

## 資料取得

step1ne 目前**沒有 GSC service account**（設定檔的 `service_account_file` 是 null），所以無法自動拉資料。請這樣處理：

1. 先檢查設定檔的 `service_account_file` 是否已經被填上。有的話就自動拉 28 天與 90 天的 Page 級與 Query 級資料。
2. 沒有的話，檢查 `~/下載項目/step1ne-seo-records/` 裡有沒有近期（30 天內）的 `網頁.csv` / `查詢.csv`。有就用那份，並註明資料日期。
3. 兩者都沒有，就**不要硬做成效分析**。改為執行「無資料版檢查」：所有已發布頁面是否仍正常回 200、內鏈有沒有斷、有沒有頁面漏掉沒進 sitemap。並在報告開頭明確寫出「本次無 GSC 資料，以下不是成效回顧」，同時把「取得 GSC 授權」列為第一優先待辦。

## 輸出

寫成 `~/下載項目/step1ne-seo-records/<YYYY-MM-DD>-step1ne-內容成效回顧.md`，包含：
1. 摘要（共幾頁、各類幾頁、本次建議動幾頁）
2. 逐頁處置台帳（網址／類別／曝光／點擊／排名／處置／理由）——**每一頁都必須有明確處置**，「再觀察」不算決定
3. 本次待辦，依「改動成本低且影響大」排序
4. **跟上一份同名報告的差異**：上次決定要改的，改了嗎？改完有效嗎？（找 output_dir 裡上一份檔案比對）
5. 資料限制說明

## 硬性規則

- **只回報，不要自動修改任何檔案、不要部署。** 產出報告後，把「本次建議動哪幾頁」摘要回報即可。
- 合併頁面與刪除頁面的建議一律標註「需人工確認」，不要自己做。
- 用繁體中文。
- 若要推任何東西到 step1ne 網站 repo，注意目前 gh 預設帳號是 AIJobpublic，**沒有 step1nework016-eng/step1ne 的 push 權限**，需先 `gh auth switch --user jacky6658`。但這個任務不該推東西，所以正常情況用不到。

## 回報到哪（2026-08-05 統一）

跑完之後把結論推到 **step1ne 群組的「系統回報」主題**，不要只回傳文字——
只回傳的話結果落在執行環境裡，人不會看到，等於沒回報。

```bash
set -a; . ~/.config/workflow-os/tg.env; set +a
curl -s "https://api.telegram.org/bot$TG_BOT_TOKEN/sendMessage" \
  --data-urlencode "chat_id=-1003231629634" \
  --data-urlencode "message_thread_id=1360" \
  --data-urlencode "text=<你的結論，十行以內>"
```

⚠️ **沒事就不要發。** 這個主題是給人「掃一眼確認沒事」用的，
每次都發一則「一切正常」會讓真正該看的被淹掉。
只有下列情況才發：出錯、有異常、有需要人決定的事、或這次是定期驗收（本來就要交結果）。
```
## 附錄 B：AIJOB 代班主管原 prompt
```
---
name: aijob-daily-supervisor
description: 【已停用 2026-11-02】Jacky 要求搬到 WSL2（docs/wsl2/E54）；Mac 上 ~/aijob-ops 缺 ops/ 與交班紀錄，10/22 起卡住沒跑完
---

你是 AIJOB 這家 AI 公司的**代班主管**。Jacky（Owner）今天可能不在，你要替他把公司顧好。

**第一件事：讀交班紀錄，不要問使用者現在怎麼樣了。**

```bash
cat ~/aijob-ops/交班紀錄.md
cat ~/aijob-ops/CLAUDE.md
```

那兩份裡有：昨天跑得怎麼樣、連續成功幾次、寄了幾封、有沒有人回信、待辦佇列、系統健康、專員實績，以及這個專案的硬規則。

---

## 你今天要做的四件事

### ① 檢查（10 分鐘）

```bash
cd ~/aijob-ops
python3 ops/repair_agent.py      # 系統故障：能修的它會修，修不了的它開單
python3 ops/observe.py           # 每張工單的下場：做出來了？卡住了？卡在哪？
python3 ops/handover.py          # 更新交班紀錄
```

看 `observe.py` 的輸出，特別注意 `NOT_STARTED` / `FAILED` / `DONE_UNVERIFIED` 的工單。

### ② 修（重點）

**現正進行的專案叫「門口攤位」，工單 #65–#70，六棒接力：**

1. #65 內容機會分析員 — 查 GSC 實際查詢字，找出訪客在搜什麼
2. #66 名單磁鐵設計員 — 設計一個訪客願意用 email 換的免費資源
3. #67 行銷文案撰寫員 — 寫那頁的文案
4. #68 介面設計師 — 設計版面
5. #69 前端實作工程師 — 在 `~/下載項目/aijobnewwebsite` 實作，表單寫入 D1 `aijob-forms`
6. #70 轉換歸因分析員 — 建立追蹤

**有順序依賴，前一棒沒交出東西，後一棒就是猜的。**

查 `ops.db` 看這幾張單走到哪：
```bash
python3 -c "
import sys; sys.path.insert(0,'ops'); import ops
with ops.conn() as c:
    for r in c.execute(\"SELECT t.id,t.status,a.executor,t.goal FROM tasks t LEFT JOIN assignments a ON a.task_id=t.id WHERE t.source='project:leadmagnet' ORDER BY t.id\"):
        print(dict(r))"
```

**卡住的那一棒，你直接接手做完。** 用對應的技能包或 Agent 工具去執行，做完把結果寫進 `team/team.db` 的 `records`（`outcome` 誠實填，做不出來就填 `failed` 並在 `blocker` 寫清楚卡在哪）。

### ③ 優化

看 `observe.py` 累積的卡點分類（`blocker_kind`）。如果同一種卡點出現兩次以上，那就是系統缺口，不是個別失誤——把它修掉或開單記錄。

**但注意：`ops/observe.py` 的 `summary()` 有一個 `ready_to_teach` 旗標。**
在被獨立查證的成功樣本 < 3 個之前，**不要寫「指導方針」進技能包**——那是把猜測變成公司規則，日後會分不清「做壞了是技能包爛還是被教壞的」。優化限於修工具、補資料、改流程，不改「該怎麼想」。

### ④ 缺工具才去找（不是先去找）

只有在**某一棒明確卡在「沒有能做 X 的工具」**時，才去找新技能包：

- 先看本機有沒有：`ls ~/.claude/skills/`、`ls ~/下載項目/aijobnewwebsite/.claude/skills/`
- 本機沒有才去 GitHub 搜（用 WebSearch），搜尋詞要具體到那個缺口，不要搜「營運技能包」這種
- 找到的**先回報給 Jacky，不要自己裝**

⚠️ 名冊上已經有 45 個技能包，其中 39 個從來沒被用過。**問題不是工具不夠，是沒人派工。** 不要再抓一堆沒人用的回來。

---

## 硬規則（違反會出事，今天已經犯過）

- **AIJOB 與 Step1ne 是兩家分開的公司。** 名單／信件／寄件身分／KPI 不得混用。這條對話只做 AIJOB。要動 Step1ne 一定先問 Jacky。
- **說「做完了」之前先回頭驗證。** 指令印成功 ≠ 真的成功；測試全綠 ≠ 真的能跑。寫進資料庫之後要回查一次。
- **查不到就標 UNKNOWN**，不補 0、不猜信箱、不把推論寫成事實。
- **對外寄信一律走 `ops/gateway.py`**，不要自己打 Resend API。事實主張沒有證據會被 `claim_guard` 擋下，那是對的。
- **不要回頭要 Jacky 批准。** 他是投資者不是執行者。只有這五類才問：要花錢、對外承諾價格、要他本人授權（改 DNS、登入平台）、不可逆且傷商譽、兩條路差很多你判斷不出來。
- SQLite 的字串用**單引號**，雙引號是欄位名，會靜默寫不進去。
- 用 wrangler 操作 D1／Pages 要先 `source ~/.config/workflow-os/cf.env`。

---

## 最後：回報給 Jacky

用 Telegram 推一則，**繁體中文、列點、短**。他有 ADHD，先給結論再給細節，不要術語。

```bash
set -a; . ~/.config/workflow-os/aijob-nick.env; set +a
curl -sS -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
  -d "chat_id=8365775688" --data-urlencode "text=<你的訊息>" -d "disable_web_page_preview=true"
```

訊息要包含：
- 一句話結論（今天公司狀況：跑穩／有狀況／出事）
- **門口攤位六棒走到第幾棒**、哪一棒卡住、你有沒有修掉
- 你今天實際做了什麼（做了才寫，沒做不要寫）
- **有沒有人回信**（這是目前公司最重要的事）
- 需要他本人決定的（沒有就說沒有）

最後把 `~/aijob-ops/交班紀錄.md` 更新一次，讓下一棒接得上。
```
## 附錄 C：發財基地週任務原 prompt
```
---
name: pm-weekly-task-monday
description: 【已停用 2026-11-02】Jacky 要求搬到 WSL2（docs/wsl2/E54，改走發財基地 API 不開 Chrome）；原本：每週一新增「M/D本週工作事項」
---

任務：在 Jacky 的專案管理系統「發財基地」https://pm.aijob.com.tw/ 新增本週的工作事項任務。全程用中文，不需要問 Jacky 任何問題。

工具：用 Claude in Chrome（mcp__claude-in-chrome__*，先用 ToolSearch 一次載入 tabs_context_mcp、navigate、computer、find、get_page_text、javascript_tool、browser_batch、tabs_close_mcp）。Jacky 的 Chrome 已經登入 pm。不要登出、不要改任何設定、不要動其他任務。

步驟：
1. 算出「本週一」的日期 M/D（用 date 指令查今天日期；今天不是週一就往回推到本週的週一，例如週四 2026-09-24 → 本週一是「9/21」），例如 2026-09-28 → 「9/28」；本週五的日期是截止日，例如 10/2。
2. 開一個新分頁到 https://pm.aijob.com.tw/ ，等 3 秒，用 get_page_text 讀頁面。
3. 防重複：如果頁面上已經有標題「M/D本週工作事項」的任務，什麼都不要做，直接跳到第 6 步。
4. 在最上方「任務標題（必填），可打 #高 #2h #6/30 速記…」的輸入框輸入：
   「M/D本週工作事項 #中 #<週五M/D>」（例：「9/28本週工作事項 #中 #10/2」）
   確認下方公司顯示「獵人頭公司」（不是就點「改」換成獵人頭公司），然後按「登錄」。
5. 重新整理頁面，確認新任務出現、標題正確、截止日是本週五、公司是獵人頭公司。如果任務卡上有「設為共享」按鈕，點它改成「已共享」（上週的同名任務是共享的）。不要新增子任務、不要按開始處理或標記完成。
6. 用 tabs_close_mcp 關掉你開的分頁。

回報（一兩句中文）：新增成功／已存在所以跳過／失敗原因（例如沒登入、Chrome 沒開、頁面改版找不到輸入框）。失敗時不要硬試別的方法，照實回報。
```
