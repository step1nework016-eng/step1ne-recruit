# E24b｜Mac 審核：E24 兩個 patch 都核准（2026-10-10）

## 已上線（你不用動）
- `e24_daemon_20261010.patch` 已由 Mac 套進 main（commit c1df615，含新檔 `cjk_punct.py`）。阿財閒下來會自己換版。
  - 測試：e24_test 全過；e23b、e17b、acai_watch 全過；e17c 失敗是因為 E17c 還沒上線（預期）。

## 要你做：SKILL 這台套用
Mac 推不上 `step1nework016-eng/recruiting-workflow`（HTTPS 回 not found、兩把 SSH key 都沒權限）。Mac 本機已 commit（61adf1b），但遠端沒有。
請在 WSL2 本機：
```
cd ~/claude-projects/工作流程技能包/recruiting-workflow
git apply ~/claude-projects/工作流程技能包/step1ne-recruit/docs/wsl2/patches/e24_skill_20261010.patch
git add interview-conductor/SKILL.md && git commit -m "interview-conductor：E24 不要每輪複誦、必追問清單（Mac 核准）"
git push   # 你那台推得上就推；推不上就回報，並說明你那台平常怎麼同步這個 repo
```
**確認沒有面談進行中**再讓阿財讀到新 SKILL（它每次呼叫都讀檔，套用即生效）。

## 新版小問題（你回報的）一併處理
- 「那我們換個方向」連用 3 次 → `_pace_hints` 的起手式偵測也要涵蓋「換個方向／換個主題」這類轉場句。
- 一句評價式安慰 → 已在規則禁止，觀察就好。

## 回報
`docs/wsl2/回報/E24b_日期時間.md`：SKILL 套用結果、git push 結果／這台怎麼同步、轉場句修正、下一場真實面談的觀察（全形比例、複誦開頭次數、題數）。
