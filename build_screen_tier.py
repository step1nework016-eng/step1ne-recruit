#!/usr/bin/env python3
"""職缺「面談層級」判斷（2026-10-03 Jacky 拍板）。

為什麼要有這支：
    阿財升級的 12 項（動機深挖、驗證數字、會不會真的走、薪資拆解…）不是每個職缺都要全問——
    Jacky：「你不能問服務員那些事情，但核心的還是要」。
    jobs.seniority 不可靠（10/3 實查：34 個招募中職缺有 13 個沒填；VIP 接待服務員跟護理師、
    獵頭顧問都標 mid），所以另外判斷每個職缺屬於哪一層，並寫理由，顧問可以在職缺卡上改。

四層：
    basic    基層服務（服務員、客服、總機、行政助理、司機、直播主…）
    pro      一般專業（工程師、護理師、編輯、財會專員、顧問…非管理職）
    manager  中階主管（經理、課長、主任、專案經理，帶小團隊或負責一個功能）
    exec     高階（處長以上、集團層級主管、直屬總經理的主管職）

存放：job_expertise.screen_tier／screen_tier_reason／screen_tier_by（AI｜顧問名）。
顧問改過的（screen_tier_by 不是 AI）不會被覆蓋。

用法：
    python3 build_screen_tier.py --all [--dry-run]
    python3 build_screen_tier.py <job_slug> [--dry-run]
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_expertise as BE

TIERS = ('basic', 'pro', 'manager', 'exec')

PROMPT = '''下面是一家台灣獵頭公司的招募中職缺。請把每個職缺分到「面談層級」四層之一，
決定 AI 面談助理要問多深（越高層問得越深：會不會被慰留、薪資含股票、驗證成就數字、職涯規劃）。

四層：
basic＝基層服務（服務員、接待、客服、總機、行政助理、司機、直播主、門市等，不需專業證照或深度專業）
pro＝一般專業（工程師、護理師、編輯、財會、設計、獵頭顧問等專業職，但不帶人）
manager＝中階主管（經理、課長、主任、專案經理、物業經理，帶小團隊或負責一個功能）
exec＝高階（處長以上、集團層級主管、直屬總經理／董事長的主管職）

判斷看「實際工作內容與責任、薪資帶」，不要只看職稱有沒有「資深」——
「資深工程師」仍是 pro；「主管特助」看實際是否帶人與決策；薪資遠高於一般的社長司機仍看工作性質。

只輸出 JSON 陣列，不要其他文字：
[{{"slug":"…","tier":"basic|pro|manager|exec","reason":"一句話，為什麼"}}]

職缺：
{jobs}'''


def main():
    dry = '--dry-run' in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    where = f"slug = {BE.q(args[0])}" if args else "status IN ('open','active') AND slug != 'unspecified'"
    jobs = BE.d1(f"SELECT slug, title, seniority, service_line, salary_min, salary_max, salary_unit, "
                 f"main_duties, reports_to FROM jobs WHERE {where} ORDER BY slug")
    locked = {r['job_slug'] for r in BE.d1(
        "SELECT job_slug FROM job_expertise WHERE screen_tier IS NOT NULL AND COALESCE(screen_tier_by,'AI') != 'AI'")}
    jobs = [j for j in jobs if j['slug'] not in locked]
    if not jobs:
        BE.log('沒有要判斷的職缺（顧問改過的不覆蓋）')
        return
    lines = []
    for j in jobs:
        sal = f"{j.get('salary_min') or ''}～{j.get('salary_max') or ''}/{j.get('salary_unit') or ''}"
        lines.append(f"- slug={j['slug']}｜{j['title']}｜薪資 {sal}｜匯報 {j.get('reports_to') or '—'}｜"
                     f"內容：{(j.get('main_duties') or '')[:220]}")
    r = subprocess.run(['claude', '-p', PROMPT.format(jobs='\n'.join(lines)), '--model', BE.MODEL,
                        '--disallowed-tools', BE._BAN + ',WebSearch,WebFetch', '--setting-sources', '',
                        '--output-format', 'text'], cwd=HERE, capture_output=True, text=True,
                       env=BE.env_with_cf(), timeout=300)
    import re
    txt = re.sub(r'^```(?:json)?|```$', '', (r.stdout or '').strip(), flags=re.M).strip()
    try:
        out = json.loads(txt[txt.find('['): txt.rfind(']') + 1])
    except Exception:
        out = None
    if not isinstance(out, list):
        BE.log(f'❌ 判斷失敗：{(r.stdout or r.stderr or "")[:300]}')
        return
    slugs = {j['slug'] for j in jobs}
    for x in out:
        s, t = x.get('slug'), x.get('tier')
        if s not in slugs or t not in TIERS:
            continue
        BE.log(f'{s}：{t}｜{x.get("reason")}')
        if not dry:
            BE.d1(f"INSERT INTO job_expertise (job_slug, topics_json, questions_json, built_at, screen_tier, "
                  f"screen_tier_reason, screen_tier_by) VALUES ({BE.q(s)}, '[]', '[]', datetime('now','+8 hours'), "
                  f"{BE.q(t)}, {BE.q(x.get('reason'))}, 'AI') ON CONFLICT(job_slug) DO UPDATE SET "
                  f"screen_tier=excluded.screen_tier, screen_tier_reason=excluded.screen_tier_reason, screen_tier_by='AI'")


if __name__ == '__main__':
    main()
