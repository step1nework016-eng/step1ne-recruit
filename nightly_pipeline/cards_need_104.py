#!/usr/bin/env python3
"""列出今天早上要用真的 Chrome 去 104 補資料的客戶（最多 20 家），輸出 JSON。

給 Windows 那台 Claude 桌面版的 07:00 排程任務用（prompts/chrome_104_morning.md）：
  wsl.exe -- bash -lc "cd ~/claude-projects/工作流程技能包/step1ne-recruit && python3 nightly_pipeline/cards_need_104.py"

背景：104 會擋背景工具（夜間的 claude -p 只能從搜尋結果拿 104 網址，進不去 104），
所以 104 現開職缺與招募窗口改由早上用真的 Chrome 去看。

優先順序：
  1. 已指派、三項都有（電話＋104 企業頁＋官網或粉專）的公司，而且認識客戶（bd_company_profiles.hiring）
     裡還沒有「104 現開」字樣 → 要補「現開職缺＋招募窗口」
  2. 沒指派（不合格）、而且只缺 104 企業頁的公司 → 要先在 104 找到企業頁，再補同樣的東西
  同一優先順序內，新進名單的排前面。

輸出每家：company、company_104_url（可能是空的）、hiring_head（目前 hiring 前 200 字）、
          priority（1 或 2）、task（這家要做什麼，給 Chrome 那邊看）。

用法：python3 cards_need_104.py [--limit 20]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import d1_http as D  # noqa: E402

SKIP_STATUS = "('rejected','superseded','draft','blocked','invalid')"


def q(sql):
    r = D.query(sql)
    return (r.get('results') if isinstance(r, dict) else r) or []


def _missing(guard):
    try:
        return (json.loads(guard or '{}') or {}).get('missing')
    except (json.JSONDecodeError, AttributeError):
        return None


def main():
    limit = 20
    if '--limit' in sys.argv:
        limit = int(sys.argv[sys.argv.index('--limit') + 1])

    base = ("SELECT o.company, MAX(o.company_104_url) u, MAX(o.assigned_to) who, MAX(o.created_at) c, "
            "MAX(COALESCE(o.hr_phone, o.company_phone, o.call_phone)) phone, "
            "MAX(json_extract(CASE WHEN json_valid(o.guard_json) THEN o.guard_json END, '$.website')) gw, "
            "MAX(o.guard_json) guard, p.website pw, p.hiring "
            "FROM bd_outreach o LEFT JOIN bd_company_profiles p ON p.company=o.company "
            f"WHERE o.channel='phone' AND o.status NOT IN {SKIP_STATUS} "
            "AND COALESCE(o.manual_stage,'') NOT IN ('closed','lost') ")

    out, seen = [], set()

    # 1. 已指派、三項合格、還沒查過 104 現開
    rows = q(base + "AND o.assigned_to IS NOT NULL AND o.company_104_url LIKE '%104.com.tw%' "
                    "AND COALESCE(p.hiring,'') NOT LIKE '%104 現開%' "
                    "GROUP BY o.company ORDER BY c DESC")
    for r in rows:
        if len(out) >= limit:
            break
        if not r.get('phone') or not (r.get('pw') or r.get('gw')) or r['company'] in seen:
            continue
        seen.add(r['company'])
        out.append({'company': r['company'], 'company_104_url': r['u'],
                    'hiring_head': (r.get('hiring') or '')[:200], 'priority': 1,
                    'task': '打開 104 公司頁：讀聯絡人／電話，再讀「工作機會」前 20 個職缺'})

    # 2. 不合格、只缺 104 企業頁
    if len(out) < limit:
        rows = q(base + "AND o.assigned_to IS NULL AND COALESCE(o.company_104_url,'')='' "
                        "GROUP BY o.company ORDER BY c DESC")
        for r in rows:
            if len(out) >= limit:
                break
            miss = _missing(r.get('guard'))
            if miss is None or [m for m in miss if '104' not in str(m)] or r['company'] in seen:
                continue  # 只收「缺的東西只有 104」的
            seen.add(r['company'])
            out.append({'company': r['company'], 'company_104_url': '',
                        'hiring_head': (r.get('hiring') or '')[:200], 'priority': 2,
                        'task': '先在 104 用公司全名找到企業頁（名稱要完全一樣才算），再做跟第 1 類一樣的事'})

    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
