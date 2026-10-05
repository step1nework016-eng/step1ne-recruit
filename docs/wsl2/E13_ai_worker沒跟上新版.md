# E13｜WSL2：ai_worker 沒有自動換成新版（2026-10-05 14:50 起）

## 結論先講
Mac 這邊 14:50 推了 ai_worker 新版（電洽準備卡會標「沒跟阿財談過／進了沒回答」、補問最多 6 題）。
之後 14:52、14:55 兩次 Fiona（application 22b6651d…）的電洽卡都被這台（DESKTOP-L5E8NAE）接走，
產出來還是舊格式（沒有 interview_coverage 欄位）——代表這台的 ai_worker 還在跑舊版。
自動更新照理 5 分鐘內會換，請查為什麼沒換。

## 要做的事
1. `cd ~/claude-projects/工作流程技能包/step1ne-recruit && git log --oneline -3`，看最新是不是有 `e30e28b` 之後那幾個 ai_worker commit（「顧問直接建檔的人選一樣當第一輪面試出題」）。
2. 看 ai_worker 的 log 最近一次「AI 工作佇列處理器啟動」是幾點；有沒有自動更新相關的錯誤（git fetch 失敗、權限、SSH）。
3. 確認新版程式有在跑：`grep -c "interview_coverage" ai_worker.py` 要 ≥ 3。
4. 沒有人在面談時（`python3 ../_tools/d1_readonly.py "SELECT COUNT(*) n FROM applications WHERE interview_state='active'"` 是 0）重啟 ai_worker。
   ⚠️ ai_worker 跟阿財是不同程式，重啟 ai_worker 不會中斷面談；但這台有人在面談時新版 ai_worker 只會同時做 1 件（避免拖慢阿財，見 10/5 陳南宏事件）。
5. 順便回報 E7（阿財逾時）一直沒交：10/2 胡耀中、10/5 14:15 陳南宏都是在這台逾時。陳南宏那次已確認主因是 Mac 推的「自動備卡」讓這台同時跑 2 個 claude（已修），但 10/2 那次原因仍未知。

## 回報
寫到 `docs/wsl2/回報/E13_日期時間.md` 並 push：第 1～3 步的輸出原文、為什麼沒自動更新（一句話＋證據）、修了什麼。
