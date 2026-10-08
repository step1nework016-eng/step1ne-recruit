#!/bin/bash
L=~/aijob-automation/logs/interview.log
echo "log 行數: $(wc -l < $L)；最早一行: $(head -1 $L | cut -c1-60)"
echo "--- 計畫失敗行數（全 log，無日期）: $(grep -c '擬題目計畫失敗' $L)；計畫已擬好: $(grep -c '題目計畫已擬好' $L)"
grep '擬題目計畫失敗' $L | sed -E 's/^\[[0-9:]+\] //' | cut -c1-14 | sort | uniq -c | sort -rn | head -15
echo "--- 擬計畫那次 claude 的耗時與第幾次嘗試（計畫已擬好的前一行）"
grep -B1 '題目計畫已擬好' $L | grep -oE '耗時 [0-9]+ 秒（第 [0-9]' | sed -E 's/耗時 //; s/ 秒（第 / /' | sort -n | awk '{a[NR]=$1; if ($2==1) f++} END{print "筆數",NR,"中位",a[int(NR/2)+1],"p90",a[int(NR*0.9)],"最大",a[NR],"第1次就成功",f}'
echo "--- 全部 claude 呼叫耗時分布（含面談回覆、預熱、計畫）"
grep -oE '耗時 [0-9]+ 秒（第 [0-9]' $L | sed -E 's/耗時 //; s/ 秒（第 / /' | sort -n | awk '{a[NR]=$1; if ($2==2) s++} END{print "筆數",NR,"中位",a[int(NR/2)+1],"p90",a[int(NR*0.9)],"p95",a[int(NR*0.95)],"最大",a[NR],"第2次才成功",s}'
echo "--- 120 秒沒回應重跑次數: $(grep -c '沒回應，重跑' $L)"
