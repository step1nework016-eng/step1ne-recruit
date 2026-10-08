#!/bin/bash
# 在「已套用三個 patch 的 worktree」裡跑全部測試（用跟正式 daemon 一樣的最小環境）
SP=/mnt/c/Users/haoli/AppData/Local/Temp/claude/C--Users-haoli-AppData-Roaming-Claude-scratch-workspaces-76862a63-ce7e-4664-bdf6-76297138bb1b-77be4121-8405-4fdd-a741-1c87f37a8a4f-scratch-2026-09-16-e59ff2/ba608703-7b36-4d2a-bfd8-d5af8c2c7d76/scratchpad
cd /tmp/wt_e17b
E="env -i HOME=/home/jack TZ=Asia/Taipei LANG=C.UTF-8 PATH=/home/jack/.nvm/versions/node/v22.22.0/bin:/home/jack/.local/bin:/usr/local/bin:/usr/bin:/bin /usr/bin/python3"
for t in e17_test_p1 e17_test_p1_prompt_eq e17_test_p2 e17_test_p3 e17_test_p3b; do
  echo "################ $t"
  $E $SP/$t.py 2>&1 | tail -${1:-14}
  echo "exit=${PIPESTATUS[0]}"
done
