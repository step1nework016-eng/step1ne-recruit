#!/usr/bin/env python3
"""E24 模擬面談：同一個模擬人選（林小安型：無獵頭經驗、履歷有量化成果、薪資期待偏高）跟「舊版」或「新版」阿財跑完整場，
最後統計：半形標點、起手式重複、問題數、薪資期待與履歷數字有沒有被追問。不寫資料庫、不聯絡任何人（只唯讀 D1 的職缺與題庫）。
用法：python3 e24_sim.py <label> <worktree 路徑> <輸出資料夾>
環境變數 INTERVIEW_SKILL_PATH 可指到新版 SKILL。"""
import json, os, re, subprocess, sys, time, collections
label, wt, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
os.chdir(wt); sys.path.insert(0, wt)
os.environ.setdefault('INTERVIEW_HOST', 'test')
import interview_daemon as D
D.log = lambda m: None
os.makedirs(OUT, exist_ok=True)
LOG = open(os.path.join(OUT, f'{label}_progress.log'), 'a', encoding='utf-8')
def say(m): LOG.write(f'[{time.strftime("%H:%M:%S")}] {m}\n'); LOG.flush()

SLUG = 'headhunter-consultant'
RESUME = ('林小安。企業內部人資招募專員 2 年（科技公司）。年度招募 120+ 人，新人留任率由 58% 提升到 71%。'
          '做過 104 與 LinkedIn 搜尋、面談安排、協助談薪資。沒有獵頭公司經驗。學歷：企管系畢業。')
TRUTH = ('沒有獵頭經驗。履歷寫的「招募 120 人」是整個部門一年的總數，你自己負責的只有其中約 40 人的聯絡與初篩；'
         '留任率提升是主管推動的新人關懷，你只是幫忙打電話。被問到細節時會答得比較表面、有點含糊。'
         '想當獵頭是因為想靠獎金賺更多。期望底薪 40K，每季獎金希望超過 10 萬（其實沒算過，也不知道新人前幾個月成交情況）。'
         '10 月底後可報到，通勤約 30 分鐘，現職沒有競業限制。被問到薪資期待的依據時會說「看網路上獵頭賺很多」。')
PERSONA_PROMPT = '''你在扮演一位正在跟 AI 面談助理「阿財」線上文字面談的求職者。只輸出你這一輪要回覆的話（1～3 句，口語、繁體中文，標點可以隨意），不要加任何說明。

【你的履歷】
{resume}

【你的真實程度（一定要照這個演，不要表現得比這個好）】
{truth}

【到目前為止的對話】
{conv}

現在輪到你回覆阿財最後說的話。'''

def persona_reply(conv):
    txt = '\n'.join(f'{"阿財" if m["role"] == "assistant" else "你"}：{m["content"]}' for m in conv[-14:])
    r = subprocess.run(['claude', '-p', '--model', 'claude-sonnet-5', '--output-format', 'text'],
                       input=PERSONA_PROMPT.format(resume=RESUME, truth=TRUTH, conv=txt), capture_output=True, text=True, timeout=180)
    return (r.stdout or '').strip()[:500] or '嗯，我想一下。'

clean = getattr(D, '_clean_out', D._trad)
base = D.context_for('dd3cc10b-3f36-450f-b491-6a7713cec47f')      # 只借 other_jobs 等職缺側資料
job = D.d1(f"SELECT * FROM jobs WHERE slug = {D.q(SLUG)}")[0]
ctx = {k: base[k] for k in ('other_jobs',) if k in base}
ctx['job'] = job
ctx['application'] = {'id': 'sim-e24', 'name': '林小安', 'job_slug': SLUG, 'job_title': job.get('title'),
                      'expected_salary': '月薪 45K 以上，可談', 'available_date': '10 月底後',
                      'interview_started_at': D.datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'interview_state': 'active'}
ctx['resume_text'] = RESUME; ctx['resume_readable'] = True; ctx['resume_source'] = '模擬'
src = D.fetch_job_understanding_sources(SLUG)
ctx['blockers'] = src.get('blockers') or []
if src.get('job_card_summary'): ctx['job_card_summary'] = src['job_card_summary']
ctx['screen_tier'] = src.get('screen_tier'); ctx['acai_v2'] = src.get('acai_v2', False)
if src.get('expertise'): ctx['expertise'] = src['expertise']
ctx['conversation'] = [{'role': 'candidate', 'content': D.MK.ENTRY_MARKER}]

# 1) 擬題目計畫（跟 do_plan 同一套素材與提示詞）
t0 = time.time()
material = D.build_prompt(ctx, '（面談規範這裡不需要，你只是在擬題目）')
res = D.run_claude(material + '\n\n─────────────\n' + D.PLAN_PROMPT, first_try=210, total=330)
qs = [x for x in (res.get('questions') or []) if isinstance(x, dict) and x.get('q')]
cap = D._plan_cap(ctx) if hasattr(D, '_plan_cap') else 12
plan = {'questions': qs[:cap], 'faq': [x for x in (res.get('faq') or []) if isinstance(x, dict) and x.get('q')][:8]}
if hasattr(D, 'CP'): plan = D.CP.deep_fix(plan)
ctx['plan'] = plan
say(f'計畫 {len(plan["questions"])} 題（模型出 {len(qs)} 題），花 {time.time() - t0:.0f} 秒')
json.dump(plan, open(os.path.join(OUT, f'{label}_plan.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

# 2) 對談
for turn in range(60):
    prompt = D.build_prompt(ctx, D.talk_skill(ctx))
    if hasattr(D, '_pace_hints'): prompt += D._pace_hints(ctx)
    res = D.run_claude(prompt)
    msgs = [m for m in (res.get('messages') or []) if str(m).strip()][:3]
    if hasattr(D, '_merge_fragments'): msgs = D._merge_fragments(msgs)
    for m in msgs: ctx['conversation'].append({'role': 'assistant', 'content': clean(m)})
    say(f'{turn + 1}: 阿財 ' + ' / '.join(clean(m) for m in msgs)[:140])
    if res.get('end'): break
    ans = persona_reply(ctx['conversation'])
    ctx['conversation'].append({'role': 'candidate', 'content': ans})
    say('    人選 ' + ans[:100])
transcript = '\n'.join(f'{"阿財" if m["role"] == "assistant" else "候選人"}：{m["content"]}' for m in ctx['conversation'])
open(os.path.join(OUT, f'{label}_transcript.txt'), 'w', encoding='utf-8').write(transcript)

# 3) 統計
HALF = re.compile(r'(?<=[一-鿿])[,?:;!]|[,?:;!](?=[一-鿿])')
a = [m for m in ctx['conversation'] if m['role'] == 'assistant']
st = {'label': label, '阿財訊息數': len(a), '含半形標點': sum(1 for m in a if HALF.search(m['content'])),
      '問題訊息數': sum(1 for m in a if re.search(r'[？?]', m['content'])),
      '以好/了解/收到開頭': sum(1 for m in a if re.match(r'^(好|了解|收到)', m['content'].strip())),
      '我記下來了類': sum(1 for m in a if re.search(r'記下來|記錄下來', m['content'])),
      '起手式前5': collections.Counter(re.split(r'[，,。！!？?]', m['content'].strip())[0][:6] for m in a).most_common(5),
      '回合數': sum(1 for m in ctx['conversation'] if m['role'] == 'candidate') - 1}
# 追問有沒有發生：薪資期待依據、履歷數字
txt = [(i, m) for i, m in enumerate(ctx['conversation'])]
def followed(trigger, probe):
    for i, m in txt:
        if m['role'] == 'candidate' and re.search(trigger, m['content']):
            nxt = ' '.join(x['content'] for j, x in txt[i + 1:i + 3] if x['role'] == 'assistant')
            if re.search(probe, nxt): return True
    return False
st['薪資期待被追問依據'] = any(followed(t, r'怎麼算|怎麼抓|依據|參考|怎麼來|怎麼估|憑什麼|為什麼.{0,6}(40|獎金|10)') for t in ('40', '獎金'))
asst_text = ' '.join(m['content'] for m in a)
st['履歷數字被追問'] = bool(re.search(r'(120|58%|71%|留任率).{0,60}(怎麼|負責|自己|哪一段|如何)|(怎麼|負責|自己|哪一段).{0,60}(120|58%|71%|留任率)', asst_text))
json.dump(st, open(os.path.join(OUT, f'{label}_stats.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
say('完成 ' + json.dumps(st, ensure_ascii=False))
