"""後台 API 權杖：優先用「具名權杖」，沒有就退回舊的共用 RECRUIT_ADMIN_TOKEN。

2026-10-23 資安 F002 Stage A：後台以前只有一把萬用鑰匙（ADMIN_TOKEN），
分不出是誰在開門。現在每台機器／每支程式各拿一把具名權杖，Worker 會在
紀錄裡寫是哪一把開的門。換證期間新舊並行：這台機器還沒放新權杖時，
行為跟以前完全一樣（讀 RECRUIT_ADMIN_TOKEN）。

讀取順序（先找到先用）：
  1. 環境變數：prefer 裡的名字 → STEP1NE_API_TOKEN
  2. ~/.config/workflow-os/tokens.env、recruit.env 裡同樣的名字
  3. 呼叫端自己讀到的舊值（fallback）
  4. 以上都沒有 → 環境變數／兩個 env 檔裡的 RECRUIT_ADMIN_TOKEN

Mac：STEP1NE_ACAI_TOKEN＝阿財面談程式專用（mac-acai）；
     STEP1NE_API_TOKEN＝Mac 其他程式（mac-workers）。
WSL2：STEP1NE_API_TOKEN＝wsl2-workers（見 docs/wsl2/E48）。
"""
import os

NEW_VAR = 'STEP1NE_API_TOKEN'
OLD_VAR = 'RECRUIT_ADMIN_TOKEN'
_FILES = ('~/.config/workflow-os/tokens.env', '~/.config/workflow-os/recruit.env')


def _read_files(names):
    for f in _FILES:
        try:
            with open(os.path.expanduser(f), encoding='utf-8') as fh:
                lines = fh.read().splitlines()
        except Exception:
            continue
        for n in names:
            for line in lines:
                if line.startswith(n + '='):
                    v = line.split('=', 1)[1].strip().strip("'\"")
                    if v:
                        return v
    return None


def get(fallback=None, prefer=()):
    names = tuple(prefer) + (NEW_VAR,)
    for n in names:
        v = (os.environ.get(n) or '').strip()
        if v:
            return v
    v = _read_files(names)
    if v:
        return v
    if fallback:
        return fallback
    v = (os.environ.get(OLD_VAR) or '').strip()
    return v or _read_files((OLD_VAR,))


def new(prefer=()):
    """只找具名權杖；找不到回 None（呼叫端自己照舊讀 RECRUIT_ADMIN_TOKEN）。"""
    names = tuple(prefer) + (NEW_VAR,)
    for n in names:
        v = (os.environ.get(n) or '').strip()
        if v:
            return v
    return _read_files(names)


def wrap(fn, prefer=()):
    """把舊的「讀權杖」函式包起來：有具名權杖就用它，沒有就呼叫原本的函式（行為不變）。"""
    def inner(*a, **k):
        return new(prefer) or fn(*a, **k)
    inner.__name__ = getattr(fn, '__name__', 'token')
    return inner
