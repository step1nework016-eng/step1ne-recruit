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


def load_clients(d1_query):
    """d1_query 是一個 callable，吃 SQL 回傳 list[dict]。抽成參數是為了能離線測試。"""
    rows = d1_query(
        "SELECT name, aliases, relation, blocked_reason, via_client, owner FROM clients") or []
    out = []
    for r in rows:
        al = [x.strip() for x in (r.get('aliases') or '').split('\n') if x.strip()]
        out.append({**r, '_variants': variants(r.get('name'), al)})
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
