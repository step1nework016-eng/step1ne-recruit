# Phoebe 電腦一次性設定：每次開新對話自動拿到最新規則（2026-10-29）

> 給 Phoebe 的 Claude 照做，做完回報「設定完成」。只需要做一次。
> 目的：Phoebe 每次換新對話，Claude 都會自動 git pull＋讀到 Jacky 最新的規則，不用再手動複製檔案。

1. 把 `C:\Users\jessi\.claude\CLAUDE.md` 的內容**整個換成下面這一行**（Claude Code 會自動把那個檔案的內容載進來）：
   ```
   @C:/Users/jessi/step1ne-work/step1ne-recruit/prompts/Phoebe_全域CLAUDE.md
   ```
2. 打開 `C:\Users\jessi\.claude\settings.json`，在最外層加上 SessionStart hook（已有 hooks 就合併進去，不要刪掉其他設定）：
   ```json
   "hooks": {
     "SessionStart": [
       { "hooks": [ { "type": "command", "command": "git -C C:/Users/jessi/step1ne-work/step1ne-recruit pull -q" } ] }
     ]
   }
   ```
3. 關掉 Claude Code 再開一個新對話，問它「匿名履歷要怎麼做？」——它應該回答 `consultant_ops.py anon …`，就代表成功。
