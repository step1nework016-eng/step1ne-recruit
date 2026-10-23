# E51：特殊職缺檢查卡（主管司機檢查）——WSL2 拉新版、重啟 ai_worker

日期：2026-10-23（Mac）

## 一句話
`git pull` 之後**重啟 WSL2 的 ai_worker.py** 就好。不用改設定檔、不用加金鑰。
（ai_worker 也會自己偵測新版、手上工作做完就換版；確認一下有換到就行，不要硬砍正在跑的工作。）

## 這次改了什麼
1. 新檔 `special_check.py`：只對 `site_settings.special_check_jobs` 清單裡的職缺（目前只有 `executive-family-driver-taipei`）
   的人選，讀履歷產「履歷疑點＋三個分數」，寫進新表 `special_check_cards`。**不推任何 TG**、不寄信。
2. `ai_worker.py`：
   - 新 kind `special_check`（交給 special_check.py；列入可回收清單，重跑只是覆蓋同一列）。
   - tick() 每分鐘最多掃一次 `special_check.scan()`（清單外的職缺連查都不查）。
3. D1：新表 `special_check_cards`、`special_check_attendance`、開關 `special_check_jobs`——Mac 已建好，WSL2 不用動。
4. 司法院裁判書：**不自動查**（該系統有機器人防護），程式沒有任何對司法院的連線。

## WSL2 要做的
1. `cd <step1ne-recruit>` → `git pull`（要拉到 commit「特殊職缺檢查卡」或更新）。
   檢查：`ls special_check.py` 存在、`grep -n "special_check" ai_worker.py` 找得到 4 處左右。
2. 重啟 WSL2 的 ai_worker.py（照原本啟動它的方式；systemd 的話 `systemctl --user restart` 那個服務）。
3. 阿財（interview_daemon.py）、阿福、parse_resumes.py 這次沒改，不用重啟。
   ⚠️ Mac 已把 `special_check` 列進 `ai_worker_skip_kinds.txt`（Mac 不跑重活），所以這種卡**只有 WSL2 會產**；WSL2 沒換版前工作會留在佇列等，不會失敗。
4. 驗收：ai_worker log 沒有 Traceback、沒有「特殊職缺檢查卡掃描這輪出錯」。
   之後這個職缺有新人選（有履歷）時，log 會出現 `[special_check] 排了 N 張特殊職缺檢查卡`（正常）。

## 回報
寫在 `docs/wsl2/回報/E51_*.md`：pull 到的 commit、重啟時間、log 前 20 行有沒有錯誤。不要寫任何金鑰。
