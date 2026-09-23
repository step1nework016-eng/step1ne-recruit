#!/usr/bin/env python3
"""把「查不到窗口」的開發目標補上真正的招募窗口。

## 為什麼要獨立一關

2026-09-23 第一次跑正向開發：6 家目標全部命中（寶雅 1027 筆職缺、大樹 892 筆…），
但 **6 家的窗口信箱全是 null**。總指揮沒有亂編，這是對的——但信寄不出去。

瓶頸不是「找不到公司」，是「找不到人」。這跟 2026-08-26 主動開發找人選時
「50 人跑通但 Email 0」是同一個病。所以把它拆成獨立的一關，專心解決。

## ⛔ 不要再犯的錯（2026-09-23 當天踩的）

第一版去抓公開資訊觀測站的**發言人**與 MOPS 信箱，拿到的是
`fin888@`、`investor@`、`stock@`、`webservice@`——那些是**財務與投資人關係**窗口。
寄招募委外過去等於丟進黑洞。Jacky 原話：「不能找發言人，這些是不相關的資料」。

**要找的是人資／招募／Talent Acquisition，不是任何其他部門。**

## 規矩（沿用 talent_sourcing_agent.py 那一套，不要另立一套）

- 只用合法公開資訊；**不登入任何帳號、不繞驗證碼或付費牆、不用外洩資料庫**
- LinkedIn 公開頁面（搜尋引擎看得到的）可以引用；需要登入才看得到的一律不用
- **不得用猜測產生 Email**（`hr@`、`recruit@` 這種套公式一律不行）。
  查不到就老實回 null——寄到不存在的信箱比沒寄更糟，而且沒人會發現。
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

MODEL = 'claude-opus-5'   # ⚠️ 一定要寫全名，這台機器 `--model opus` 會解析成舊版

PROMPT = """你要替下面這幾家公司找出**招募／人資窗口**，好讓我們寄一封談「委託招募合作」的信。

## 你要找的是誰

人資部門、招募部門、Talent Acquisition、人力資源處——**負責找人的那個部門**。
規模大的公司找到部門層級的公開招募信箱也可以。

## ⛔ 以下這些查到也不要寫（寫了等於白做）

- 發言人、代理發言人、財務、投資人關係（`ir@`、`investor@`、`stock@`、`fin@`）
- 客服、總機、網站意見信箱（`service@`、`webservice@`、`info@`、`contact@`）
- 業務、採購、媒體公關

這些部門不會處理招募委外，信寄過去不會被轉交。

## 查的順序（照這個跑，查到就停，不要每個來源都跑一遍）

1. **公司官網的「人才招募／加入我們／徵才」頁** —— 找頁面上**實際寫出來**的招募信箱或聯絡人
2. **104／1111 的公司頁「聯絡方式 → 聯絡人」** —— 這裡常有人名（例：人資 余小姐）
3. **公開的 LinkedIn 個人頁**（搜尋引擎找得到的那種，不要登入）——
   搜「公司名 + 人資 / 招募 / HR / Talent Acquisition / Recruiter」，
   找得到姓名與職稱就記下來，**LinkedIn 上看不到 email 就不要編**
4. **新聞稿、人事異動報導、徵才活動頁** —— 有時會寫出人資主管姓名

## 只輸出這個 JSON，不要有其他文字

{{
  "results": [
    {{
      "company": "公司全名，照我給你的寫",
      "contact_name": "姓名或『人資 余小姐』這種稱呼；查不到寫 null",
      "contact_title": "職稱；查不到寫 null",
      "contact_email": "**只有在公開頁面上實際看到這個字串**才寫；用推的、用套公式的一律寫 null",
      "contact_phone": "公開電話含分機；查不到寫 null",
      "linkedin_url": "那個人的公開 LinkedIn 網址；沒有寫 null",
      "source_url": "你是在哪一頁看到的，**一定要填**，沒有來源的資料不算數",
      "confidence": "high|medium|low —— high 只給『官網或104上明確寫著這是招募窗口』的",
      "note": "一句話說明你怎麼找到的，或為什麼找不到"
    }}
  ],
  "run_summary": "這次用了哪些來源、幾家找到、幾家沒有、卡在哪"
}}

## 找不到怎麼辦

**就老實填 null，並在 note 寫清楚你查過哪些地方。**
查不到不是失敗——編一個出來才是。後面顧問會自己打電話問，
但他需要知道「已經查過官網和 104 了」才不會重工。

## 這次要查的公司

{companies}
"""


def run_claude(prompt, workdir, timeout=1800):
    os.makedirs(workdir, exist_ok=True)
    open(os.path.join(workdir, 'contact.prompt.txt'), 'w', encoding='utf-8').write(prompt)
    import importlib.util
    spec = importlib.util.spec_from_file_location('d', os.path.join(ROOT, 'interview_daemon.py'))
    D = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(D)
    r = subprocess.run(
        ['claude', '--print', '--model', MODEL,
         '--setting-sources', '', '--permission-mode', 'bypassPermissions'],
        input=prompt, capture_output=True, text=True, cwd=workdir,
        env=D.env_with_cf(), timeout=timeout)
    out = (r.stdout or '').strip()
    open(os.path.join(workdir, 'contact.raw.txt'), 'w', encoding='utf-8').write(out)
    if not out:
        raise RuntimeError(f'總指揮沒有回應：{(r.stderr or "")[:300]}')
    # ⚠️ 總指揮很愛在 JSON 後面附一段說明（2026-09-23 正向開發第一次跑就是被這個
    #    害到整批作廢）。用 raw_decode 只取第一個物件，後面的文字丟掉就好。
    s = out.find('{')
    if s < 0:
        raise RuntimeError('回應裡找不到 JSON，看 contact.raw.txt')
    obj, _ = json.JSONDecoder().raw_decode(out[s:])
    return obj


def lookup(companies, workdir):
    """companies 是公司全名的清單。回 {company: 結果} 的 dict。"""
    lines = '\n'.join(f'- {c}' for c in companies)
    spec = run_claude(PROMPT.format(companies=lines), workdir)
    return {r.get('company'): r for r in (spec.get('results') or [])}, spec.get('run_summary', '')


# 明顯不是招募窗口的信箱，出口再擋一次。
# 只寫在 prompt 裡不夠——2026-09-23 第一版就是模型照抓不誤。
_BAD_MAIL = ('ir@', 'investor@', 'stock@', 'fin@', 'finance@', 'service@',
             'webservice@', 'info@', 'contact@', 'sales@', 'pr@', 'media@')


def is_recruiting_mail(email):
    e = (email or '').strip().lower()
    if not e or '@' not in e:
        return False
    return not any(e.startswith(b) for b in _BAD_MAIL)
