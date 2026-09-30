# WSL2 那台｜夜間自動開發客戶＋找人選（給 WSL2 那台的 Claude Code）

## 白話先講：這是在做什麼

**比喻**：請一位「夜班研究員」住在 WSL2 那台電腦裡。
- **晚上 10 點**：他去查「最近有擴廠、得標、募資、換主管、或正在大量徵才」的台灣公司，每晚最多 30 家，
  把電話、104 企業頁、官網、可以聊的話題、LinkedIn 上的人資是誰，整理成**電話名單**，
  三樣都查到的就直接分給 Jacky 或 Phoebe。
- **半夜 1 點**：他替每個正在開、最近有人應徵的職缺找人選，每個職缺最多 10 位，
  只收台灣在地、有公開聯絡方式的，放進人選池；顧問之前標過「不合適」的那一類會避開。
- **早上 7:00**：因為 104 會擋「背景」的機器人，這一段改由 **Windows 那台的 Claude 桌面版**，
  用**真的 Chrome**（跟人一樣開分頁）去 104 看：名單上的公司現在開了哪些職缺、招募聯絡人和電話是什麼，
  寫回名單，發一則 TG 摘要。夜裡的背景工作**不進 104**。
- **早上 8:30**：在 TG「📬 開發信 開信・回信」主題推一則早報：昨晚新增幾家、分給誰、幾家不合格缺什麼、
  各職缺找到幾位、AI 找人的準度。

他**只查資料、只寫進名單，不會聯絡任何人**（不寄信、不發訊息）。瀏覽器只有查的時候才開，查完就關。

⚠️ 這份只在 WSL2 這台動手。**不要在 Mac 上跑夜間工作**（Mac 記憶體已經很吃緊）。
⚠️ 資料庫是正式的：驗收時上限一定要先改成 3 家，不要一上來就跑 30 家。

---

## 1. 拿最新程式

```bash
cd ~/claude-projects/工作流程技能包/step1ne-recruit
git pull --rebase origin main
ls nightly_pipeline/   # 要看到 run_nightly.sh、d1q.py、morning_summary.py、cards_need_104.py、save_104.py、mcp_playwright.json、prompts/、systemd/
chmod +x nightly_pipeline/run_nightly.sh
```
找不到 `nightly_pipeline` 資料夾 → 代表還沒拉到新版，先停下來回報。

## 2. 前提檢查（任何一項不過就停，回報原因）

1. **claude 已登入、而且模型名稱對**：
   ```bash
   claude -p "回覆 OK" --model claude-sonnet-5 --setting-sources ''
   ```
   要回 OK。記下 `dirname $(which claude)`，第 3 步要用。
2. **資料庫憑證**：`ls ~/.config/workflow-os/cf.env`（之前搬 socialpost 時已放過），再跑
   ```bash
   python3 ~/claude-projects/工作流程技能包/step1ne-recruit/nightly_pipeline/d1q.py q "SELECT COUNT(*) n FROM jobs"
   ```
   要印出一個數字。
3. **TG 設定**：`ls ~/.config/workflow-os/step1ne-tg.env`，裡面要有 `TG_BOT_TOKEN`、`TG_CHAT_ID`（只看有沒有，不要把值貼出來）。
   沒有就從 Mac 的同一個檔案複製過來（跟 cf.env 一樣的做法）。
4. **無頭瀏覽器可以用**（夜班研究員要用它查 JS 網頁）：
   ```bash
   npx -y @playwright/mcp@latest --help | head -3        # 會先下載，看到說明就代表裝得起來
   npx -y playwright install --with-deps chromium        # 第一次要裝 chromium 本體（會要 sudo 裝系統套件）
   ```
   裝完確認：`ls ~/.cache/ms-playwright/` 要看到 `chromium-…` 資料夾。
5. **本機檢查一次（不會真的跑）**：
   ```bash
   cd ~/claude-projects/工作流程技能包/step1ne-recruit/nightly_pipeline
   bash -n run_nightly.sh && ./run_nightly.sh bd --limit 3 --dry-run | head -5
   python3 morning_summary.py --dry-run
   ```

## 3. 裝排程（systemd user timer）

先把三個 `.service` 裡的 `Environment=PATH=` 的 node 路徑換成第 2 步記下的 claude 所在資料夾，再裝：

```bash
cd ~/claude-projects/工作流程技能包/step1ne-recruit/nightly_pipeline
mkdir -p ~/.config/systemd/user ~/aijob-automation/logs
cp systemd/step1ne-*.service systemd/step1ne-*.timer ~/.config/systemd/user/
# 換 PATH（把下面的路徑換成實際的）
sed -i "s|%h/.nvm/versions/node/current/bin|$(dirname "$(which claude)")|" ~/.config/systemd/user/step1ne-nightly-*.service ~/.config/systemd/user/step1ne-morning-summary.service
systemctl --user daemon-reload
systemd-analyze --user verify ~/.config/systemd/user/step1ne-nightly-bd.service   # 沒有紅字就好
# ⚠️ 先不要 enable，等第 4 步驗收過了再開
```

三個排程：
| 排程 | 時間（台灣） | 做什麼 | 最長可以跑多久 |
|---|---|---|---|
| step1ne-nightly-bd.timer | 每天 22:00 | 開發客戶，最多 30 家 | 2 小時 50 分（01:00 前一定結束，不跟找人選搶） |
| step1ne-nightly-sourcing.timer | 每天 01:00 | 找人選，每職缺最多 10 位 | 5 小時 |
| step1ne-morning-summary.timer | 每天 08:30 | 推 TG 早報 | 5 分鐘 |

驗收過了再開：
```bash
systemctl --user enable --now step1ne-nightly-bd.timer step1ne-nightly-sourcing.timer step1ne-morning-summary.timer
systemctl --user list-timers | grep step1ne     # 三個都要看到下次執行時間
```
開機自動復活：跟 socialpost、阿財一樣，確認這台有 `loginctl enable-linger jack`（或掛在「登入時喚醒」那支 VBS 的清單裡）。

## 4. 驗收（全部要做，沒做到的回報時明講）

**A. 手動跑一次開發客戶，上限 3 家**（大約 15～40 分鐘）
```bash
cd ~/claude-projects/工作流程技能包/step1ne-recruit/nightly_pipeline
systemd-run --user --unit=step1ne-bd-test --wait -p TimeoutStartSec=1h -E TZ=Asia/Taipei \
  -E PATH="$(dirname "$(which claude)"):/usr/local/bin:/usr/bin:/bin" \
  ./run_nightly.sh bd --limit 3
# 另開一個視窗看進度：tail -f ~/aijob-automation/logs/nightly_bd.log
```
（用 `systemd-run` 跑，是為了跟正式排程一模一樣的環境。）

跑完檢查：
1. **有寫進去**：
   ```bash
   python3 d1q.py q "SELECT company, assigned_to, call_phone, company_104_url, guard_json FROM bd_outreach WHERE batch_id LIKE 'nightly-bd-%' ORDER BY created_at DESC LIMIT 5"
   python3 d1q.py q "SELECT company, updated_by FROM bd_company_profiles WHERE updated_by='AI夜間客戶研究'"
   python3 d1q.py q "SELECT company, name, title FROM bd_hr_contacts WHERE source LIKE '%AI夜間%'"
   ```
   最多 3 家；合格的有 assigned_to；每家的 `why_company` 以【觸發】開頭、有日期和來源網址；正式名稱沒有括號英文、沒有「待確認」。
   **隨機挑 1 家，自己打開它的官網對一下電話號碼是不是真的**。
2. **瀏覽器有關掉**：
   ```bash
   tail -5 ~/aijob-automation/logs/nightly_bd.log        # 要看到「✅ 沒有留下瀏覽器」
   pgrep -a -i 'chrom|headless_shell' || echo 沒有瀏覽器   # 要印「沒有瀏覽器」
   tail -1 ~/aijob-automation/logs/nightly_runs.jsonl     # exit 要是 0、leftover_browsers 要是 0
   ```
   如果 log 寫「⚠️ 發現這一輪留下 N 個瀏覽器」→ 程式已經自動收掉了，但代表 AI 沒照規定自己關，回報裡要寫。
3. **沒有聯絡任何人**：`grep -i -E "sendMessage|api.telegram|resend|smtp" ~/aijob-automation/logs/nightly_bd.log` 要沒有結果。
4. **鎖有效**：跑的過程中另開視窗再下一次 `./run_nightly.sh bd --limit 3`，log 要出現「⏭ 上一輪 bd 還在跑，這次跳過」。

**B. 早報**
```bash
python3 morning_summary.py --dry-run    # 看內容，應該看得到剛剛那 ≤3 家
python3 morning_summary.py              # 真的發一次，到 TG「📬 開發信 開信・回信」主題確認有收到
```

**C. 找人選**：不用手動跑，開排程後隔天早上看 08:30 早報的「找人選」那段和最後的 ✅／❌。
（想先試：`./run_nightly.sh sourcing --per-job 2`，一樣用上面 systemd-run 的包法。）

## 5. Windows 那台：Claude 桌面版的 07:00 排程任務（Jacky 照著做就好）

**這一段在做什麼**：像是每天早上請一位助理，打開 Chrome、一家一家點進 104 看「這家現在在徵什麼人、招募窗口電話幾號」，
抄回名單上。因為是真的 Chrome，104 不會把它當機器人擋掉。助理只看不按：不登入、不應徵、不按「聯絡」。

**前提（已經有的打勾就好）**
- [ ] 那台 Windows 電腦設定了自動登入（已經有了）
- [ ] Chrome 開著，裝了「Claude in Chrome」擴充功能，而且登入的是 Jacky 的 Claude 帳號
- [ ] Claude 桌面版開著、登入 Jacky 的帳號
- [ ] 上面第 1～2 步在 WSL2 做完了（程式拉好、cf.env 和 step1ne-tg.env 都在）
- [ ] 在 Windows 的 PowerShell 貼這行，會印出一段有公司名的文字（代表 Windows 叫得動 WSL 裡的小程式）：
  `wsl.exe -- bash -lc "cd ~/claude-projects/工作流程技能包/step1ne-recruit && python3 nightly_pipeline/cards_need_104.py --limit 1"`

**建立排程（一次就好）**
1. 打開 Claude 桌面版 → 左邊找「**排程任務**」→ 按「**新增**」。
2. 名稱填：`Step1ne 104 晨間補資料`
3. 時間：**每天 07:00**。
4. 內容：打開 `step1ne-recruit/nightly_pipeline/prompts/chrome_104_morning.md`，**全文複製貼上**。
   （也可以在 WSL 裡 `cat ~/claude-projects/工作流程技能包/step1ne-recruit/nightly_pipeline/prompts/chrome_104_morning.md` 再複製。）
5. 如果畫面問要不要允許使用 Chrome、執行指令、寫檔案 → 都選允許（它需要開分頁、呼叫 WSL 的小程式、存一個暫存檔）。
6. 存檔。

**驗收（第一次一定要做）**
1. 在剛剛貼上的內容最上面，把「**本次上限：20 家**」改成「**本次上限：3 家**」，下面第一步指令裡的 `--limit 20` 也改成 `--limit 3`。
2. 按「**立即執行**」。
3. 看著 Chrome：它應該**自己開一個新分頁** → 進 104 公司頁 → 點「工作機會」→ **關掉分頁** → 下一家。
   Jacky 原本開的分頁不能被動到。
4. 跑完後檢查：
   - TG「📬 開發信 開信・回信」主題收到一則「🔎 104 晨間補資料」：查了幾家、補到幾個招募窗口、哪幾家卡住。
   - Chrome 裡**沒有留下它開的分頁**。
   - 在 WSL 查寫回的結果（任選一家核對 104 上看到的是不是一樣）：
     ```bash
     cd ~/claude-projects/工作流程技能包/step1ne-recruit/nightly_pipeline
     python3 d1q.py q "SELECT company, hiring FROM bd_company_profiles WHERE hiring LIKE '104 現開%' ORDER BY updated_at DESC LIMIT 3"
     python3 d1q.py q "SELECT company, hr_contact, hr_phone, phone_source FROM bd_outreach WHERE phone_source LIKE '%104 公司頁%' ORDER BY updated_at DESC LIMIT 3"
     ```
5. 驗收過了，把上限改回 **20**（兩個地方）存檔。

**如果出現人機驗證**：它會停下來、不硬闖，TG 摘要會寫「卡住：某某公司：人機驗證」。
偶爾一次沒關係；連續兩天都卡，就回報，可能要把上限調低或改時間。

## 6. 調整上限

- 每晚家數：改 `~/.config/systemd/user/step1ne-nightly-bd.service` 的 `--limit 30`
- 每職缺人數：改 `step1ne-nightly-sourcing.service` 的 `--per-job 10`
- 改完 `systemctl --user daemon-reload`。
- 規則（排除誰、分給誰、怎麼判等級）寫在 `nightly_pipeline/prompts/` 兩份提示詞裡，要改規則改那裡，改完 commit。

## 7. 回報

寫到 `回報/E2_<日期時間>.md`：
- 做了什麼（前提檢查 5 項各自過了沒）
- 驗收 A（1～4 項）、B、C 各自的結果；**沒測到的明講沒測到**
- 驗收 A 寫進去的那 ≤3 家：公司名、分給誰、你親自核對的那 1 家電話對不對
- 瀏覽器有沒有留下（貼 `nightly_runs.jsonl` 最後一行）
- 三個 timer 有沒有 enable、`list-timers` 看到的下次執行時間
- Windows 07:00 排程：有沒有建好、「立即執行」3 家的結果（TG 有沒有收到、分頁有沒有關乾淨、哪幾家卡住）；沒測到就明講
- 有沒有改到程式（有的話 commit 編號）
