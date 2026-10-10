"""call_audio 測試（E21）：全部假的 TG／假轉文字／假確認卡，不下載模型、不連正式 D1、不用真錄音。
在 repo 根目錄跑：python3 tests/call_audio_test.py"""
import io, json, os, sys, time, tempfile, urllib.error
sys.path.insert(0, os.getcwd())
os.environ.setdefault('INTERVIEW_HOST', 'test')
import call_audio as C

fails = []
def check(name, cond, detail=''):
    print(('  ✅ ' if cond else '  ❌ ') + name + (f'  ｜{detail}' if detail else ''))
    if not cond: fails.append(name)

C.log = lambda m: None

print('\n[A] 專有名詞提示')
RESUME = '''林大明｜財務經理
商周集團｜財務經理｜2018-2022：負責導入鼎新 T100，維護 BOM 表，帶 7 人團隊
宣捷生技股份有限公司 成本會計 2022-2025，使用 SAP、Laravel 報表；證照 CPA
the quick brown fox 負責 sales and marketing support'''
terms = C.extract_terms(RESUME)
check('抓到公司名（商周集團、宣捷生技股份有限公司）', '商周集團' in terms and '宣捷生技股份有限公司' in terms, terms)
check('抓到英數縮寫（T100、BOM、SAP、Laravel、CPA）', all(t in terms for t in ('T100', 'BOM', 'SAP', 'Laravel', 'CPA')), terms)
check('抓到職稱（財務經理）', '財務經理' in terms)
check('一般英文小寫單字不當專有名詞（the、quick、and…）', not any(t in terms for t in ('the', 'quick', 'brown', 'and', 'sales')), terms)
p = C.build_prompt(terms)
check('提示詞不超過上限、以公司名開頭排序', len(p) <= C.PROMPT_MAX_CHARS + 40 and '商周集團' in p, f'{len(p)} 字：{p[:60]}')
check('沒有專有名詞 → 空字串', C.build_prompt([]) == '')
check('極長清單被截斷', len(C.build_prompt(['公司名稱很長的科技股份有限公司' + str(i) for i in range(100)])) <= C.PROMPT_MAX_CHARS + 40)

print('\n[B] 人選解析')
seen = []
def fake_d1_factory(rows):
    def d1(sql): seen.append(sql); return rows
    return d1
check('名字 → 查 LIKE，單引號會跳脫', (C.resolve_candidates("O'Brien", fake_d1_factory([])) == [] and "O''Brien" in seen[-1]), seen[-1][-120:])
check('編號 → 精準查', (C.resolve_candidates('dd3cc10b-3f36-450f-b491-6a7713cec47f', fake_d1_factory([{'id': 'x'}])) == [{'id': 'x'}] and "id='dd3cc10b" in seen[-1]))

print('\n[C] run()：流程與清檔')
def mk_job(**o):
    p = {'file_id': 'FID', 'file_size': 1000000, 'caption': '林大明', 'chat_id': '-1003', 'thread_id': 77,
         'from': {'id': 5, 'username': 'jackyyuqi', 'name': 'Jacky'}}
    p.update(o)
    return {'id': 'job1', 'kind': 'call_audio', 'payload_json': json.dumps(p)}
class Rec:
    def __init__(self): self.say = []; self.cards = []; self.dirs = []
R = Rec()
def notify_fn(chat, th, text): R.say.append((chat, th, text)); return True
def download_ok(fid, d): R.dirs.append(d); p = os.path.join(d, 'rec.m4a'); open(p, 'wb').write(b'x' * 100); return p
LONG_TEXT = '您之前在商周集团负责导入鼎新T100系统，对吗？' * 5
def transcribe_ok(path, terms): return {'text': LONG_TEXT, 'duration': 600, 'secs': 50}
def card_ok(app, text, payload, work): R.cards.append((app, text, payload, os.path.exists(work))); return 'ok'
app1 = [{'id': 'A1', 'name': '林大明', 'job_slug': 'jb', 'job_title': '財務經理', 'created_at': '2026-10-01'}]
idle_calls = []
def run(job, d1_rows=app1, **kw):
    R.say.clear(); R.cards.clear(); R.dirs.clear()
    args = dict(d1=lambda sql: d1_rows, download=download_ok, transcribe_fn=transcribe_ok, card_fn=card_ok, notify_fn=notify_fn,
                context_fn=lambda aid, d1: ['商周集團', 'T100'], idle_fn=lambda d1: idle_calls.append(1))
    args.update(kw)
    return C.run(job, **args)
out = run(mk_job())
check('成功：貼確認卡一次，內容有標頭＋繁體轉換', len(R.cards) == 1 and R.cards[0][1].startswith('【電話錄音轉文字') and '商周集團' in R.cards[0][1] and '导入' not in R.cards[0][1], R.cards[0][1][:60] if R.cards else '')
check('標頭提醒：AI 辨識要核對、未分講話者、錄音約 10 分鐘', '核對' in R.cards[0][1] and '未分講話者' in R.cards[0][1] and '10 分鐘' in R.cards[0][1])
check('確認卡拿到的是對的人選與誰傳的', R.cards[0][0]['id'] == 'A1' and R.cards[0][2]['from']['name'] == 'Jacky')
check('成功後暫存資料夾（錄音檔）已刪掉', R.dirs and not os.path.exists(R.dirs[0]))
check('成功時回短字串（不含逐字稿）', '已貼確認卡' in out and LONG_TEXT[:10] not in out, out)
check('有先等阿財空檔（idle_fn 被呼叫）', len(idle_calls) >= 1)

out = run(mk_job(file_size=25 * 1048576))
check('payload 標示 >20MB → 不下載、請改傳較短', not R.dirs and R.say and '20MB' in R.say[0][2] and not R.cards)
out = run(mk_job(), d1_rows=[])
check('查無人選 → 通知、不下載', not R.dirs and R.say and '查不到' in R.say[0][2] and not R.cards)
two = app1 + [{'id': 'A2', 'name': '林大明', 'job_slug': 'jb2', 'job_title': '會計', 'created_at': '2026-10-05'}]
out = run(mk_job(), d1_rows=two)
check('同名多筆 → 列清單請 Jacky 指定、不下載', not R.dirs and R.say and 'A1' in R.say[0][2] and 'A2' in R.say[0][2] and not R.cards)
def transcribe_bad(path, terms): raise C.CallAudioError('轉文字程式失敗：模型沒下載')
out = run(mk_job(), transcribe_fn=transcribe_bad)
check('轉文字失敗 → 通知 Jacky、暫存已刪、不貼卡', R.say and '沒成功' in R.say[0][2] and R.dirs and not os.path.exists(R.dirs[0]) and not R.cards)
def download_retry(fid, d): R.dirs.append(d); raise C.RetryLater('連 Telegram 失敗')
try:
    run(mk_job(), download=download_retry); raised = False
except C.RetryLater:
    raised = True
check('暫時性網路失敗 → 例外往上拋讓 ai_worker 重試，暫存也刪掉', raised and R.dirs and not os.path.exists(R.dirs[0]))
out = run(mk_job(), transcribe_fn=lambda p, t: {'text': '嗯', 'duration': 3})
check('轉出來幾乎沒字 → 通知、不貼卡', R.say and '太少' in R.say[0][2] and not R.cards)
check('沒有 chat_id 時不通知也不當掉', run(mk_job(chat_id=None, file_size=25 * 1048576)) == '檔案過大，已請對方改傳')

print('\n[D] 等阿財空檔')
state = {'n': 0}
def d1_active(sql): state['n'] += 1; return [{'n': 1 if state['n'] < 3 else 0}]
slept = []
C.wait_idle(d1_active, sleep=slept.append)
check('面談進行中 → 睡 30 秒再看，結束就繼續', len(slept) == 2 and all(s == 30 for s in slept), slept)
t_real = time.time
_clock = {'t': 0.0}
def fake_time():
    _clock['t'] += 400.0                          # 每看一次時鐘就過 400 秒 → 很快超過 600 秒上限
    return _clock['t']
C.time.time = fake_time
try:
    C.wait_idle(lambda sql: [{'n': 1}], sleep=lambda s: None, max_wait=600); r2 = False
except C.RetryLater:
    r2 = True
C.time.time = t_real
check('等太久 → RetryLater（不硬做）', r2)

print('\n[E] TG 下載（假 urlopen）')
class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
C._tg_token = lambda: 'SECRET-TOKEN-123'
urls = []
def mk_urlopen(handlers):
    def u(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        urls.append(url)
        for key, h in handlers.items():
            if key in url:
                return h(url)
        raise AssertionError(url)
    return u
real_urlopen = C.urllib.request.urlopen
tmp = tempfile.mkdtemp()
C.urllib.request.urlopen = mk_urlopen({'getFile': lambda u: FakeResp(json.dumps({'ok': True, 'result': {'file_path': 'voice/a.oga', 'file_size': 1234}}).encode()),
                                       '/file/bot': lambda u: FakeResp(b'AUDIO' * 100)})
path = C.tg_download('FID 1', tmp)
check('下載成功：副檔名保留、內容寫進去、file_id 有編碼', path.endswith('.oga') and open(path, 'rb').read() == b'AUDIO' * 100 and 'FID%201' in urls[0], path)
C.urllib.request.urlopen = mk_urlopen({'getFile': lambda u: FakeResp(json.dumps({'ok': True, 'result': {'file_path': 'a.m4a', 'file_size': 25 * 1048576}}).encode())})
try:
    C.tg_download('BIG', tmp); e1 = ''
except C.CallAudioError as e:
    e1 = str(e)
check('getFile 回報 >20MB → CallAudioError', '20MB' in e1, e1)
def raise_http(u): raise urllib.error.HTTPError(u, 400, 'Bad Request: file is too big', {}, None)
C.urllib.request.urlopen = mk_urlopen({'getFile': raise_http})
try:
    C.tg_download('X', tmp); e2 = ''
except C.CallAudioError as e:
    e2 = str(e)
check('HTTP 400（檔案太大）→ 中文說明，且訊息裡沒有金鑰', '20MB' in e2 and 'SECRET-TOKEN' not in e2, e2)
def raise_net(u): raise urllib.error.URLError('dns fail')
C.urllib.request.urlopen = mk_urlopen({'getFile': raise_net})
try:
    C.tg_download('X', tmp); e3 = None
except C.RetryLater as e:
    e3 = str(e)
check('網路錯誤 → RetryLater（訊息沒有金鑰）', e3 is not None and 'SECRET-TOKEN' not in e3, e3)
# 下載內容超過 20MB（getFile 沒給 size）
C.urllib.request.urlopen = mk_urlopen({'getFile': lambda u: FakeResp(json.dumps({'ok': True, 'result': {'file_path': 'a.m4a'}}).encode()),
                                       '/file/bot': lambda u: FakeResp(b'0' * (21 * 1048576))})
try:
    C.tg_download('X', tmp); e4 = ''
except C.CallAudioError as e:
    e4 = str(e)
check('實際下載超過 20MB 也擋（防 getFile 沒回大小）', '20MB' in e4)
C.urllib.request.urlopen = real_urlopen

print('\n[F] 確認卡呼叫')
calls = []
class P: returncode = 0; stdout = '✅ 已貼出確認卡（abc）'; stderr = ''
real_run = C.subprocess.run
C.subprocess.run = lambda cmd, **kw: (calls.append((cmd, kw)), P())[1]
wd = tempfile.mkdtemp()
out = C.post_card({'id': 'A1'}, '【標頭】\n逐字稿內容', {'chat_id': '-1003', 'thread_id': 77, 'from': {'id': 5, 'username': 'jackyyuqi', 'name': 'Jacky'}}, wd)
cmd, kw = calls[0]
check('呼叫 consultant_ops.py summary <人選> --file', cmd[1].endswith('consultant_ops.py') and cmd[2:5] == ['summary', 'A1', '--file'], cmd)
check('用 COPS_* 環境變數告訴它是誰、在哪個主題（確認卡才貼得回去）', kw['env']['COPS_TG_USERNAME'] == 'jackyyuqi' and kw['env']['COPS_CHAT'] == '-1003' and kw['env']['COPS_THREAD'] == '77' and kw['env']['COPS_TG_NAME'] == 'Jacky')
check('逐字稿檔權限 600', oct(os.stat(os.path.join(wd, 'transcript.txt')).st_mode & 0o777) == '0o600')
P.returncode = 1; P.stderr = '查不到'
try:
    C.post_card({'id': 'A1'}, 'x' * 40, {}, wd); e5 = ''
except C.CallAudioError as e:
    e5 = str(e)
check('consultant_ops 失敗 → CallAudioError', '貼確認卡失敗' in e5)
C.subprocess.run = real_run

print('\n[G] 轉文字子程序的命令列')
calls.clear()
class P2: returncode = 0; stdout = ''; stderr = ''
C.subprocess.run = lambda cmd, **kw: (calls.append(cmd), open(cmd[cmd.index('--out') + 1], 'w').write('{"text":"好","duration":1}'), P2())[2]
wd2 = tempfile.mkdtemp(); fake_audio = os.path.join(wd2, 'rec.m4a'); open(fake_audio, 'wb').write(b'x')
res = C.transcribe(fake_audio, ['商周集團', 'T100'])
cmd = calls[0]
check('用 nice 10 + venv python 跑自己的 --transcribe，帶 prompt／hotwords', cmd[:3] == ['nice', '-n', '10'] and cmd[3] == C.VENV_PY and '--transcribe' in cmd and '--hotwords' in cmd and '商周集團 T100' in cmd, cmd)
check('預設不允許下載模型（沒有 --allow-download）', '--allow-download' not in cmd)
check('提示詞檔權限 600', oct(os.stat(os.path.join(wd2, 'prompt.txt')).st_mode & 0o777) == '0o600')
C.subprocess.run = real_run

print('\n[H] ai_worker 整合')
src = open('ai_worker.py', encoding='utf-8').read()
check("process() 有 call_audio 分派", "if kind == 'call_audio':" in src and 'call_audio.run(job)' in src)
check("撈工作條件：沒啟用或有面談就排除 call_audio", "_kind_filter = '' if _ca_ok else \"AND kind <> 'call_audio' \"" in src)
check("SQL 用括號包住再套 %（避免只套到最後一段）", '("SELECT * FROM ai_jobs WHERE status=\'pending\' AND attempts < %d " + _kind_filter' in src)
check("call_audio 在可回收清單、做完的列會被清掉、最終失敗會通知", "'call_audio'," in src and "DELETE FROM ai_jobs WHERE kind='call_audio'" in src and 'call_audio.notify_failure' in src)
check('enabled()：不是 WSL2 或沒裝 venv 就是 False', (C.enabled() in (True, False)))
real_rel = C.platform.release
C.platform.release = lambda: '6.1.0-generic'
check('非 WSL2（例如 Mac／一般 Linux）→ 永遠不啟用', C.enabled() is False)
C.platform.release = real_rel

print('\n失敗 %d 項' % len(fails))
sys.exit(1 if fails else 0)
