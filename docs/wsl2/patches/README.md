# deliver_wsl2.patch（基準：origin/main `ac87c52`；套用：`git apply docs/wsl2/patches/deliver_wsl2.patch`）

1. **改了什麼**：`_find_chrome()` 多找 `/mnt/c/Program Files*/Google/Chrome/.../chrome.exe` 兩個路徑；`html_to_pdf()` 在 Chrome 路徑以 `.exe` 結尾時（WSL2 借用 Windows 的 Chrome），改把暫存 HTML 與輸出 PDF 放在 `/mnt/c/Windows/Temp/step1ne-chrome-pdf`，用 `wslpath -w` 把路徑轉成 `C:\...` 給 Chrome，印完再複製回呼叫端要的 `out_path`（另加 `import shutil`）。
2. **為什麼只有 WSL2 需要**：WSL2 沒裝 Linux 版 Chrome，只能叫 Windows 的 `chrome.exe`；它是 Windows 行程，看不到 WSL2 的 `/tmp` 與專案路徑，直接 `--print-to-pdf=/home/jack/...` 會印不出來，所以要走 Windows 看得到的資料夾再複製回來。
3. **Mac 上套用會不會有影響**：不會。Mac 的 Chrome 是 `/Applications/Google Chrome.app/...`，不以 `.exe` 結尾，`is_win_chrome=False`，新分支全部不執行；非 `.exe` 時 `win_out_path` 就等於 `out_path`，Chrome 指令列、暫存檔位置、輪詢的檔案都跟原版一樣（已用模擬 Mac 路徑實測：`--user-data-dir=/tmp/step1ne-chrome-pdf`、`--print-to-pdf=<out_path>`、一般 `file://` 網址）；唯一差別是多一次對 `out_path` 的 exists→remove（原本下一行就會做）。

## interview_lock_and_apology_20261002.patch（阿財：鎖過期被搶＋逾時後補道歉訊息）

- 問題：`LOCK_TTL_SEC=90` 比 claude 呼叫上限 `CLAUDE_TIMEOUT=240` 短。呼叫還在跑時鎖就過期，另一個處理者搶進來再回一次；原本那輪逾時後 `handle()` 的 except 還會無條件寫「系統出了點狀況，顧問會聯繫」並發 TG。2026-10-02 胡耀中（15:45 那輪）實際發生：同一則訊息被回一次正常、再收到一則道歉。
- 修法兩處：(1) `LOCK_TTL_SEC = CLAUDE_TIMEOUT + 30`；(2) except 寫道歉前先查最後一則是不是 assistant，是就只記 log、不插訊息、不發 TG。
- 這台**沒有套用**（共用工作目錄，會連帶影響所有程序，且檔案屬於上游）。`git apply --check` 已驗證可套用。套用後阿財要重啟（等沒人面談時）。
- 沒解決的：這台 claude 呼叫為什麼會卡到 240 秒（根因未查到）。

## interview_timeout_retry_20261005.patch（阿財：claude 逾時也重跑一次＋記錄耗時）
- 問題：claude 呼叫送出後沒有模型回應，等滿 240 秒才放棄，阿財直接對候選人道歉並中斷面談（10/2 胡耀中、10/5 陳南宏）。
- 修法：第一次呼叫只給 120 秒，逾時重跑，總預算仍是 240 秒；每次成功記一行耗時與嘗試次數。
- 已驗證：`git apply --check` 通過；測試副本 py_compile＋4 個假 subprocess 情境全過。**已於 2026-10-05 併入主線**（Jacky 確認），阿財在回合之間閒下來時會自己換上新版，不用手動重啟。
- `interview_lock_and_apology_20261002.patch` 已被上游採用（不用再套，現在套不上是正常的）；本補丁對最新 `origin/main` `apply --check` 通過。詳見 `docs/wsl2/回報/E12_阿財AI呼叫卡住_20261005-1425.md`。


---

# E17（2026-10-08）：阿財提速三件事 —— 三個 patch，**依序套用 1 → 2 → 3，尚未部署**

基準：origin/main `a604f45`；套用：`git apply docs/wsl2/patches/e17_1_transition_message_20261008.patch`，再依序 `e17_2_…`、`e17_3_…`（已驗證依序 `apply --check` 通過）。完整說明、量測、回放比較、變差的地方、沒做的事，見 `docs/wsl2/回報/E17_阿財提速_*.md`。測試與回放腳本在 `docs/wsl2/patches/e17_tests/`（`e17_run_all_tests.sh` 一次跑完；需在已套用三個 patch 的 worktree 裡、用跟 daemon 一樣的最小環境執行）。

## e17_1_transition_message_20261008.patch（等待過渡語）
- **改了什麼**：人選講完話超過 40 秒（常數 `TRANSITION_AFTER_SEC`）阿財還沒回，由程式（不是 LLM）寫入一句固定的話「收到，我整理一下您剛剛說的，稍等我一下。」。是否該送的判斷與寫入合成**同一句 SQL**（最後一則是人選、不是進房標記、這一輪還沒送過才寫），所以兩個程序同時處理、或正式回覆同時寫入，都不會重複或排在正式回覆之後。新增 `interview_markers.py`（認得這句話）。
- **為什麼要排除**：這句存進 `messages` 後，所有「誰在等誰」「給 LLM 的歷史」「報告」「訊息數」都把它當作不存在（`active_sessions`、`post_wrap_questions`、`paused_sessions`、出錯道歉前的檢查、`context_for`、`finish` 逐字稿、`answer_timing`，以及 `verification_engine`／`export_pdf`／`job_switch`／`candidate_questions_digest`）。**漏排除的後果最嚴重**：最後一則變 assistant，阿財會以為人選已被回而不再回他。
- **驗證**：真 daemon＋假 D1／假 claude 的模擬（卡 60 秒、卡 10 秒、兩個程序同時、卡了又失敗、顧問先回）全過；300 次隨機競態 0 次錯序；5 場真實面談 40 個回話點，LLM 提示詞改前改後逐字相同。
- **Mac 上套用會不會有影響**：程式碼路徑相同（不分主機）；備援機（`BACKUP_DELAY_SEC`）平常被擋、有人等太久才動，這時一樣適用。**Worker／前台／顧問後台不在這個 repo，過渡語對它們是一則普通的 assistant 訊息**，請確認那邊沒有用「最後一則是 assistant」判斷已回覆。
- 沒解決：40 秒會讓一場平均出現約 3 次同一句（22% 的輪次）；建議改 60 秒或文案輪替，見回報 §1。

## e17_2_prepare_earlier_20261008.patch（預約人選提早準備＋計畫更可靠）
- **改了什麼**：(1) 預約（`status='scheduled'`）24 小時內且**已交卷或中高階免測驗**的人選，提早擬題目計畫與預熱開場白；(2) 職缺或顧問面談前交代在計畫／開場白擬好之後改過 → 作廢重擬（compare-and-set、最多 3 次、60 秒檢查一次）；(3) 計畫失敗重試 1→2→5→10 分鐘（原本固定 30 分鐘）；(4) 背景呼叫（擬計畫、預熱）第一次逾時 120→210 秒、總預算 330 秒（面談回覆不變）——log 裡成功的擬計畫呼叫中位數 108 秒、p90 120 秒，失敗 39 次對成功 18 次。
- **驗證**：記憶體 sqlite 單元測試（名單條件、作廢、競態、重試序列、逾時參數）全過；對正式 D1 **唯讀乾跑**（所有寫入攔下）。
- **注意**：預約人選很少（`bookings` 表是空的、全部預約 5 筆、最近 30 場只有 2 場），所以這個 patch 對大多數人選沒有幫助；真正的缺口是「交卷後 1～10 分鐘就進房」，回報 §2 有分析與建議（交卷前就擬，品質看起來不受影響，但初篩相依與快取沒驗證，**沒做**）。上線後會作廢陳鵬仁、蔡依庭的舊計畫與陳昱霖、Teresa Kuo 放了好幾週的開場白。

## e17_3_report_not_covered_20261008.patch（顧問版報告「這次沒問到的地方」）
- **改了什麼**：`finish()` 組報告 prompt 的區塊原封不動抽成 `build_report_prompt()`；報告 prompt 多一段指示（位置：「適合的職務方向」之後、「建議」之前，並寫明不可省略原有段落）與（有的話）題目計畫；JSON 規格＋第 19 條規則＋白名單 `not_covered`；`deliver.py` 顧問版新增一格、`reporttpl/consultant.html` 新增卡片（空陣列整張不顯示）。客戶版（舊＋v2）是白名單組版，不讀這一欄。
- **驗證**：`with_not_covered=False` 的 prompt 與改動前真正的 `finish()` 送給 claude 的 prompt 逐字相同（7 場）；差異法（有無 `not_covered`）客戶版輸出逐字相同（7 場真實報告）；7 場真實面談改前／改後回放；`report_to_json` 真實轉換測試。
- **變差的地方（要知道）**：第一版規則（新段落放「建議之前」）讓 5 場裡 4 場漏掉原本必須有的段落（改前基準 2/5），所以改了位置規則（現在的版本）；重跑後 2/5，與基準相同，但樣本只有 5 場。「其他推薦職缺」現況就會漏，不是這個 patch 造成的。
- **沒處理**：`consultant_call_report.py` 電洽後合併重寫會把舊 `content_md` 帶進去，新段落可能變成過時內容。

---

# E17c（2026-10-09）：交卷前就擬題目計畫 —— `e17c_plan_early_20261009.patch`，**未上線，待 Mac 審**

基準：origin/main（E17b 上線後）；套用：`git apply docs/wsl2/patches/e17c_plan_early_20261009.patch`（只改 `interview_daemon.py`）。完整說明、回放比較、品質風險見 `docs/wsl2/回報/E17c_*.md`。
- 投遞後（`pending_assessment`、72 小時內、還沒進房、履歷抓得到文字）就擬題目計畫；有面談在處理回覆時不跑（`ACAI_PLAN_EARLY=0` 可關）。
- 提早擬的計畫會把「缺測驗／初篩」的資料快取住 → 新增 `_late_refresh` 補撈（每場每 30 秒最多一次、只補缺的）。
- 初篩晚於計畫、人還沒進房 → 計畫重擬一次（`ACAI_PLAN_REDO_ON_SCREENING=0` 可關）。
- 測試 `e17_tests/e17c_test.py`（**需先套 E17c patch**，沒套會失敗）；回放腳本 `e17c_plan_ab.py`／`e17c_cmp.py`。`e17_run_all_tests.sh` 已含 e17c_test。
