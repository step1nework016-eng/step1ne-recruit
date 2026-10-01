# 第二台 WSL2｜只開阿財（給第二台的 Claude Code）

> 2026-10-01 Jacky：多一台 WSL2 一起跑阿財，兩台合計同時 6 場（每台 3 場），跑幾天看回覆速度再往上加。
> **這台只跑阿財（interview_daemon.py）一支。** 夜間開發客戶、日報、追信、找人選等排程都已經在第一台，
> **這台一支都不要裝**，裝了就會重複發信、重複推 TG。

## 這台跟第一台怎麼分工

- 兩台共用同一個資料庫排隊：誰先搶到一位人選就由誰面談，另一台安靜跳過（`applications.lock_expires_at` 原子鎖，不用改程式）。
- 用 `INTERVIEW_HOST` 分辨是哪台：第一台是 `wsl2`，**這台用 `wsl2-b`**。
- 上限 `INTERVIEW_MAX_PARALLEL=3`。真正的瓶頸是 Claude 帳號用量（兩台同一個帳號），不是電腦，不要自己調高。

⚠️ 第一台的阿財可能正在跟真人面談。**只在這台動手，不要碰第一台、不要改資料庫、不要手動改任何應徵狀態。**

---

## 0. Jacky 要先在這台 Windows 準備好的（Claude 做不了，看到沒準備好就停下來回報缺哪一項）

1. **WSL2 Ubuntu**，`/etc/wsl.conf` 有 `[boot] systemd=true`。
2. **Windows 自動登入**：用 Sysinternals Autologon 設定（跟第一台一樣）。⚠️ 要輸入的是 **Windows 帳號密碼，不是 PIN**；用 PIN 會「看起來設定成功但沒生效」。
   沒設的話 Windows 半夜自動更新重開機後，阿財要等有人登入才會復活。
3. **Windows 裝了 Google Chrome**（阿財產面談報告 PDF 會借用 Windows 的 Chrome）。
4. **WSL 裡裝好 Claude Code 並登入 Jacky 的 Claude 帳號**（`claude` 指令能用）。
5. **4 個設定檔**：從第一台的 `~/.config/workflow-os/` 複製這 4 個到這台同一個位置，然後 `chmod 600`：
   `cf.env`、`tokens.env`、`step1ne-tg.env`、`taskboard.env`
   ⚠️ 用隨身碟或兩台之間直接複製，**不要貼在任何聊天視窗、TG 或 Email 裡**——這些是資料庫與機器人的鑰匙。
6. **GitHub 讀取權限**：這台要能 clone `step1nework016-eng/step1ne-recruit`（private）與 `step1nework016-eng/recruiting-workflow`（private）。用 `gh auth login` 登入 step1nework016-eng，或請 Jacky 給這台加一把 deploy key。

## 1. 拿程式

```bash
mkdir -p ~/claude-projects/工作流程技能包 && cd ~/claude-projects/工作流程技能包
[ -d step1ne-recruit ] || git clone git@github.com:step1nework016-eng/step1ne-recruit.git
[ -d recruiting-workflow ] || git clone git@github.com:step1nework016-eng/recruiting-workflow.git
cd step1ne-recruit && git pull --rebase origin main && git log --oneline -1
grep -n "INTERVIEW_HOST\|INTERVIEW_MAX_PARALLEL" interview_daemon.py   # 兩個都要找得到
grep -c "說不會就不考" ../recruiting-workflow/interview-conductor/SKILL.md   # 要 ≥1
```

## 2. 前提檢查（任何一項不過就停，回報原因）

```bash
which claude                        # ⚠️ 要是 Linux 路徑（例如 ~/.nvm/... 或 ~/.local/bin），不能是 /mnt/c/...
claude -p "回覆 OK" --model claude-sonnet-5
ls -l ~/.config/workflow-os/{cf.env,tokens.env,step1ne-tg.env,taskboard.env}
cd ~/claude-projects/工作流程技能包/step1ne-recruit
python3 -c "import deliver; print(deliver.CHROME)"   # 要印出 /mnt/c/Program Files/Google/Chrome/... 的路徑
python3 -c "
import deliver
print('PDF:', deliver.html_to_pdf('<h1>測試</h1>' + '<p>內容</p>'*200, '/tmp/acai_pdf_test.pdf'))"   # 要印 PDF: True
```

## 3. 建常駐服務

`~/.config/systemd/user/step1ne-interview.service`：

```ini
[Unit]
Description=Step1ne 阿財面談引擎（第二台 WSL2）
After=network-online.target

[Service]
WorkingDirectory=%h/claude-projects/工作流程技能包/step1ne-recruit
Environment=TZ=Asia/Taipei
Environment=INTERVIEW_HOST=wsl2-b
Environment=INTERVIEW_MAX_PARALLEL=3
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PATH=<這台 which claude 所在的資料夾>:%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 interview_daemon.py
Restart=always
RestartSec=30
StandardOutput=append:%h/aijob-automation/logs/interview.log
StandardError=append:%h/aijob-automation/logs/interview.log

[Install]
WantedBy=default.target
```

- **PATH 不要放任何 `/mnt/c/...`**：第一台踩過，會叫到 Windows 版的工具、吐亂碼整個卡住。
- **TZ=Asia/Taipei 一定要有**：鎖的到期時間是台灣時間字串，時區錯了兩台會互搶。程式啟動時會檢查，不對會印 ❌ 結束。

```bash
mkdir -p ~/aijob-automation/logs
loginctl enable-linger "$USER"          # 不用登入 WSL 也會自己起來
systemctl --user daemon-reload
systemctl --user enable --now step1ne-interview
```

## 4. 驗收（全部要做，沒測到的就明講沒測到）

1. `tail -30 ~/aijob-automation/logs/interview.log`：有 `面談引擎啟動｜wsl2-b（…同時最多 3 場）`，沒有 ❌。
2. **開機自動復活**：在 Windows PowerShell 跑 `wsl --shutdown`，再開 WSL，`systemctl --user is-active step1ne-interview` 要是 `active`，log 多一行啟動訊息。
3. **兩台不重複回話**：等下一位人選進來（或請 Jacky 用測試職缺丟一筆），確認同一位人選只有一台的 log 出現回話紀錄。
4. **自動更新沒被擋**：`git status --short` 不能有任何「被追蹤檔的修改」（`??` 未追蹤的可以）。阿財會自己定期 git pull，工作目錄髒了就會更新失敗、停在舊版。
5. Telegram 專案卡片「阿財」心跳看得到 `wsl2-b`（看不到 TG 就請 Jacky 確認）。

## 5. 不要做的事

- 不要裝任何其他排程（crontab、systemd timer 都不要）。這台只有阿財。
- 不要改 `step1ne-recruit` 的程式。真的要改，寫在回報裡，由 Mac 那邊改、你再 pull。
- 不要動資料庫、不要手動改應徵狀態。

## 6. 回報

寫到 `docs/wsl2/回報/E5_第二台_<日期>.md`，git add／commit／push：
做了什麼、0 的六項前提哪些是 Jacky 已經準備好的、第 4 節五項驗收各自結果（沒測到的明講）、有沒有改到程式。
