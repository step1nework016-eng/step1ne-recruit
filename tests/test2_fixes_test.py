"""2026-10-21 Jacky 測試2（377ba9c1）三個問題＋HTML 感謝信。不連 D1、不寄信、不碰真人選。

  python3 tests/test2_fixes_test.py

1. 感謝信：paused（中途離開、房間留著）不寄；真的關房（正常收尾／close_paused）才寄；已寄過 → 推薦職缺另寄短信
2. 提早喊停／逾時收尾：快速應徵給 /resume/?t= 上傳連結，不叫他寄 official@
3. 表單填過的到職時間：阿財看得到（中文標籤）、必問清單標成已知；「最快什麼時候可以到職」算問過
"""
import importlib.util, os, sys, re
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location('daemon_t2', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec); spec.loader.exec_module(D)
import interview_thanks_mail as ITM

fail = 0
def check(cond, msg):
    global fail
    print(('PASS ' if cond else 'FAIL ') + msg); fail += (not cond)

SQL = []
DB = {}
def fake_d1(sql):
    SQL.append(sql)
    for k, v in DB.items():
        if k in sql:
            return v
    return []
D.d1 = fake_d1
D.d1_raw = lambda sql: {'meta': {}}
D.log = lambda *a, **k: None
D.funnel_event = lambda *a, **k: None

TOK = 'tok123'
JOB = {'slug': 'bim-engineer-houli', 'title': 'BIM 工程師(儲備培訓)', 'company_id': None, 'locations': '台中市后里區',
       'required_conditions': '', 'client_screen_conditions': ''}
NOTE = ('【快速應徵（測試版）】\n相關經驗：還沒有\n會用的軟體：都還沒用過\n相關證照：都沒有\n機車駕照：有\n'
        '最高學歷：博士\n學校：台大\n科系：土木\n期望工作城市：桃園市\n履歷：還沒附（之後補）')

def app(**kw):
    a = {'id': 'T2', 'name': '測試2', 'job_title': JOB['title'], 'note': NOTE, 'origin': 'quick_apply',
         'chat_token': TOK, 'available_date': '馬上', 'location_ok': '桃園市', 'expected_salary': '依職缺薪資',
         'created_at': '2026-10-21 20:12:58', 'interview_started_at': '2026-10-21 20:13:06'}
    a.update(kw); return a

def ctx(a=None, conv=None):
    return {'application': a or app(), 'job': JOB, 'conversation': conv or [
        {'role': 'candidate', 'content': '（候選人已進入面談室）'},
        {'role': 'assistant', 'content': '您好，我是阿財。'}], 'resume_readable': False, 'resume_note': '未提供'}

# ── 3. 到職時間 ──
p = D.build_prompt(ctx(), '（規範佔位）')
check('最快可到職（表單填的）：馬上' in p, '表單的到職時間用中文標籤給阿財看')
check('期望工作地點（表單填的）：桃園市' in p, '期望工作城市給阿財看')
for f in ('最高學歷：博士', '學校：台大', '科系：土木', '相關證照：都沒有', '期望工作城市：桃園市', '機車駕照：有'):
    check(f in p, f'快速應徵欄位看得到：{f}')
line = next((l for l in p.split('\n') if '最快到職日' in l), '')
check('表單已填：馬上' in line and '不用再問' in line, f'必問清單「最快到職日」標成已知：{line.strip()[:80]}')
p2 = D.build_prompt(ctx(app(available_date='再看看')), '（規範佔位）')
line2 = next((l for l in p2.split('\n') if '最快到職日' in l), '')
check('再看看' in line2 and '問一次' in line2, f'表單選「再看看」→ 只准確認一次：{line2.strip()[:80]}')
p3 = D.build_prompt(ctx(app(available_date='')), '（規範佔位）')
line3 = next((l for l in p3.split('\n') if '最快到職日' in l), '')
check('表單已填' not in line3 and '再看看' not in line3, '沒填到職時間 → 照常要問')
check('預告期／到職時間' in D._topics_of('如果錄取的話，最快什麼時候可以到職呢？'), '「最快什麼時候可以到職」算問過到職時間')
check('預告期／到職時間' in D._topics_of('那您什麼時候可以上班？'), '「什麼時候可以上班」算問過到職時間')

# ── 2. 收尾給上傳連結 ──
up = f'https://step1ne.com/resume/?t={TOK}'
check('一律給這個上傳連結' in p and up in p, '快速應徵沒履歷：prompt 一律給 /resume/?t= 連結（含喊停、時間到）')
xline = next((l for l in p.split('\n') if l.startswith('※')), '')
check('不要叫他寄 Email' in xline, f'快速應徵的 ※ 行不再叫他寄 official@：{xline[:60]}')
pn = D.build_prompt(ctx(app(origin='', note='', resume_url='https://drive.example/cv')), '（規範佔位）')
xn = next((l for l in pn.split('\n') if l.startswith('※')), '')
check('不要叫他再寄履歷' in xn, '原本流程、已有履歷：不叫他再寄 official@')
pn2 = D.build_prompt(ctx(app(origin='', note='')), '（規範佔位）')
xn2 = next((l for l in pn2.split('\n') if l.startswith('※')), '')
check('official@step1ne.com' in xn2 and '/resume/?t=' not in xn2, '原本流程、沒履歷：維持原本信箱／LINE 說法')
DB.clear(); DB['SELECT id, origin, note, chat_token, resume_file_id'] = [app()]
check(up in (D._quick_resume_bye_line('T2') or ''), '逾時／時間到的道別訊息附上傳連結')
DB.clear(); DB['SELECT id, origin, note, chat_token, resume_file_id'] = [app(resume_url='x')]
check(D._quick_resume_bye_line('T2') is None, '已附履歷 → 道別訊息不加連結')

# ── 1. 感謝信時機 ──
src = open(os.path.join(HERE, 'interview_daemon.py'), encoding='utf-8').read()
fin = src[src.index('def finish('):src.index('prompt = build_report_prompt(', src.index('def finish('))]
check('if close and _is_q:' in fin, 'finish()：快速應徵只有 close=True（真的關房）才寄感謝信，paused 不寄')
cp = src[src.index('def close_paused('):src.index('def held_too_long(')]
check('invite_assessment_after_interview' in cp, 'close_paused()：保留期滿真的關房時才寄')

REC = [{'title': '工程設計工程師(儲備培訓)', 'location': '桃竹苗地區', 'salary': '依資歷核薪', 'employment': '正職',
        'why': '土木背景接近', 'url': 'https://step1ne.com/jobs/x/', 'evidence': '人選原話不外流'}]
A_ROW = {'id': 'T2', 'name': '測試2', 'email': 'test@example.com', 'job_title': JOB['title'], 'job_slug': JOB['slug'],
         'chat_token': TOK, 'note': NOTE, 'origin': 'quick_apply', 'has_resume': 0}
DB.clear(); SQL.clear()
DB['FROM applications WHERE id='] = [A_ROW]
D.invite_assessment_after_interview('T2', recs=REC)
ins = [s for s in SQL if s.startswith('INSERT INTO mail_outbox')]
check(len(ins) == 1 and "'assessment_invite'" in ins[0] and '<!doctype html>' in ins[0], '第一次：排一封 HTML 感謝信（含 html 欄）')
check('人選原話不外流' not in ins[0], '推薦職缺的 evidence（人選原話）不放進信')
DB.clear(); SQL.clear()
DB['FROM applications WHERE id='] = [A_ROW]
DB["kind='assessment_invite'"] = [{'1': 1}]
D.invite_assessment_after_interview('T2', recs=REC)
ins = [s for s in SQL if s.startswith('INSERT INTO mail_outbox')]
check(len(ins) == 1 and "'acai_job_recs'" in ins[0], '感謝信已寄過、這次才有推薦 → 推薦職缺另寄一封')

# ── HTML 信本體 ──
s, t, h = ITM.build(name='測試2', job_title='BIM', token=TOK, has_resume=False, show_assessment=True, recs=REC)
check(up in h and up in t and f'assessment/?t={TOK}&amp;after=1' in h, '快速版：上傳履歷＋測驗連結（HTML 與純文字都有）')
check('您可能也適合這些職缺' in h and 'acai-avatar-192.png' in h and 'max-width:600px' in h, '推薦區塊、PNG 頭像、600px')
s, t, h = ITM.build(name='測試2', job_title='BIM', token=TOK, has_resume=True, show_assessment=False, recs=[])
check('✓ 履歷已收到' in h and '/resume/' not in h and '工作風格測驗' not in h, '原始版：履歷已收到、沒按鈕、沒測驗')
check('您可能也適合' not in h and '您可能也適合' not in t, '沒推薦 → 整塊不出現')
check(all('客戶' not in x for x in (s, t, h)), '信裡沒有「客戶」兩字')

print('\n全部通過' if not fail else f'\n{fail} 項失敗'); sys.exit(1 if fail else 0)
