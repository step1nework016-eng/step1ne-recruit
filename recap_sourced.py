#!/usr/bin/env python3
"""用「AI 當初已經逐條判好的結果」套現在的規則重算等第，不再叫 AI（2026-10-10）。

起因：10/10 Jacky「找人選都沒在找」。其實晚上找人有找到好人選（例：嘉聯益會計處經理，AI 當下評 A），
是程式的等第上限把「學歷、熟悉法規、特定 ERP」這種 LinkedIn 本來就看不到的條件當成缺證據，壓成 C。
sourcing_quality.py 已把這類改成「電話確認」。這支把已存的人選照新規則重算一次。

為什麼不用 regrade_sourced.py：那支會重新叫 AI，而且預設不能上網，拿到的證據比當初找人時少，
重評結果反而更差（實測友達會計部經理 C59→C40）。這支只重算，不重新判斷證據。

只處理還沒被處理的人（status=new、fit 空、owner 空、沒轉成應徵者），只會「升等」不會降等。
條件編號：舊規則清單（含被新規則濾掉的「不拘」這類）→ 依文字對到新清單編號，避免編號錯位。

用法：python3 recap_sourced.py [--job slug] [--apply]   （預設只印、不寫）
"""
import argparse
import json
import re
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '_tools'))
import sourcing_quality as Q  # noqa: E402
import d1_rw as d  # noqa: E402

ORDER = {'A': 0, 'B': 1, 'C': 2, 'D': 3}


def old_condition_texts(job):
    """10/10 修改前的條件清單（只要文字與順序，用來把舊編號對回去）。"""
    items = []
    for f in Q.COND_FIELDS:
        items += Q._split(job.get(f))
    for p in Q._split(job.get('preferred_background')):
        if '必要' in p or '必備' in p:
            items.append(re.sub(r'[（(]?必要[）)]?|[（(]?必備[）)]?', '', p).strip() or p)
    if not items and (job.get('title') or job.get('main_duties')):
        items.append(f"實際做過這個職缺的核心工作：{job.get('title') or ''}｜{str(job.get('main_duties') or '')[:120]}")
    seen, out = set(), []
    for t in items:
        key = re.sub(r'\s+', '', t)
        if key in seen or re.search(r'非必要|不限|加分|尤佳|為佳|優先|不需要|不要求', t):
            continue
        seen.add(key)
        out.append(t[:200])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--job')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    where = "s.status='new' AND s.fit IS NULL AND s.owner IS NULL AND s.converted_application_id IS NULL AND s.grade IN ('C','D') " \
            "AND j.status='open' AND s.raw_json LIKE '%must-check-v1%'"
    params = []
    if a.job:
        where += ' AND s.job_slug=?'; params.append(a.job)
    rows = d.q(f"SELECT s.id, s.name, s.headline, s.grade, s.score, s.raw_json, s.job_slug FROM sourced_candidates s "
               f"JOIN jobs j ON j.slug=s.job_slug WHERE {where}", params)
    jobs = {}
    up = {}
    for r in rows:
        try:
            raw = json.loads(r['raw_json'] or '{}')
        except ValueError:
            continue
        if r['job_slug'] not in jobs:
            jobs[r['job_slug']] = d.q('SELECT * FROM jobs WHERE slug=?', [r['job_slug']])[0]
        job = jobs[r['job_slug']]
        new = Q.job_conditions(job)
        new_no = {re.sub(r'\s+', '', c['text']): c['no'] for c in new}
        old = old_condition_texts(job)
        checks = []
        for x in raw.get('must_check') or []:
            try:
                t = old[int(x.get('no')) - 1]
            except (TypeError, ValueError, IndexError):
                continue
            n = new_no.get(re.sub(r'\s+', '', t))
            if n:
                checks.append({**x, 'no': n})
        det0 = raw.get('grade_detail') or {}
        # 只救「被 10/10 放寬的那幾條（查不到證據／必備技能比例）壓成 C」的人：
        # D 級、或當初因為沒對上、職能、產業被擋的，可能還有別的關卡（例：職訓課程不算經歷）——一律不動。
        old_why = ' '.join(det0.get('why') or [])
        if r['grade'] != 'C' or det0.get('cap') != 'C' or det0.get('unmet') or re.search(r'職能|產業|沒對上|沒有逐條', old_why):
            continue
        c = {'must_check': checks, 'function_match': raw.get('function_match'),
             'industry_required': raw.get('industry_required'), 'industry_match': raw.get('industry_match'),
             'grade': det0.get('model_grade') or r['grade'], 'score': max(int(r['score'] or 0), 60),
             'grade_reason': raw.get('grade_reason')}
        g, score, reason, det = Q.enforce_grade(c, new)
        if ORDER.get(g, 9) >= ORDER.get(r['grade'], 9):
            continue   # 只升不降
        up[g] = up.get(g, 0) + 1
        print(f"{r['grade']}{r['score']}→{g}{score}｜{r['name']}｜{(r['headline'] or '')[:30]}｜{r['job_slug']}")
        if a.apply:
            raw.update({'must_check': checks, 'grade_detail': det, 'grade_jd_fp': Q.job_fingerprint(job)})
            note = Q.grade_note(c, g, det, tag='10/10 依新規則重算')
            d.q('UPDATE sourced_candidates SET grade=?, score=?, note=?, raw_json=? WHERE id=?',
                [g, score, note, json.dumps(raw, ensure_ascii=False)[:20000], r['id']])
    print(f"看了 {len(rows)} 位，升等 {sum(up.values())} 位 {up}" + ('（已寫入）' if a.apply else '（試算，沒寫入）'))


if __name__ == '__main__':
    main()
