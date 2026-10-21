"""推得出來的答案不要再問、只補問缺的那一半、一輪一則（2026-10-21 Jacky 實測）。不連 D1、不碰真人選。

  python3 tests/implied_answers_test.py

實際對話：
  阿財：您是住在哪個縣市呢？到台中后里上班的話，會怎麼通勤？
  人選：機車
  阿財：好，騎機車的話，那您目前是住在哪個縣市呢？到台中后里通勤大概要花多久時間？
  阿財：另外您有機車駕照嗎？        ← 騎機車上班的人當然有駕照；整題重問；第二個氣泡又加一題
"""
import importlib.util, os, sys, json
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location('daemon_ia', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec); spec.loader.exec_module(D)
D.d1 = lambda sql: []
D.d1_raw = lambda sql: {'meta': {}}

fail = 0
def check(cond, msg):
    global fail
    print(('PASS ' if cond else 'FAIL ') + msg); fail += (not cond)

JOB = {'slug': 'bim-engineer-houli', 'title': 'BIM 工程師（儲備培訓）', 'company_id': None, 'locations': '台中市后里區',
       'required_conditions': '1. 具備普通重機車駕照\n2. 會用 Revit', 'client_screen_conditions': ''}
Q1 = '您是住在哪個縣市呢？到台中后里上班的話，會怎麼通勤？'
CONV = [{'role': 'candidate', 'content': '（候選人已進入面談室）'},
        {'role': 'assistant', 'content': '您好，我是阿財，今天大概聊 15 到 25 分鐘。'},
        {'role': 'assistant', 'content': Q1},
        {'role': 'candidate', 'content': '機車'}]

def ctx(conv=CONV, job=JOB):
    return {'application': {'id': 'TEST-IA', 'name': '測試人選', 'job_title': JOB['title'], 'note': '', 'chat_token': None,
                            'created_at': '2026-10-21 10:00:00'},
            'job': job, 'conversation': conv, 'resume_readable': False, 'resume_note': '未提供'}

# 1. 推得出來的答案
facts = D._implied_facts(ctx())
check(any('機車駕照' in f for f in facts), f'騎機車通勤 → 推得有機車駕照：{facts}')
check(not D._implied_facts(ctx(conv=CONV[:3] + [{'role': 'candidate', 'content': '搭公車，我沒有機車駕照'}])),
      '搭公車、自己說沒駕照 → 不推')
check(not D._implied_facts(ctx(conv=CONV[:3] + [{'role': 'candidate', 'content': '騎腳踏車'}])), '騎腳踏車 → 不推機車駕照')
check(any('汽車駕照' in f for f in D._implied_facts(ctx(conv=CONV[:3] + [{'role': 'candidate', 'content': '我自己開車'}]))),
      '自己開車 → 推得有汽車駕照')

p = D.build_prompt(ctx(), '（規範佔位）')
line = next((l for l in p.split('\n') if '具備普通重機車駕照' in l), '')
check('由回答推得' in line and '不用再問' in line, f'必問清單那一項標成已確認：{line.strip()}')
check('推得出來的答案就算問到了' in p, 'prompt 有「推得出來就算問到」通則（同區通勤、相關科系、現職在用的軟體）')
check('只補問缺的那一半' in p, 'prompt 有「只答一半 → 只補問缺的那一半」通則')
check('一輪只發一則訊息' in p, 'prompt 有「一輪一則」規則')

# 2. 只答了一半 → 只補問缺的那一半
h = D._implied_hints(ctx())
check('您住在哪一區' in h and '不要把整題' in h, f'只答通勤方式 → 只補問住哪一區：{h.strip()[:120]}')
check('機車駕照' in h, '提示裡也講「機車駕照已推得，不要問」')
check('哪一區' not in D._implied_hints(ctx(conv=CONV[:3] + [{'role': 'candidate', 'content': '住台中豐原，騎機車'}])),
      '已經講了住哪 → 不補問')
check(D._implied_hints(ctx(conv=CONV[:2])) == '', '還沒對話 → 不加任何字')

# 3. 一輪一則；推得出來的問題被擋掉
bad = ['好，騎機車的話，那您目前是住在哪個縣市呢？到台中后里通勤大概要花多久時間？', '另外您有機車駕照嗎？']
out = D._one_message(bad, ctx())
check(len(out) == 1, f'兩個氣泡 → 一則：{out}')
check('駕照' not in out[0], '「另外您有機車駕照嗎？」被拿掉（已推得）')
out2 = D._one_message(['了解。', '您住在哪一區呢？'], ctx())
check(out2 == ['了解。\n您住在哪一區呢？'], f'一般兩則併成一則：{out2}')
check(D._one_message(['您好，我是阿財', '先請您介紹一下自己？'], ctx(conv=CONV[:1])) == ['您好，我是阿財', '先請您介紹一下自己？'],
      '開場那輪（阿財還沒講過話）不動')
check(D._one_message(['另外您有機車駕照嗎？'], ctx()) == ['另外您有機車駕照嗎？'], '只剩這一句就不刪（不能讓人選沒收到回覆）')

# 4. 報告：推得的寫「由回答推得：…」
check('由回答推得' in D.REPORT_SCHEMA_HINT if hasattr(D, 'REPORT_SCHEMA_HINT') else '由回答推得' in open(os.path.join(HERE, 'interview_daemon.py'), encoding='utf-8').read().split('"hard_conditions"')[1][:600],
      '報告的硬條件格式有「由回答推得」')

src = open(os.path.join(HERE, 'interview_daemon.py'), encoding='utf-8').read()
check('_implied_hints(ctx)' in src and '_one_message(msgs, ctx)' in src, 'handle() 有接上提示與一輪一則')

print(f'\n{"全部通過" if not fail else f"失敗 {fail} 項"}')
sys.exit(1 if fail else 0)
