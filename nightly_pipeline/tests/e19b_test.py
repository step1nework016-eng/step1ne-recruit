"""d1q.py insert 的 must_check 檢查測試：跑真的 _sourced_gate，職缺條件用 D1 裡真實的（唯讀 SELECT），不寫任何資料。"""
import os, sys, io, copy, contextlib
sys.path.insert(0, os.path.join(os.getcwd(), 'nightly_pipeline'))
import d1q
import sourcing_quality as Q

ok_all = True
def check(name, cond, detail=''):
    global ok_all; ok_all &= bool(cond)
    print(('  ✅ ' if cond else '  ❌ ') + name + (f'  ｜{detail}' if detail else ''))

SLUG = 'trainee-engineering-design-engineer'          # 10/9 就是這個職缺漏填一條
job = (d1q._post('SELECT * FROM jobs WHERE slug=?', [SLUG]).get('results') or [{}])[0]
conds = Q.job_conditions(job)
print(f'職缺 {SLUG} 的必要條件共 {len(conds)} 條：', [(c["no"], c["text"][:14], c["kind"]) for c in conds])
nolog = os.devnull
d1q.NO_PROFILE_LOG = nolog

def row(**kw):
    r = {'name': '測試甲', 'job_slug': SLUG, 'grade': 'B', 'score': 70, 'linkedin_url': 'https://www.linkedin.com/in/test-e19b',
         'source_url': 'https://www.linkedin.com/in/test-e19b', 'function_match': True, 'industry_required': False, 'industry_match': True}
    r.update(kw); return r

def full_checks(skip=()):
    return [{'no': c['no'], 'status': 'unknown' if c['kind'] == 'phone' else 'met', 'evidence': '測試'} for c in conds if c['no'] not in skip]

def gate(rows):
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            out = d1q._sourced_gate(copy.deepcopy(rows))
        return 'pass', out, buf.getvalue()
    except SystemExit as e:
        return 'exit', str(e), buf.getvalue()

print('\n[1] 自評 B、漏填一條（10/9 的情況）')
miss = conds[-1]['no']
st, out, _ = gate([row(must_check=full_checks(skip=(miss,)))])
check('直接報錯、不寫入', st == 'exit')
check('訊息列出缺的編號與條件文字', f'缺這幾條的編號：{miss}' in out and conds[-1]['text'][:10] in out, out.split('\n')[2][:60] if st == 'exit' else '')
check('訊息教它下一步（conds 指令、重新 insert）', 'd1q.py conds ' + SLUG in out and '重新 insert' in out)

print('\n[2] 逐條都填滿')
st, out, _ = gate([row(must_check=full_checks())])
check('通過、有一筆回來', st == 'pass' and len(out) == 1, f'等第 {out[0].get("grade") if st == "pass" else ""}')

print('\n[3] 完全沒附 must_check（自評 B）')
st, out, _ = gate([row()])
check('報錯並說明完全沒附，列出全部編號', st == 'exit' and '完全沒有附 must_check' in out and all(str(c['no']) in out for c in conds))

print('\n[4] status 亂寫')
bad = full_checks(); bad[0]['status'] = 'ok'
st, out, _ = gate([row(must_check=bad)])
check('報錯並指出編號與寫錯的值', st == 'exit' and f"編號 {conds[0]['no']} 的 status 寫成「ok」" in out)
bad = full_checks(); bad[0]['status'] = 'MET'
st, out, _ = gate([row(must_check=bad)])
check('大小寫不同（MET）視為合法（不為難模型）', st == 'pass')

print('\n[5] 自評 C／D 不查')
st, out, _ = gate([row(grade='C', score=50)])
check('自評 C、沒附 must_check → 照舊通過（會被當 C 寫入）', st == 'pass' and out[0]['grade'] == 'C')
st, out, _ = gate([row(grade='', score=0)])
check('沒給等第也沒分數（視為 D）→ 通過', st == 'pass')
st, out, _ = gate([row(grade='', score=75)])
check('沒給等第、分數 75（視為 B）→ 缺條件就報錯', st == 'exit')

print('\n[6] 一批多筆：有一筆有問題就整批不寫，且一次報完')
st, out, _ = gate([row(name='乙', must_check=full_checks()), row(name='丙', must_check=full_checks(skip=(miss,))),
                   row(name='丁', must_check=None)])
check('整批報錯（不是只擋壞的那筆）', st == 'exit')
check('丙、丁都被列出，乙（填滿的）沒被列出', '「丙」' in out and '「丁」' in out and '「乙」' not in out)

print('\n[7] 沒有個人頁的不算 must_check 問題（照舊只記線索）')
r = row(name='戊'); r.pop('linkedin_url'); r['source_url'] = 'https://news.example.com/a'
st, out, printed = gate([r])
check('照舊「不寫入、沒個人頁」，不是 must_check 報錯', st == 'pass' and out == [] and '沒有個人頁' in printed)

print('\n[8] 職缺完全沒有條件、沒有職稱／主要工作（conds 為空）→ 不查')
real_post = d1q._post
d1q._post = lambda sql, params=None, **k: {'results': [{'slug': 'x'}]} if 'FROM jobs' in sql else real_post(sql, params)
check('這種職缺 conds 確實是空的', Q.job_conditions({'slug': 'x'}) == [])
st, out, _ = gate([row(job_slug='x-nocond')])
check('conds 為空 → 通過', st == 'pass')

print('\n[9] 職缺沒填必要條件、只有職稱（程式會自動補 1 條「實際做過核心工作」）→ 這 1 條也要填')
d1q._post = lambda sql, params=None, **k: {'results': [{'slug': 'y', 'title': '某職缺', 'main_duties': '做事'}]} if 'FROM jobs' in sql else real_post(sql, params)
check('自動補出 1 條 core 條件', len(Q.job_conditions({'title': '某職缺', 'main_duties': '做事'})) == 1)
st, out, _ = gate([row(job_slug='y-core')])
check('自評 B、沒附 must_check → 報錯，列出編號 1', st == 'exit' and '缺這幾條的編號：1' in out)
st, out, _ = gate([row(job_slug='y-core', must_check=[{'no': 1, 'status': 'met', 'evidence': '做過'}])])
check('填了編號 1 → 通過', st == 'pass')
d1q._post = real_post

print('\n全部通過' if ok_all else '\n有失敗'); sys.exit(0 if ok_all else 1)
