# E40｜人才標籤 backfill：舊人選（約 96 位）跟開著的職缺（約 63 個）先分類（等 Jacky 核准才做）

## 為什麼
10/21 做了「人才標籤＋自動推薦」（talent-tags 分支）：每份阿財報告存好後，AI 把人選分到固定的
20 種職務類別＋等級（新手可培訓／有經驗／資深主管），職缺開出來也分一次。配對前先用類別、等級、
薪資、地區把明顯不相干的人篩掉，剩下的才給 AI 排序。
**新面談的人會自動分類，但之前談過的人沒有標籤**——沒標籤的人目前一律放行（等於沒篩），
所以要補跑一次舊資料，篩選才會真的生效。Mac 記憶體小，這批交給 WSL2。

## 前提（缺一不做）
1. talent-tags 分支已經被 Jacky 核准、合併進 main、推上去，WSL2 已拉到新版（ai_worker 會自動換版）。
2. 兩張新表已建（第一次用到會自動建；也可以先手動跑
   `npx wrangler d1 execute step1ne-recruit --remote --file migrations/2026-10-21_talent_tags.sql`）。
3. Mac 的 `~/.config/workflow-os/ai_worker_skip_kinds.txt` 已加上
   `candidate_tagging_backfill,job_tagging`（Mac 不接這批，留給 WSL2）。

## 要做
1. 先數：`python3 scripts/backfill_talent_tags.py`（只印數字，不寫任何資料）
2. 確認數字合理（人選 ~96、職缺 ~63）後排進佇列：`python3 scripts/backfill_talent_tags.py --apply`
3. ai_worker 會自己一筆一筆跑（排在背景工作最後面，不會擋到電洽卡、阿財面談）。一筆約 10～20 秒。

## 硬規則
- backfill 只打標籤，**不會**觸發配對、**不會**發 TG、**不會**寄任何信（payload 的 match_after=false）。
- 同一份報告重複排只會留一筆（工作 id 固定是 `ctag:<report_id>`／`jtag:<slug>|<updated_at>`），重跑 --apply 安全。
- AI 看不到年齡、性別、婚育、國籍、兵役（輸入是白名單，摘要裡的這類字眼先遮成「〔略〕」）。

## 交付
- 跑完查：
  `SELECT tag, level, COUNT(*) n FROM candidate_tags GROUP BY 1,2 ORDER BY n DESC`
  `SELECT COUNT(DISTINCT job_slug) FROM job_tags`
  `SELECT status, COUNT(*) FROM ai_jobs WHERE kind IN ('candidate_tagging_backfill','job_tagging') GROUP BY 1`
- 回報到 docs/wsl2/回報/E40_回報.md：三個查詢結果＋失敗的幾筆 error 原文，push。
