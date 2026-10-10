#!/bin/bash
# 在「已套用 E17 三個 patch（含 E17b 修改）的 worktree」裡跑全部測試（用跟正式 daemon 一樣的最小環境）
# 用法：cd <worktree> && bash docs/wsl2/patches/e17_tests/e17_run_all_tests.sh [尾端行數]
ROOT=$(cd "$(dirname "$0")/../../../.." && pwd)
T=$ROOT/docs/wsl2/patches/e17_tests
cd "$ROOT"
E="env -i HOME=/home/jack TZ=Asia/Taipei LANG=C.UTF-8 PATH=/home/jack/.nvm/versions/node/v22.22.0/bin:/home/jack/.local/bin:/usr/local/bin:/usr/bin:/bin /usr/bin/python3"
rc=0
for t in $T/e17_test_p1.py $T/e17_test_p1_prompt_eq.py $T/e17_test_p2.py $T/e17_test_p3.py $T/e17_test_p3b.py $T/e17b_test.py $T/e23b_test.py $ROOT/tests/acai_watch_test.py $T/e17c_test.py; do
  echo "################ $(basename $t)"
  $E $t 2>&1 | tail -${1:-14}
  r=${PIPESTATUS[0]}; echo "exit=$r"; [ "$r" != 0 ] && rc=1
done
echo "全部結果：$([ $rc = 0 ] && echo 通過 || echo 有失敗)"
exit $rc
