#!/bin/bash
# LINE 訊息格式驗證器——改任何 Flex 卡片之後跑這支，不用等真人踩到才發現壞掉。
#
# 為什麼要有：2026-09-09 做「已關閉職缺」卡片時，本機沒辦法確認 Flex 結構
# 對不對，只能拿現有卡片肉眼比對。LINE 有官方的 validate 端點，送進去只驗
# 格式不會真的發訊息給任何人，金鑰就在 step1ne-line.env。
#
# 用法：
#   ./validate_flex.sh                 # 驗 closedJobMessages 的兩種情況
#   ./validate_flex.sh <某個json檔>     # 驗自己準備的 {"messages":[...]}
set -e
cd "$(dirname "$0")"
set -a; . ~/.config/workflow-os/step1ne-line.env; set +a

check() {  # $1=說明 $2=json檔
  local code
  code=$(curl -s -o /tmp/flexres.txt -w "%{http_code}" -X POST \
    https://api.line.me/v2/bot/message/validate/reply \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $LINE_CHANNEL_ACCESS_TOKEN" --data @"$2")
  if [ "$code" = "200" ]; then echo "  ✅ $1"
  else echo "  ❌ $1 → $(cat /tmp/flexres.txt)"; FAIL=1; fi
}

if [ -n "$1" ]; then check "$1" "$1"; exit ${FAIL:-0}; fi

gen() {  # $1=有沒有相關職缺(1/0) $2=輸出檔
  node -e '
    const fs=require("fs");
    let src=fs.readFileSync("src/index.js","utf8");
    const a=src.indexOf("function jobLocText"), b=src.indexOf("async function lineReply(env");
    eval(src.slice(a,b)+"\nglobalThis.f=closedJobMessages;");
    const withRel=process.argv[1]==="1";
    const rows=withRel?[{slug:"a",title:"培訓工程設計工程師",locations:"['桃竹苗地區', '台中']"},
                        {slug:"b",title:"資深半導體專案工程師",locations:"桃竹苗地區、台中、雲林"},
                        {slug:"c",title:"資深職業安全衛生工程師",locations:null}]:[];
    const env={DB:{prepare:()=>({bind:()=>({all:async()=>({results:rows})})})}};
    (async()=>{const m=await f(env,{slug:"bim-engineer",title:"BIM 工程師（無經驗可・正職）",company_id:"co_x"});
      fs.writeFileSync(process.argv[2],JSON.stringify({messages:m}));})();
  ' "$1" "$2"
}

echo "驗證 closedJobMessages："
gen 1 /tmp/flex_with.json;  check "有相關職缺（3 個）" /tmp/flex_with.json
gen 0 /tmp/flex_none.json;  check "沒有相關職缺（只給列表）" /tmp/flex_none.json
exit ${FAIL:-0}
