#!/usr/bin/env python3
"""常駐 daemon 的自動更新（2026-09-21 從 ai_worker.py 抽出來共用）

## 為什麼要有這個

這套系統跑在兩台機器上（Mac 與 WSL2），程式碼靠 git 同步。在這支之前只有
ai_worker.py 有自動更新，其他 daemon 全部要人工 ssh 上去 git pull。

實際出過的事：
  - WSL2 還在跑舊版 ai_worker，兩台搶同一筆 ai_jobs，WSL2 搶到就寫回舊格式，
    Backend 判定不合格擋下來，顧問看到「明明生了卻沒有」，最後靠 Jacky 在
    兩邊傳話才發現是版本沒同步。（ai_worker 就是為此加了自動更新）
  - 2026-09-21 王仁君的 rematch 失敗，錯誤是「未知的工作類型」——同樣是
    WSL2 跑舊版、不認得新的 kind。
  - 同日社群發文加了審稿與價格體檢，WSL2 沒有自動更新，那台跑的還是舊版，
    兩道新關卡等於不存在。

## ⚠️ 最重要的設計：不是每個 daemon 都可以隨時重啟

自動更新是用 os.execv 換掉自己這個程序。對 ai_worker 這種「處理完一件工作
才檢查一次」的來說沒問題，但對 interview_daemon 來說——

**阿財正在跟候選人對話時重啟，那個人就會看到對話卡住、沒有回應。**
2026-09-21 王美日已經因為別的原因遇過一次「系統出了點狀況」然後沒下文，
差點跑掉。不能再讓自動更新製造第二次。

所以 `maybe_self_update()` 收一個 `can_restart` 判斷函式。回 False 就這輪
不重啟，等下一輪再看——新版最多晚幾分鐘生效，但不會打斷任何人。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# 多久檢查一次遠端有沒有新版本。5 分鐘是 ai_worker 用了就沒問題的值：
# 夠即時（推完程式碼五分鐘內兩台就同步），又不會把 git fetch 打太兇。
CHECK_INTERVAL_SEC = 300


def _rev(ref):
    return subprocess.run(['git', 'rev-parse', ref], cwd=HERE,
                          capture_output=True, text=True, timeout=10).stdout.strip()


# 這個程序啟動時載入的是哪一版程式（import 的當下記下來）。見 maybe_self_update 的說明。
try:
    START_HEAD = _rev('HEAD')
except Exception:  # noqa: BLE001
    START_HEAD = ''


def maybe_self_update(last_checked, log=print, can_restart=None, name=''):
    """檢查 origin/main 有沒有新 commit，有就 pull 並重啟自己。

    回傳新的 last_checked（呼叫端要把它存起來下次傳進來）。

    參數
      last_checked  上次檢查的時間戳（time.time()）
      log           印訊息用，預設 print
      can_restart   可選的判斷函式。回 False 代表「現在不能重啟」，
                    這輪就只記錄不動作。**有真人在等的 daemon 一定要傳。**
      name          只用在訊息裡，方便在混合的 log 檔裡分辨是誰

    為什麼用 os.execv 而不是靠 launchd/systemd 重啟：這樣同一套邏輯在
    Mac、WSL2、或任何裝置上都能用，不依賴外部監督機制怎麼設定。
    """
    now = time.time()
    if now - last_checked < CHECK_INTERVAL_SEC:
        return last_checked
    tag = f'{name} ' if name else ''
    try:
        subprocess.run(['git', 'fetch', 'origin', 'main', '--quiet'],
                       cwd=HERE, timeout=30, check=True)
        local = _rev('HEAD')
        remote = _rev('origin/main')
        if not local or not remote:
            return now
        # ⚠️ 2026-10-01 修（WSL2 E6 回報）：原本只比「磁碟上的 HEAD vs origin/main」。
        #    WSL2 有 6～7 支常駐程式共用同一個資料夾，第一支 pull 完之後，其他支看到
        #    HEAD == origin/main 就以為自己是最新的、不重啟——記憶體裡跑的還是舊程式。
        #    （ai_worker 因此回報 266 次「未知的工作類型」；Mac 開發機推完程式碼後
        #    daemon 不會自己換版也是同一個原因。）
        #    現在改成：要不要重啟，看的是「磁碟上的程式」跟「我啟動時載入的那一版」
        #    （START_HEAD）一不一樣。本機有還沒推的 commit（HEAD 領先 origin）時
        #    pull 是空動作、HEAD 也沒變，所以不會一直重啟。
        if local == remote and local == START_HEAD:
            return now

        if can_restart is not None:
            try:
                ok = can_restart()
            except Exception as e:
                # 判斷不出來就當成不能重啟。寧可晚幾分鐘更新，
                # 也不要在不確定的情況下把正在服務的人踢掉。
                log(f'⚠️ {tag}無法判斷現在能不能重啟（{str(e)[:80]}），這輪先不更新')
                return now
            if not ok:
                log(f'⏸️ {tag}有新版本（{START_HEAD[:7]}→{remote[:7]}），'
                    f'但現在有人在使用，等閒下來再更新')
                return now

        if local != remote:
            subprocess.run(['git', 'pull', 'origin', 'main', '--quiet'],
                           cwd=HERE, timeout=30, check=True)
        head_now = _rev('HEAD')
        if head_now and head_now != START_HEAD:
            log(f'🔄 {tag}偵測到新版本（執行中 {START_HEAD[:7]} → 磁碟上 {head_now[:7]}），重啟自己')
            os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        log(f'⚠️ {tag}自動更新檢查失敗（不影響這一輪，下次再試）：{str(e)[:150]}')
    return now
