# E52：阿財 AI 卡住時改成背景重試、整場最多道歉一次——WSL2 拉新版、確認阿財自己重啟

日期：2026-10-24（Mac）

## 一句話
`git pull` 之後確認 **interview_daemon.py（阿財）有自己換到新版**、log 沒有 Traceback。不用改設定、不用加金鑰。

## 由來
10/24 01:07～01:19 謝佳恩那場：claude 連續逾時（WSL2 回空、Mac 120 秒×2），每失敗一輪就對人選送
「不好意思，我這邊系統出了點狀況。我們的顧問會直接與您聯繫…」——4 分鐘送了 3 次，TG 也跳兩則「⚠️ 面談出錯」，
AI 恢復後阿財又若無其事接著問。Jacky 要求以後人選感覺不出來。

## 這次改了什麼（只有 interview_daemon.py）
1. AI 這輪接不上 → **不道歉**，把這場的鎖設成 30／60／120 秒後到期（依人選已等多久），下次輪詢（兩台誰先搶到）在背景重試。
   人選只會看到既有的「稍等我一下」等待語（一輪最多一句，原本的 SQL 守衛）。
2. 這一輪重試的第一次改用**備用模型**（環境變數 `ACAI_FALLBACK_MODEL`，沒設＝`claude-haiku-5-5`），之後回主模型。
3. 人選等滿約 10 分鐘（`AI_STUCK_GIVEUP_SEC=600`）仍接不上，才送一次「系統出了點狀況、顧問會聯繫」；
   **整場最多一次**（SQL 守衛：這場送過就不再寫）。之後仍每 2 分鐘背景試，接上了第一句先說「抱歉讓您久等了，我們繼續。」
4. TG：這一輪第一次失敗推一則「⚠️ 面談出錯…已在背景重試，還不用接手」；真的放棄時推「🆘 面談需要顧問接手」；
   放棄後又接上推「✅ 阿財已重新接上」。去重記在 D1 `notify_throttle`（key 開頭 `acai_stuck:`／`acai_fallback:`／`acai_resumed:`），兩台共用，不會重複推。
5. 鎖的長度（LOCK_TTL_SEC）沒改。

## WSL2 要做的
1. `cd <step1ne-recruit>` → `git pull`（拉到 commit「阿財：AI 卡住改背景重試」或更新）。
   檢查：`grep -n "AI_STUCK_GIVEUP_SEC\|FALLBACK_TALK_MODEL" interview_daemon.py` 找得到。
2. 阿財偵測到新版會自己重啟（手上那一輪回完才換）。看 log 有一行「偵測到新版本…重啟自己」＋「面談引擎啟動」。
   10 分鐘內沒換到就照原本方式重啟阿財（不要在有人面談中硬砍）。
3. 確認 WSL2 的 claude CLI 認得備用模型（只跑一次、花不到一毛）：
   `echo '只回 {"ok":1}' | claude -p --model claude-haiku-5-5 --output-format json`
   回 `unrecognized_model` 的話：在阿財的環境變數設 `ACAI_FALLBACK_MODEL=claude-sonnet-5-5`（Mac 實測兩個都可用）再重啟阿財，回報裡寫明。
4. 離線測試（不碰正式資料）：`python3 tests/ai_stuck_retry_test.py` 應顯示「失敗 0 項」。
5. 驗收：阿財 log 沒有 Traceback。

## 回報
寫在 `docs/wsl2/回報/E52_*.md`：pull 到的 commit、阿財換版時間、第 3 步備用模型測試結果、測試結果、log 有無錯誤。不要寫任何金鑰。
