"""阿福健檢報告最後一段：「跟你方向接近的職缺」（2026-10-23，Jacky 看過示意圖同意；測試版）。

不另寫一套比對邏輯——全部借用阿財的 acai_job_recs.py：
  · 候選職缺：load_candidate_jobs()（status='open'、職缺頁 step1ne.com/jobs/<slug>/ 回 200、
    排除他已經應徵／被推薦過的；用 email 判斷同一人）
  · AI 挑選：build_prompt()（換成阿福、健檢的說法，多給健檢表單與履歷摘要）
  · 把關：validate_picks()（原話要對得上、客戶名守門員、保護特徵字眼、承諾字眼，任何一項不過整個職缺丟掉）
  · 給人選看的欄位：public_job_view() 白名單（職稱／地點縮到縣市區／職缺頁公開的薪資說明／類型／工作內容）
這支只做三件事：把健檢資料轉成阿財那支看得懂的形狀、加上「一鍵應徵」連結、結果存回 checkups.job_recs_json。

只有新版表單（checkups.form_version='v2'）的場次才做；舊表單照舊，報告不會多這一段。
沒有夠接近的職缺就回空清單，報告整段不出現（寧可不推，不要硬塞）。任何失敗都不往外丟，不影響報告本身。
"""
import json
import urllib.parse

import acai_job_recs as AJR
import recommendation_service as RS

MAX_RECS = 3
APPLY_URL = ('https://step1ne.com/apply/?job={slug}&title={title}'
             '&utm_source=checkup_report&utm_medium=report&utm_campaign=afu_job_recs')
SENTINEL = '（本人已進入健檢對談室）'


def log(msg):
    RS.log(f'[阿福推薦職缺] {msg}')


def eligible(checkup):
    return (checkup or {}).get('form_version') == 'v2'


def _form(c):
    """健檢表單上跟找工作有關的格子（新版表單才有後四格）。"""
    out = {}
    for k, label in (('want_job_types', '想找的工作'), ('want_locations', '期望地點'),
                     ('expected_salary', '期望薪資'), ('education_level', '最高學歷'),
                     ('current_title', '目前職稱'), ('current_industry', '目前產業'),
                     ('note', '最想知道的事')):
        v = str(c.get(k) or '').strip()
        if v:
            out[label] = v[:200]
    return out


def to_acai_ctx(ctx):
    """健檢資料 → acai_job_recs 看得懂的 ctx（application＋conversation）。"""
    c = ctx.get('checkup') or {}
    conv = []
    for m in ctx.get('messages') or []:
        t = str(m.get('content') or '')
        if not t.strip() or t == SENTINEL:
            continue
        conv.append({'role': 'candidate' if m.get('role') == 'user' else 'assistant', 'content': t})
    return {
        'application': {'name': c.get('name'), 'email': c.get('email'),
                        'expected_salary': c.get('expected_salary'),
                        'location_ok': c.get('want_locations'), 'note': ''},
        'conversation': conv,
    }


def build_prompt(ctx, actx, jobs):
    p = AJR.build_prompt(actx, jobs)
    p = p.replace('你是獵頭公司的 AI 面談助理「阿財」。一場面談剛結束，',
                  '你是獵頭公司的 AI 履歷健檢助理「阿福」。一場履歷健檢對談剛結束，')
    p = p.replace('給他在面談室最後參考。', '放在他健檢報告的最後一段給他參考。')
    p = p.replace('**只能根據人選今天自己講過的話或表單寫的內容**',
                  '**只能根據人選今天自己講過的話、健檢表單或他的履歷**')
    p = p.replace('用「您」稱呼', '用「你」稱呼')
    p = p.replace('要填人選**原話**的一小段（照抄逐字稿，6～30 字）',
                  '要填人選**原話**的一小段（照抄逐字稿、健檢表單或履歷原文，6～30 字）')
    p = p.replace('【今天的面談逐字稿】', '【今天的健檢對談逐字稿】')
    c = ctx.get('checkup') or {}
    extra = '\n【健檢表單】\n' + json.dumps(_form(c), ensure_ascii=False, indent=1)
    resume = str(ctx.get('resume_text') or '').strip()
    if resume:
        extra += '\n\n【履歷摘錄】\n' + RS.scrub_sensitive(resume[:5000])
    return p.replace('【人選表單】', extra.strip() + '\n\n【人選表單】', 1)


def said_text(ctx, actx):
    """「原話」可以從哪裡找：對談裡他講的話＋健檢表單＋履歷原文。"""
    c = ctx.get('checkup') or {}
    return (AJR._said_text(actx) + ''.join(_form(c).values())
            + str(ctx.get('resume_text') or '')[:20000])


def with_apply_link(r):
    v = {k: r.get(k) for k in AJR.CANDIDATE_FIELDS}
    v['apply_url'] = APPLY_URL.format(slug=urllib.parse.quote(r['slug']),
                                      title=urllib.parse.quote(r.get('title') or ''))
    return v


def generate(cid, ctx, llm):
    """回傳給報告用的推薦清單（可能是空的）＋給顧問查的明細。llm(prompt) → dict。"""
    c = ctx.get('checkup') or {}
    name = c.get('name') or ''
    if not eligible(c):
        return [], None
    try:
        actx = to_acai_ctx(ctx)
        jobs, _ = AJR.load_candidate_jobs(cid, None, email=c.get('email'))
        if not jobs:
            log(f'{name}：沒有可推的開放職缺')
            return [], {'pool': 0}
        guard = AJR.ClientGuard.from_d1()
        out = llm(build_prompt(ctx, actx, jobs))
        recs, dropped = AJR.validate_picks(out, {j['slug']: j for j in jobs}, guard, said_text(ctx, actx))
        recs = recs[:MAX_RECS]
        detail = {'pool': len(jobs), 'dropped': dropped, 'prompt_version': AJR.PROMPT_VERSION + '+afu',
                  'recs': [dict(r) for r in recs]}   # 含 fit／evidence，只給顧問看
        log(f"{name}：{len(recs)} 個職缺" + (f"：{'、'.join(r['title'] for r in recs)}" if recs else '（不放這一段）'))
        return [with_apply_link(r) for r in recs], detail
    except Exception as e:
        log(f'{name}：產生推薦職缺失敗（不影響報告）：{e}')
        return [], {'error': str(e)[:300]}
