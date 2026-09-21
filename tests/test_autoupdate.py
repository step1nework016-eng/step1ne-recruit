#!/usr/bin/env python3
"""自動更新的四個情境（2026-09-21）

最重要的是情境 B：**阿財正在跟候選人對話時不可以重啟**。
自動更新是 os.execv 換掉整個程序，中途重啟那個人就會看到對話卡住。
2026-09-21 王美日已經因為別的原因遇過一次「系統出了點狀況」然後沒下文，
差點跑掉；不能再讓自動更新製造第二次。

跑法：python3 tests/test_autoupdate.py　不碰資料庫、不碰網路、不會真的重啟。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import autoupdate  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  ← {detail}" if detail and not cond else ''))


class _Harness:
    """攔掉 subprocess 與 os.execv，模擬「遠端有新版本」但不真的動作。"""

    def __enter__(self):
        self.logs, self.git = [], []
        self._run, self._execv = autoupdate.subprocess.run, autoupdate.os.execv

        def fake_run(cmd, **kw):
            self.git.append(cmd[1])

            class R:
                returncode = 0
                stdout = 'aaaaaaa' if cmd[:3] == ['git', 'rev-parse', 'HEAD'] else 'bbbbbbb'
            return R()

        def fake_execv(*a):
            raise SystemExit('__EXECV__')

        autoupdate.subprocess.run = fake_run
        autoupdate.os.execv = fake_execv
        return self

    def __exit__(self, *a):
        autoupdate.subprocess.run = self._run
        autoupdate.os.execv = self._execv

    def log(self, m):
        self.logs.append(m)

    def run(self, can_restart=None):
        try:
            autoupdate.maybe_self_update(0, self.log, can_restart=can_restart, name='測試')
            return False          # 沒重啟
        except SystemExit:
            return True           # 有重啟


# A：沒人在用 + 有新版 → 應該更新並重啟
with _Harness() as h:
    restarted = h.run(can_restart=lambda: True)
    check('A1 沒人在用且有新版 → 會重啟', restarted)
    check('A2 有真的執行 git pull', 'pull' in h.git, str(h.git))
    check('A3 有告知偵測到新版本', any('偵測到新版本' in x for x in h.logs))

# B：有人在用 + 有新版 → 絕對不可以重啟（最重要的一條）
with _Harness() as h:
    restarted = h.run(can_restart=lambda: False)
    check('B1 有人在使用時，即使有新版也不重啟', not restarted)
    check('B2 連 git pull 都不做（避免程式碼換掉但程序沒換，版本錯亂）',
          'pull' not in h.git, str(h.git))
    check('B3 有告知在等閒下來', any('等閒下來' in x for x in h.logs))

# C：判斷函式自己壞掉 → 保守處理，寧可不更新
with _Harness() as h:
    def boom():
        raise RuntimeError('DB 連不上')
    restarted = h.run(can_restart=boom)
    check('C1 判斷不出來能不能重啟時，一律不重啟', not restarted)
    check('C2 有把原因記下來', any('無法判斷' in x for x in h.logs))

# D：還沒到檢查間隔 → 不該去打 git（避免每輪都 fetch）
with _Harness() as h:
    autoupdate.maybe_self_update(time.time(), h.log, name='測試')
    check('D1 未達檢查間隔時完全不碰 git', not h.git, str(h.git))

# E：沒傳 can_restart（不需要閘門的 daemon）→ 照常更新
with _Harness() as h:
    check('E1 沒有閘門的 daemon 照常更新', h.run(can_restart=None))

check('F1 檢查間隔是 5 分鐘（夠即時又不會把 git fetch 打太兇）',
      autoupdate.CHECK_INTERVAL_SEC == 300, str(autoupdate.CHECK_INTERVAL_SEC))

print(f'\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項')
if FAIL:
    print('失敗：', FAIL)
sys.exit(1 if FAIL else 0)
