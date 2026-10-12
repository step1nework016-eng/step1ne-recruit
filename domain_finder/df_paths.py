"""網域獵人：程式放 repo，會變動的資料放 repo 外面（E34，2026-10-12）。

為什麼分開：WSL2 每天會自動 git pull。名單、佇列、查分數紀錄每天都在變，
如果放在 repo 裡被追蹤，git pull 會被「本機有修改」擋下，整台的自動更新都會卡住。

  程式（這個資料夾）         ：*.py、*.md、industries.json、seed/（第一次的起始資料）
  資料（DOMAIN_FINDER_HOME）：網域口袋名單.csv（名單正本）、網域口袋名單.html、cache/、output/
                              預設 ~/aijob-automation/state/domain_finder/

第一次跑任何一支程式時，資料夾不存在就從 seed/ 複製一份過去；已經有的檔案絕不覆蓋。
"""
import os
import shutil
from pathlib import Path

CODE = Path(__file__).resolve().parent
SEED = CODE / "seed"
HOME = Path(os.environ.get("DOMAIN_FINDER_HOME") or "~/aijob-automation/state/domain_finder").expanduser()
CACHE = HOME / "cache"
CSVF = HOME / "網域口袋名單.csv"
HTMLF = HOME / "網域口袋名單.html"


def ensure_state():
    """資料夾不在就建，缺的起始檔從 seed/ 補上（只補缺的，不覆蓋）。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    for src in SEED.rglob("*"):
        if src.is_file():
            dst = HOME / src.relative_to(SEED)
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    return HOME


ensure_state()
