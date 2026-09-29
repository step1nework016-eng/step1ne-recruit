#!/usr/bin/env python3
"""分級階梯題模擬面談（2026-09-29）——上線前驗證用，不寫資料庫、不聯絡任何人。

用真實職缺的面談設定（context_for 讀一筆既有應徵只為了拿到職缺資料），
換上本機試產的階梯題（ladder_dryrun/<slug>.json），讓另一個 AI 扮演背景固定的人選，
跟新版阿財跑完整場面談，最後用新版報告結構算出「每個主題到第幾級」與分數。

用法：python3 sim_ladder_interview.py <persona代號>
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import interview_daemon as D

OUT = os.path.join(HERE, 'ladder_dryrun', 'sim')

PERSONAS = {
    # 培訓版、真的是新手：土木系應屆，只在課堂畫過平面圖作業
    'trainee_l1': {
        'slug': 'trainee-engineering-design-engineer',
        'sample_app': 'c0c75b81-f0b6-4001-9d6d-54f827f4b0cb',
        'resume': '王小明，23歲前的應屆畢業生（不要主動提年齡）。國立某科大土木系 2026 年 6 月畢業。'
                  '大二上過 AutoCAD 必修課，畫過住宅平面圖作業（老師給範例照著畫），沒有實習、沒有專題用到繪圖軟體。'
                  '暑假在便利商店打工一年。沒碰過 Revit，只在 YouTube 看過介紹。',
        'truth': '你是真的新手：AutoCAD 只在課堂照範例畫過平面圖，知道圖層、比例尺大概是什麼但講不深；'
                 '從沒自己獨立完成過一張會被拿去用的圖；沒處理過圖面衝突；更不可能教人。'
                 '被問到做不到的事，就老實說沒做過，或講得很含糊。動機：想進半導體相關產業、願意學、可以配合駐廠與加班。'
                 '期望月薪 3.5 萬，最快下個月可到職。',
    },
    # 資深版，但實力其實只有入門：做過一年繪圖助理
    'senior_l2': {
        'slug': 'engineering-design-engineer-hsinchu',
        'sample_app': 'c0c75b81-f0b6-4001-9d6d-54f827f4b0cb',
        'resume': '陳大華。機械系畢業，在一家小型機電工程行當繪圖助理一年，主要是依主管的紅線修改 AutoCAD 圖、出圖列印。'
                  '會一點 Revit 基本操作（上過 40 小時補習班）。應徵資深工程設計工程師。',
        'truth': '你的真實程度：看得懂機電圖；在主管指導下改過圖（第 2 級）；'
                 '但從沒自己從需求開始獨立畫完一套圖，都是主管先畫好你改；沒處理過跨專業碰撞；沒帶過人。'
                 '被問到第 3 級以上的事，會想用模糊的話帶過（「都有碰過」「看情況」），被追問就講不出細節。'
                 '動機：想加薪、想做大案子。期望月薪 5 萬，到職需一個月。',
    },
}

PERSONA_PROMPT = '''你在扮演一位正在跟 AI 面談助理「阿財」線上文字面談的求職者。只輸出你這一輪要回覆的話（1～4 句，口語、繁體中文），不要加任何說明。

【你的履歷】
{resume}

【你的真實程度（一定要照這個演，不要表現得比這個好）】
{truth}

【到目前為止的對話】
{conv}

現在輪到你回覆阿財最後說的話。'''


def persona_reply(p, conv):
    txt = '\n'.join(f'{"阿財" if m["role"] == "assistant" else "你"}：{m["content"]}' for m in conv[-16:])
    r = subprocess.run(['claude', '-p', '--model', 'claude-sonnet-5', '--output-format', 'text'],
                       input=PERSONA_PROMPT.format(resume=p['resume'], truth=p['truth'], conv=txt),
                       capture_output=True, text=True, timeout=180, env=D.env_with_cf() if hasattr(D, 'env_with_cf') else None)
    return (r.stdout or '').strip()[:600] or '嗯，我想一下。'


def main():
    key = sys.argv[1]
    p = PERSONAS[key]
    lad = json.load(open(os.path.join(HERE, 'ladder_dryrun', f'{p["slug"]}.json'), encoding='utf-8'))
    # 9/29 第一輪模擬的教訓：直接沿用 sample_app 的整包資料會帶進那位真人的東西——
    # 兩週前的開始時間（阿財以為已經談了幾百分鐘，立刻收尾）、電訪紀錄、題目計畫、讀不到履歷。
    # 所以只借「職缺相關」的欄位，人選這一側全部換成乾淨的模擬資料。
    base = D.context_for(p['sample_app'])
    job = D.d1(f"SELECT * FROM jobs WHERE slug = {D.q(p['slug'])}")[0]
    ctx = {k: base[k] for k in ('other_jobs',) if k in base}
    ctx['job'] = job
    ctx['application'] = {'id': 'sim-' + key, 'name': '模擬人選', 'job_slug': p['slug'], 'job_title': job.get('title'),
                          'interview_started_at': D.datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                          'interview_state': 'active'}
    ctx['resume_text'] = p['resume']
    ctx['resume_readable'] = True
    ctx['resume_source'] = '模擬'
    src = D.fetch_job_understanding_sources(p['slug'])
    ctx['blockers'] = src.get('blockers') or []
    if src.get('job_card_summary'):
        ctx['job_card_summary'] = src['job_card_summary']
    ex = dict(src.get('expertise') or {})
    ex['ladder'] = dict(lad, start_level=lad['start_level'], expected_level=lad['expected_level'])
    ctx['expertise'] = ex
    ctx['conversation'] = [{'role': 'candidate', 'content': '（候選人已進入面談室）'}]
    os.makedirs(OUT, exist_ok=True)
    for turn in range(40):
        prompt = D.build_prompt(ctx, D.talk_skill(ctx))
        res = D.run_claude(prompt)
        msgs = [m for m in (res.get('messages') or []) if str(m).strip()][:3]
        for m in msgs:
            ctx['conversation'].append({'role': 'assistant', 'content': D._trad(m)})
        print(f'--- 第{turn + 1}輪 阿財：' + ' / '.join(msgs)[:300], flush=True)
        if res.get('end'):
            break
        ans = persona_reply(p, ctx['conversation'])
        ctx['conversation'].append({'role': 'candidate', 'content': ans})
        print(f'    人選：{ans[:200]}', flush=True)
    transcript = '\n'.join(f'{"阿財" if m["role"] == "assistant" else "候選人"}：{m["content"]}' for m in ctx['conversation'])
    open(os.path.join(OUT, f'{key}_transcript.txt'), 'w', encoding='utf-8').write(transcript)
    js = D.report_to_json('【模擬面談逐字稿，請直接依逐字稿判斷】\n' + transcript, ctx, '模擬人選')
    data = json.loads(js) if js else {}
    json.dump(data, open(os.path.join(OUT, f'{key}_report.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n===== 結果 =====')
    print(json.dumps(data.get('skill_ladder'), ensure_ascii=False, indent=1))
    for f in data.get('ladder_findings') or []:
        print(f"  {f['topic']}｜問了第{f.get('asked_levels')}級｜到第{f['reached_level']}級｜{f['evidence'][:60]}")
    fs = data.get('fit_scores') or {}
    print('總分', fs.get('total'), fs.get('grade'), '分流', (data.get('route') or {}).get('label'))


if __name__ == '__main__':
    main()
