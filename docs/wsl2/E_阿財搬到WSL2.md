# WSL2 那台｜開第二個阿財（給 WSL2 那台的 Claude Code）

> **2026-09-30 更新（Jacky）：這次是「搬家」不是「多開一台」。** Mac 那台記憶體不夠、很慢，WSL2 這台驗收通過後，Mac 上的阿財會由 Mac 那邊的 Claude 停掉。
> 所以 `INTERVIEW_MAX_PARALLEL` 改成 **3**（一台扛全部）。驗收第 3 點（兩台不重複回話）照樣要做——搬家期間兩台會短暫並存。
> 回報寫在這台的 `~/claude-projects/工作流程技能包/step1ne-recruit/docs/wsl2/回報/`，寫完 git commit＋push，Mac 那邊 pull 就看得到。


**目標**：讓這台 WSL2（Ubuntu，使用者 jack）也跑一個阿財（interview_daemon.py），跟 Mac 上的阿財一起接面談。
兩台共用同一個資料庫排隊：誰先搶到一位人選就由誰面談，另一台會跳過（跨機器的鎖早就有，`applications.lock_expires_at`，不用改）。

⚠️ **現在 Mac 上的阿財可能正在跟真人面談**。這份只在 WSL2 這台動手，**不要碰 Mac、不要改資料庫、不要手動把任何應徵改狀態**。

## 1. 拿最新程式

```bash
cd ~/claude-projects/工作流程技能包/step1ne-recruit   # 若這台沒有，先 git clone git@github.com:step1nework016-eng/step1ne-recruit.git 到這個位置
git pull --rebase origin main
grep -n "INTERVIEW_HOST\|INTERVIEW_MAX_PARALLEL\|utcoffset" interview_daemon.py   # 三個都要找得到，找不到代表還沒拉到新版，先停下來回報
```

## 2. 確認三件前提（任何一項不過就停，回報原因）

1. `claude -p "回覆 OK" --model claude-sonnet-5` 會回 OK（CLI 已登入）。
2. `~/.config/workflow-os/cf.env` 存在（資料庫憑證，之前搬 socialpost 時已放過）。
3. 花費紀錄路徑已經是依本機資料夾自動推算（`CLAUDE_PROJECTS_DIR`），不用改；只要確認跑過一場後 `~/.claude/projects/` 底下有出現 step1ne-recruit 對應的資料夾。


4. **阿財的面談規範 SKILL.md 要在這台、而且是最新版**：程式讀的是 `~/claude-projects/工作流程技能包/recruiting-workflow/interview-conductor/SKILL.md`。
   ```bash
   cd ~/claude-projects/工作流程技能包
   [ -d recruiting-workflow ] || git clone https://github.com/step1nework016-eng/recruiting-workflow.git
   cd recruiting-workflow && git pull --rebase origin main
   grep -c "說不會就不考" interview-conductor/SKILL.md   # 要 ≥1，是 0 代表不是最新版，停下來回報
   ```
   （這個 repo 是 private，用 step1nework016-eng 帳號登入 gh 才拉得到。）

## 3. 建常駐服務（systemd user service）

`~/.config/systemd/user/step1ne-interview.service`：

```ini
[Unit]
Description=Step1ne 阿財面談引擎（WSL2 第二台）
After=network-online.target

[Service]
WorkingDirectory=%h/claude-projects/工作流程技能包/step1ne-recruit
Environment=TZ=Asia/Taipei
Environment=INTERVIEW_HOST=wsl2
Environment=INTERVIEW_MAX_PARALLEL=3
Environment=PATH=%h/.local/bin:%h/.nvm/versions/node/current/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 interview_daemon.py
Restart=always
RestartSec=30
StandardOutput=append:%h/aijob-automation/logs/interview.log
StandardError=append:%h/aijob-automation/logs/interview.log

[Install]
WantedBy=default.target
```

（PATH 裡的 node 路徑請換成這台實際 `which claude` 所在的資料夾。）

```bash
mkdir -p ~/aijob-automation/logs
systemctl --user daemon-reload
systemctl --user enable --now step1ne-interview
```

**TZ=Asia/Taipei 一定要有**：鎖的到期時間是用台灣時間字串存的，時區錯了兩台會互相搶。程式啟動時會自己檢查，不是 UTC+8 會印 ❌ 然後直接結束。

## 4. 驗收（全部要做）

1. `tail -30 ~/aijob-automation/logs/interview.log`：看到啟動訊息含 `wsl2`、上限 `3`，沒有 ❌。
2. 開機自動復活：跟 socialpost 一樣掛進「登入時喚醒」那支 VBS 的清單，然後重開 WSL（`wsl --shutdown` 再進來）確認服務自己起來。
3. 等到下一位人選進來（或 Jacky 有空時用測試職缺丟一筆），確認**同一位人選只有一台在回話**：兩台的 log 只有一台出現這位人選的回話紀錄（搶不到鎖的那台是安靜跳過，不會印東西）。
4. 看 Telegram 專案卡片「阿財」心跳，應該看得到 `wsl2` 這台。

## 5. 回報

寫到 `docs/wsl2/回報/E_<日期時間>.md`，然後 git add／commit／push：做了什麼、四項驗收各自的結果（沒測到的明講沒測到）、有沒有改到程式（有的話 commit 編號）。
