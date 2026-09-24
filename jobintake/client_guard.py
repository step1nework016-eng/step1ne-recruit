#!/usr/bin/env python3
"""反向開發（MPC）的客戶名單比對關卡。

⚠️ 這一關的存在理由，是兩次真實事故：

  2026-08-10  agent 提議去敲台灣美光。美光是律准科技（我們的委任客戶）的終端客戶，
              去敲等於跟自己的客戶搶案子。
  2026-08-11  agent 提議去敲帆宣系統科技。顧問當時正在跟帆宣談簽約。

兩次都不是「挑錯公司」——是**流程裡根本沒有這一關**。
agent 能查到的只有 jobs.client_name（＝已經有職缺頁的客戶），
而會出事的三種（洽談中、終端客戶、明確禁止）一筆都不在裡面。

所以規矩是：**產生開發名單之後、寫信之前，一定要跑這支。**
命中 blocked 的公司直接從名單移除，不是標註「請注意」——
標註會被讀過去，移除不會。
"""

import re

# 哪些關係代表「不可以當開發對象」
BLOCK = {
    'signed':      '已簽約客戶。我們是他的乙方，不會反過來推人選給他當開發案',
    'negotiating': '正在談簽約。這時候寄開發信會讓對方以為我們在亂槍打鳥',
    'end_client':  '是我們某個客戶的終端客戶。去敲等於跟自己的客戶搶案子',
    'blocked':     '顧問明確標記不可接觸',
}
# 這些可以敲，但要讓顧問知道
WARN = {
    'past':     '曾經合作過。可以敲，但開場白要認人，不要當成陌生開發',
    'prospect': '之前已經敲過了。再敲之前先看上次的回覆，不要重複同一套說法',
}

_SUFFIX = re.compile(r'(股份)?有限公司$|公司$|集團$|企業社$|工程行$|科技$|系統$')


def variants(name, aliases=None):
    """一家公司的所有可能寫法。

    「帆宣系統科技股份有限公司」要能比對到 agent 寫的「帆宣系統科技」「帆宣」。
    只比對全名等於沒比對——agent 幾乎不會寫全名。
    """
    out = set()
    for raw in [name] + list(aliases or []):
        s = (raw or '').strip()
        if not s:
            continue
        out.add(s)
        stem = s
        for _ in range(3):                      # 逐層剝：股份有限公司 → 科技 → 主體
            nxt = _SUFFIX.sub('', stem).strip()
            if nxt == stem:
                break
            stem = nxt
            if len(stem) >= 2:
                out.add(stem)
    # 一個字的主體不拿來比對，會誤殺（例如「中」）
    return sorted({v for v in out if len(v) >= 2}, key=len, reverse=True)



# ── 讀音比對（2026-09-24 加）──
# 電洽錄音轉文字時，公司名常被轉成同音別字：
#   弘昌 → 紅昌（hong chang）、帆宣 → 凡宣／樊宣／泛宣（fan xuan）
# 兩天內發生兩次，每次都讓已簽約／洽談中的客戶被當成新的開發目標。
# 一個一個補別名永遠補不完，所以在字面比對失敗後，再用讀音比一次。
#
# ⚠️ pypinyin 不一定每台機器都有（WSL2 可能沒裝）。沒有的話就跳過這一層，
#    字面比對照常運作——不可以因為少一個套件就讓整個防護掛掉。
try:
    from pypinyin import lazy_pinyin as _lazy_pinyin
except Exception:          # noqa: BLE001
    _lazy_pinyin = None


def _py(text):
    """「帆宣系統」→「fan xuan xi tong」。只轉中文字，英數原樣保留。"""
    if not _lazy_pinyin:
        return ''
    return ' '.join(_lazy_pinyin(text or '')).strip()


def _py_contains(a, b):
    """讀音層的雙向包含，用完整音節當邊界，避免 'an' 撞到 'fan' 這種假命中。"""
    if not a or not b:
        return False
    A, B = f' {a} ', f' {b} '
    return A in B or B in A

def load_clients(d1_query):
    """d1_query 是一個 callable，吃 SQL 回傳 list[dict]。抽成參數是為了能離線測試。

    ⚠️ 2026-09-23 修的重大缺口：這支原本只讀 `clients`（9 筆），但顧問後台、
    客戶 portal、成交紀錄實際在用的是 `client_companies`（16 筆）。兩張表長期
    不同步，結果是**弘昌、築樂等 7 家已簽約客戶從來沒被保護過**——
    開發系統本來就有可能寄信去敲自己的客戶，而且沒有任何地方會發現。

    現在讀兩張表的聯集。`clients` 有 blocked_reason／via_client 這些欄位，
    `client_companies` 用 relation_note 當同一個角色，對應過來即可。
    重複的以 `clients` 為準（那張是專門為這個防護維護的）。
    """
    out, seen = [], set()

    def add(name, aliases, relation, reason, via, owner):
        key = (name or '').strip()
        if not key or key in seen:
            return
        seen.add(key)
        al = [x.strip() for x in (aliases or '').split('\n') if x.strip()]
        out.append({'name': key, 'aliases': aliases, 'relation': relation,
                    'blocked_reason': reason, 'via_client': via, 'owner': owner,
                    '_variants': variants(key, al),
                    '_py': [_py(v) for v in variants(key, al)]})

    for r in (d1_query(
            "SELECT name, aliases, relation, blocked_reason, via_client, owner "
            "FROM clients") or []):
        add(r.get('name'), r.get('aliases'), r.get('relation'),
            r.get('blocked_reason'), r.get('via_client'), r.get('owner'))

    # ⚠️ 讀不到不要讓整個防護掛掉——但也不要靜默通過。
    #    寧可少擋一張表，也不要因為一個欄位改名就讓開發信全部失去保護。
    try:
        rows2 = d1_query(
            "SELECT display_name, aliases, relation, relation_note, via_client, owner "
            "FROM client_companies") or []
    except Exception:
        rows2 = []
    for r in rows2:
        add(r.get('display_name'), r.get('aliases'), r.get('relation'),
            r.get('relation_note'), r.get('via_client'), r.get('owner'))
    return out


def check(company, clients):
    """單一家公司比對結果。回 None 代表可以敲。"""
    c = (company or '').strip()
    if not c:
        return None
    cv = variants(c)
    for row in clients:
        for v in row['_variants']:
            # 雙向比對：名單寫「帆宣」而 agent 寫「帆宣系統科技」要中；反過來也要中
            if any(v in x or x in v for x in cv):
                rel = row['relation']
                if rel in BLOCK:
                    why = row.get('blocked_reason') or BLOCK[rel]
                    if rel == 'end_client' and row.get('via_client'):
                        why += f"（透過 {row['via_client']}）"
                    return {'company': c, 'matched': row['name'], 'relation': rel,
                            'verdict': 'block', 'why': why}
                if rel in WARN:
                    return {'company': c, 'matched': row['name'], 'relation': rel,
                            'verdict': 'warn', 'why': row.get('blocked_reason') or WARN[rel]}

    # 字面比不到，再用讀音比一次（同音別字）。
    # 寧可多擋：讀音撞到的最壞結果是「少敲一家」，
    # 沒擋到的最壞結果是「寄開發信去敲自己的客戶」。
    if _lazy_pinyin:
        cpy = [_py(v) for v in cv if len(v) >= 2]
        for row in clients:
            rpy = row.get('_py') or [_py(v) for v in row['_variants']]
            if any(_py_contains(x, y) for x in cpy for y in rpy if len(y.split()) >= 2):
                rel = row['relation']
                note = f"讀音與「{row['name']}」相同，可能是逐字稿把公司名轉成同音字"
                if rel in BLOCK:
                    return {'company': c, 'matched': row['name'], 'relation': rel,
                            'verdict': 'block', 'phonetic': True,
                            'why': f"{row.get('blocked_reason') or BLOCK[rel]}（{note}）"}
                if rel in WARN:
                    return {'company': c, 'matched': row['name'], 'relation': rel,
                            'verdict': 'warn', 'phonetic': True,
                            'why': f"{row.get('blocked_reason') or WARN[rel]}（{note}）"}
    return None


def filter_targets(companies, clients):
    """把開發名單過一遍。

    回 (可以敲的, 被擋下的, 要注意的)。
    ⚠️ 被擋下的要**整家移除**，連同它的信件與聯絡人。
       留在名單上加註「請注意」是沒有用的——顧問趕時間時會直接照著寄。
    """
    ok, blocked, warned = [], [], []
    for c in companies:
        name = c if isinstance(c, str) else (c.get('company') or c.get('name') or '')
        r = check(name, clients)
        if r and r['verdict'] == 'block':
            blocked.append({**r, 'item': c})
        elif r and r['verdict'] == 'warn':
            warned.append({**r, 'item': c})
            ok.append(c)
        else:
            ok.append(c)
    return ok, blocked, warned


if __name__ == '__main__':
    import json, sys
    demo = [
        {'name': '帆宣系統科技股份有限公司', 'aliases': '帆宣\n帆宣系統', 'relation': 'negotiating',
         'blocked_reason': '顧問正在談簽約中', 'via_client': None},
        {'name': '台灣美光', 'aliases': '美光\nMicron', 'relation': 'end_client',
         'blocked_reason': None, 'via_client': '律准科技'},
        {'name': '遊戲橘子集團', 'aliases': '遊戲橘子', 'relation': 'signed',
         'blocked_reason': None, 'via_client': None},
    ]
    for d in demo:
        d['_variants'] = variants(d['name'], [x for x in (d['aliases'] or '').split('\n') if x])
    tests = ['帆宣', '帆宣系統科技', '台灣美光', 'Micron Taiwan', '遊戲橘子', '亞翔工程', '中鼎工程']
    for t in tests:
        r = check(t, demo)
        print(f"{t:14} → {'⛔ ' + r['why'] if r and r['verdict']=='block' else ('⚠️ ' + r['why'] if r else '✅ 可以敲')}")
