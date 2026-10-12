#!/usr/bin/env python3
"""E27b：API 額度保護、每人費用、單價統一（假 D1／假 TG／假匯率，不花錢）"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import acai_budget as B
import claude_api as A
import acai_token_report as R

bad = 0
def chk(n, c, d=''):
    global bad
    print(('✅' if c else '❌'), n, ('' if c else f'← {d}')); bad += (not c)

tmp = tempfile.mkdtemp()
B.CONFIG_FILE = os.path.join(tmp, 'b.json'); B.STATE_FILE = os.path.join(tmp, 's.json'); B.MODE_FILE = os.path.join(tmp, 'mode')
sent, modes = [], []
dm = lambda t: sent.append(t) or True
setm = lambda m: modes.append(m)
def d1_of(v):
    return lambda sql: [{'usd': v}]

# 單價統一
chk('單價 sonnet-5 = 2/10/4/0.2', A.price_for('claude-sonnet-5')[0] == (2.0, 10.0, 4.0, 0.2) and A.price_for('claude-sonnet-5-5')[1])
chk('未知模型標未確認', A.price_for('claude-foo')[1] is False)
chk('費用：1000 輸入＋200 輸出 = $0.004', abs(A.cost_usd({'input_tokens': 1000, 'output_tokens': 200}, 'claude-sonnet-5') - 0.004) < 1e-9)
chk('報表 PRICE 與 claude_api 同一份', R.PRICE == {'input': 2.0, 'output': 10.0, 'cache_write_1h': 4.0, 'cache_read': 0.2})

# 預算線
def run(v, **kw):
    return B.check(d1fn=d1_of(v), dm_fn=dm, mode_fn=setm, force=True)
chk('$5：沒動作', run(5.0) == [] and not sent)
a = run(10.5)
chk('$10.5：發 $10 提醒一次', a == [('warn', 10.0)] and len(sent) == 1 and '10' in sent[0], (a, sent))
chk('$10.9 再查：不重發', run(10.9) == [] and len(sent) == 1)
a = run(16.2)
chk('$16.2：發 $16 提醒', a == [('warn', 16.0)] and len(sent) == 2)
a = run(19.3)
chk('$19.3：切回 cli 並通知（一次）', ('cutoff', 19.3) in a and modes == ['cli'] and len(sent) == 3, (a, modes, len(sent)))
chk('切換後再查：不重複通知', run(19.5) == [] and len(sent) == 3 and modes == ['cli'])
chk('通知文字不含金鑰樣式', all('sk-ant' not in t for t in sent))

# 儲值後改預算 → 重新武裝
json.dump({'budget_usd': 40, 'warn_usd': [20, 32], 'cutoff_usd': 39, 'since': '2026-10-13 00:00:00'}, open(B.CONFIG_FILE, 'w'))
sent.clear(); modes.clear()
a = run(21.0)
chk('改預算後狀態重置：新提醒線生效', a == [('warn', 20.0)] and len(sent) == 1, (a, sent))
chk('限制 since 時 SQL 帶起算點', 'created_at >= ' in (lambda: (lambda box: (B.spent_usd(B.load_config(), lambda sql: box.append(sql) or [{'usd': 0}]), box)[1])([]))()[0])

# 額度不足
os.remove(B.CONFIG_FILE) if os.path.exists(B.CONFIG_FILE) else None
sent.clear(); modes.clear()
r1 = B.on_credit_exhausted('額度', dm_fn=dm, mode_fn=setm, today='2026-10-13')
r2 = B.on_credit_exhausted('額度', dm_fn=dm, mode_fn=setm, today='2026-10-13')
r3 = B.on_credit_exhausted('額度', dm_fn=dm, mode_fn=setm, today='2026-10-14')
chk('額度不足：同一天只發一次 TG', r1 and not r2 and r3 and len(sent) == 2, (r1, r2, r3, len(sent)))
chk('額度不足：每次都把模式寫回 cli', modes == ['cli', 'cli', 'cli'], modes)
chk('TG 文字提到儲值與回我「阿財改回 API」', '儲值' in sent[0] and '阿財改回 API' in sent[0])

# claude_api 掛鉤
got = []
A.on_credit_exhausted = lambda reason: got.append(reason)
import io, urllib.error
A.ENV_FILE = os.path.join(tmp, 'a.env'); open(A.ENV_FILE, 'w').write('ANTHROPIC_API_KEY=sk-ant-TESTKEY1234567890abcdefghij\n')
def op(req, timeout=0): raise urllib.error.HTTPError('u', 400, 'x', {}, io.BytesIO(b'{"error":{"message":"credit balance is too low"}}'))
try: A.call('x', 'm', 5, opener=op)
except A.ApiFallback: pass
chk('claude_api 偵測額度不足會呼叫回呼', len(got) == 1 and '額度' in got[0], got)

# 報表：每人費用與台幣
def row(app, name, job, src, usd, calls=1, ts='2026-10-13 10:00:00', model='claude-sonnet-5'):
    return {'application_id': app, 'name': name, 'job_title': job, 'source': src, 'cost_usd': usd, 'calls': calls,
            'input_tokens': 1000, 'output_tokens': 200, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 500,
            'created_at': ts, 'model': model}
data = [row('a1', '甲', '財務長', 'api', 0.30), row('a1', '甲', '財務長', 'api', 0.20, calls=2), row('a2', '乙', 'IT 主管', 'cli_json', 1.5)]
ppl = R.build_people(data)
jia = [p for p in ppl if p['name'] == '甲'][0]
chk('每人彙總：甲 呼叫 3 次 $0.50', jia['calls'] == 3 and abs(jia['usd'] - 0.5) < 1e-9, ppl)
fx = {'rate': 32.1234, 'source': 'open.er-api.com', 'at': 'Mon, 13 Oct 2026 00:02:31 +0000'}
txt = R.format_people(ppl, fx)
chk('報表印出匯率、來源與時間', '32.1234' in txt and 'open.er-api.com' in txt and '2026' in txt)
chk('台幣 = 美元 × 匯率', 'NT$16' in txt, txt)    # 0.5×32.1234 = 16.06
txt2 = R.format_people(ppl, None)
chk('匯率抓不到：只給美元並註明', '匯率未取得' in txt2 and 'NT$（匯率未取得）' in txt2)
chk('fetch_fx 失敗回 None（不寫死）', R.fetch_fx(opener=lambda *a, **k: (_ for _ in ()).throw(OSError('x'))) is None)
class FakeResp:
    def __init__(s, b): s.b = b
    def __enter__(s): return s
    def __exit__(s, *a): pass
    def read(s): return s.b
okfx = R.fetch_fx(opener=lambda *a, **k: FakeResp(json.dumps({'result': 'success', 'time_last_update_utc': 'T', 'rates': {'TWD': 31.5}}).encode()))
chk('fetch_fx 成功解析', okfx and okfx['rate'] == 31.5 and okfx['source'] == 'open.er-api.com')
chk('未知模型的 api 列標「單價未確認」', '單價未確認' in R.format_people(R.build_people([row('z', '丙', 'X', 'api', 0.1, model='claude-foo')]), fx))

# 每日推播
api_today = R.build_people([r for r in data if r['source'] == 'api'])
msg = R.daily_text(api_today, fx, 3.2, 20.0, '2026-10-13')
chk('每日推播：每人一行含姓名｜職缺｜US$｜NT$', '甲｜財務長｜US$0.50｜NT$16' in msg, msg)
chk('每日推播：含今日合計、累計、剩餘預算、匯率', '今日合計' in msg and '累計 US$3.20 / 預算 US$20' in msg and '剩約 US$16.80' in msg and '匯率' in msg)
chk('沒有 API 花費的日子不推', R.daily_text([], fx, 0, 20, '2026-10-13') is None)

# 恢復
B.CONFIG_FILE = os.path.join(tmp, 'b2.json')
B.resume_api('40')
chk('--resume-api --budget 40：模式 api、預算線重設', open(B.MODE_FILE).read().strip() == 'api' and B.load_config()['budget_usd'] == 40.0 and B.load_config()['cutoff_usd'] == 39.0 and B.load_config()['since'])

print(f'\n失敗 {bad} 項'); sys.exit(1 if bad else 0)
