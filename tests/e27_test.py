#!/usr/bin/env python3
"""E27：claude_api 與 run_claude 的 API／CLI 切換（假 HTTP，不花錢、不碰金鑰）"""
import io, json, os, sys, tempfile, time, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claude_api as A

bad = 0
def chk(n, c, d=''):
    global bad
    print(('✅' if c else '❌'), n, ('' if c else f'← {d}')); bad += (not c)

tmp = tempfile.mkdtemp()
A.ENV_FILE = os.path.join(tmp, 'a.env'); A.MODE_FILE = os.path.join(tmp, 'mode')
open(A.ENV_FILE, 'w').write('ANTHROPIC_API_KEY=sk-ant-TESTKEY1234567890abcdefghij\nANTHROPIC_WORKSPACE_ID=wrkspc_test\n')

class Resp:
    def __init__(s, b): s.b = b
    def __enter__(s): return s
    def __exit__(s, *a): pass
    def read(s): return s.b

seen = {}
def ok_opener(req, timeout=0):
    seen['h'] = {k.lower(): v for k, v in req.header_items()}
    seen['body'] = json.loads(req.data)
    return Resp(json.dumps({'model': 'claude-sonnet-5', 'stop_reason': 'end_turn',
        'content': [{'type': 'text', 'text': '{"reply":"嗨"}'}],
        'usage': {'input_tokens': 1000, 'output_tokens': 200, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0}}).encode())

def http_err(code, body):
    def op(req, timeout=0):
        raise urllib.error.HTTPError('u', code, 'x', {}, io.BytesIO(body.encode()))
    return op

# 模式切換
os.environ.pop('ACAI_LLM', None)
chk('預設走 CLI', A.mode() == 'cli' and not A.use_api())
open(A.MODE_FILE, 'w').write('api\n')
chk('檔案設 api → 走 API', A.mode() == 'api' and A.use_api())
os.environ['ACAI_LLM'] = 'cli'
chk('環境變數 cli 優先於檔案', A.mode() == 'cli')
os.environ.pop('ACAI_LLM')

# 成功
r = A.call('你好', 'claude-sonnet-5', 30, opener=ok_opener)
chk('成功：文字與 usage', r['text'] == '{"reply":"嗨"}' and r['usage']['input_tokens'] == 1000)
chk('成功：費用 = 1000×2 + 200×10 = $0.004', abs(r["cost_usd"] - 0.004) < 1e-9, r['cost_usd'])
chk('標頭帶金鑰、版本、工作區', seen['h'].get('x-api-key', '').startswith('sk-ant-') and seen['h'].get('anthropic-version') and seen['h'].get('anthropic-workspace-id') == 'wrkspc_test')
chk('請求內容正確', seen['body']['messages'][0]['content'] == '你好' and seen['body']['model'] == 'claude-sonnet-5')

# 錯誤分類
def expect_fallback(name, op, cool):
    A._down_until = 0
    try:
        A.call('x', 'm', 5, opener=op); chk(name, False, '沒丟例外')
    except A.ApiFallback as e:
        chk(name + '（錯誤訊息不含金鑰）', 'TESTKEY' not in str(e) and 'sk-ant-TEST' not in str(e))
        chk(name + (' → 進入冷卻' if cool else ' → 不冷卻'), (A.cooldown_info()[1] > 0) == cool, A.cooldown_info())
expect_fallback('額度不足', http_err(400, '{"error":{"message":"Your credit balance is too low sk-ant-TESTKEY1234567890abcdefghij"}}'), True)
chk('額度不足：冷卻中 use_api() 為 False', (open(A.MODE_FILE, 'w').write('api') or True) and not A.use_api())
expect_fallback('金鑰被拒 401', http_err(401, '{"error":{"message":"invalid x-api-key"}}'), True)
expect_fallback('沒綁工作區', http_err(400, '{"error":{"message":"not scoped to a workspace"}}'), True)
expect_fallback('模型不存在 404', http_err(404, '{"error":{"message":"model: x"}}'), True)
expect_fallback('暫時性 529', http_err(529, 'overloaded'), False)
expect_fallback('連不上', lambda req, timeout=0: (_ for _ in ()).throw(urllib.error.URLError('dns fail')), False)
A._down_until = 0
try:
    A.call('x', 'm', 5, opener=lambda req, timeout=0: (_ for _ in ()).throw(TimeoutError('t')))
    chk('逾時 → ApiTimeout', False)
except A.ApiTimeout:
    chk('逾時 → ApiTimeout（不冷卻）', A.cooldown_info()[1] == 0)
os.remove(A.ENV_FILE)
A._down_until = 0
try:
    A.call('x', 'm', 5, opener=ok_opener); chk('沒有金鑰檔 → ApiFallback', False)
except A.ApiFallback:
    chk('沒有金鑰檔 → ApiFallback', True)

# run_claude 整合
A._down_until = 0
import interview_daemon as D
D.log = lambda *a, **k: None
calls = {'api': 0, 'cli': 0}
def fake_api(prompt, model, timeout, max_tokens=0, opener=None):
    calls['api'] += 1
    return {'text': '{"reply":"API"}', 'usage': {'input_tokens': 10, 'output_tokens': 5}, 'model': 'm', 'cost_usd': 0.0001, 'ms': 800}
class R:  # 假 subprocess 結果
    returncode = 0; stderr = ''
    stdout = json.dumps({'result': '{"reply":"CLI"}', 'usage': {}, 'total_cost_usd': 0})
def fake_run(*a, **k):
    calls['cli'] += 1; return R()
D.subprocess.run = fake_run
D.claude_api.call = fake_api
D.claude_api.use_api = lambda: True
out = D.run_claude('p')
chk('run_claude：API 成功就不跑 CLI', out == {'reply': 'API'} and calls == {'api': 1, 'cli': 0}, (out, calls))
meta = getattr(D._CALL_META, 'meta', None)
chk('用量 meta 標 source=api 且記到 cost', meta and meta.get('source') == 'api' and abs(meta['cost_usd'] - 0.0001) < 1e-9, meta)
D._CALL_META.meta = None
def api_down(*a, **k): raise A.ApiFallback('額度不足')
D.claude_api.call = api_down
calls.update(api=0, cli=0)
out = D.run_claude('p')
chk('run_claude：API 不可用 → 同一輪改走 CLI 且正常回覆', out == {'reply': 'CLI'} and calls['cli'] == 1, (out, calls))
D._CALL_META.meta = None
def api_to(*a, **k): raise A.ApiTimeout('t')
D.claude_api.call = api_to
calls.update(api=0, cli=0)
try:
    D.run_claude('p', first_try=1, total=3)
    chk('run_claude：API 逾時反覆 → 放棄', False)
except RuntimeError:
    chk('run_claude：API 逾時反覆 → 放棄（跟 CLI 逾時同一種處理）', True)
D.claude_api.use_api = lambda: False
calls.update(api=0, cli=0)
out = D.run_claude('p')
chk('run_claude：模式 cli → 完全走舊路徑', out == {'reply': 'CLI'} and calls == {'api': 0, 'cli': 1}, (out, calls))
print(f'\n失敗 {bad} 項'); sys.exit(1 if bad else 0)
