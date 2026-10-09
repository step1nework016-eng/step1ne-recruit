#!/usr/bin/env python3
"""acai_watch.py — 阿財面談備援通知（E20，2026-10-09 Jacky）

為什麼有這支：Jacky 要「接下來會跟阿財面談的人選，隨時有人顧著」。人選在面談裡講了話，阿財太久沒回，
Jacky 要能馬上知道、直接打電話接手，不要等人選自己放棄。這支每 2 分鐘跑一次，只看、只通知 Jacky 的 TG 私訊：

  🟢 開始面談：人選第一次真的開口（不算「進入面談室」那個系統標記）→ 通知一次
  🚨 阿財太久沒回：人選最後一則訊息超過 3 分鐘阿財還沒回 → 通知（附電話、人選卡片連結）；
        同一則最多每 10 分鐘提醒一次、最多 6 次；阿財回了就自動停。
        通知前先試著自己修：阿財程式沒在跑 → 重啟；面談鎖過期沒清 → 清掉（跟修復員 step1ne_medic 同一套判準：可逆、不對外），
        修了就在通知裡說一聲。
  ✅ 面談結束：狀態從進行中變成結束（done／paused）→ 通知一次，說報告正在產生

不做的事：不改面談內容、不對人選發任何訊息、不碰報告。
為什麼不併進 step1ne_medic.py：修復員每 30 分鐘一次、而且要 import 整支 interview_daemon；這裡 2 分鐘一次、
要輕、要有自己的「通知過了沒」記憶，分開比較乾淨。修復員的 launchctl 那套在 WSL2 也用不上（這台是 systemd）。

用法：
  python3 acai_watch.py             # 正常跑（通知＋會修）
  python3 acai_watch.py --dry       # 照真資料判斷、印出會做什麼，不通知、不修、不寫記憶檔
  python3 acai_watch.py --test-send # 用假資料把三種通知各發一則給 Jacky（標明【測試】）
D1 用量：每次 3 個很小的查詢（最近 600 則訊息＋少數幾筆人選），一天約 40 萬列讀，不碰全表。
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402

try:                     # E17 的等待過渡語（還沒上線也沒關係）：過渡語不算阿財的正式回覆
    import interview_markers as MK  # noqa: E402
except ImportError:
    MK = None

JACKY_TG = '8365775688'
ENTRY_MARKER = '（候選人已進入面談室）'
STATE_PATH = os.path.expanduser('~/aijob-automation/state/acai_watch.json')
SERVICE = 'step1ne-interview'
CARD = 'https://step1ne.com/consultant/candidates/?tab=triage&app={}'

WINDOW_SEC = 6 * 3600        # 只看最近 6 小時內有動靜的面談
RECENT_MSGS = 600            # 只讀最近這麼多則訊息（避免掃全表、省 D1 額度）
SLOW_SEC = 180               # 人選講完話超過 3 分鐘阿財沒回 → 警報
REPEAT_SEC = 600             # 同一則最多每 10 分鐘提醒一次
MAX_ALERTS = 6               # 同一則最多提醒幾次（約一小時），免得一直響
START_FRESH_SEC = 15 * 60    # 「開始面談」只在第一次開口後 15 分鐘內補發
END_FRESH_SEC = 20 * 60
D1_FAIL_ALERT = 5            # 連續幾次查不到 D1 就私訊一次（備援自己瞎了也要讓人知道）
FMT = '%Y-%m-%d %H:%M:%S'


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def tw_now():
    # D1 的時間都是台灣時間，不依賴這台機器的時區設定
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None) + datetime.timedelta(hours=8)


def ts(s):
    return datetime.datetime.strptime(str(s)[:19], FMT)


def is_transition(text):
    return bool(MK and MK.is_transition(text))


# ── 通知（只私訊 Jacky）────────────────────────────────────────
def dm(text):
    """送到 Jacky 的 TG 私訊。成功回 True。"""
    try:
        e = dict(l.strip().split('=', 1) for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
                 if '=' in l and not l.startswith('#'))
        r = json.loads(urllib.request.urlopen(
            f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
            data=urllib.parse.urlencode({'chat_id': JACKY_TG, 'text': text, 'disable_web_page_preview': 'true'}).encode(),
            timeout=20).read())
        return bool(r.get('ok'))
    except Exception as ex:
        log(f'TG 私訊失敗：{str(ex)[:120]}')
        return False


def d1(sql, tries=2):
    last = None
    for i in range(tries):
        try:
            return d1_http.query(sql).get('results') or []
        except Exception as e:
            last = e
            time.sleep(2)
    raise RuntimeError(str(last)[:160])


# ── 文字 ──────────────────────────────────────────────────────
def job_of(a):
    return a.get('job_title') or a.get('job_slug') or '（未知職缺）'


def msg_start(a, first, prefix=''):
    return (f"{prefix}🟢 開始面談｜{a['name']}\n職缺：{job_of(a)}\n第一次開口：{ts(first['created_at']):%H:%M}\n"
            f"人選卡片：{CARD.format(a['id'])}")


def msg_slow(a, last, wait_min, n, fixes, prefix=''):
    snippet = str(last['content']).replace('\n', ' ')
    snippet = '（進入面談室，阿財還沒打招呼）' if snippet == ENTRY_MARKER else '「' + snippet[:40] + ('…' if len(snippet) > 40 else '') + '」'
    s = (f"{prefix}🚨 阿財 {wait_min} 分鐘沒回｜{a['name']}\n職缺：{job_of(a)}\n電話：{a.get('phone') or '（沒有電話）'}\n"
         f"他最後說：{snippet}（{ts(last['created_at']):%H:%M}）\n人選卡片：{CARD.format(a['id'])}")
    if fixes:
        s += '\n🔧 已自動處理：' + '；'.join(fixes)
    s += f"\n（每 10 分鐘提醒一次，阿財回了就停；第 {n}/{MAX_ALERTS} 次）"
    return s


def msg_end(a, state, prefix=''):
    how = '正常收尾' if state == 'done' else '人選離開（閒置收尾，房間先留著）'
    return f"{prefix}✅ 面談結束｜{a['name']}\n職缺：{job_of(a)}\n{how}，報告正在產生，完成後會另外推播。"


# ── 能自己修的先修（可逆、不對外）──────────────────────────────
def daemon_active():
    r = subprocess.run(['systemctl', '--user', 'is-active', SERVICE], capture_output=True, text=True)
    return r.stdout.strip() == 'active'


def restart_daemon():
    subprocess.run(['systemctl', '--user', 'restart', SERVICE], capture_output=True)
    time.sleep(4)
    return daemon_active()


def clear_expired_lock(app_id, now):
    d1(f"UPDATE applications SET lock_expires_at=NULL WHERE id='{app_id}' AND lock_expires_at IS NOT NULL "
       f"AND lock_expires_at < '{now.strftime(FMT)}'")


# ── 判斷（純函式：給資料就回要做的事，測試用假資料就能跑）────────────
def decide(now, apps, msgs_by_app, state, fixer):
    """回傳 (actions, new_state)。actions 是 [('start'|'slow'|'end', 應用資料, 內容...)]。
    fixer：{'daemon_active': f, 'restart': f, 'clear_lock': f}，只在要發警報時才會呼叫。"""
    st = json.loads(json.dumps(state))          # 不改傳進來的
    st.setdefault('apps', {}); st.setdefault('slow', {})
    actions = []
    for a in apps:
        aid = a['id']
        ms = sorted(msgs_by_app.get(aid, []), key=lambda m: m['id'])
        if not ms:
            continue
        real = [m for m in ms if not is_transition(m['content'])]
        mem = st['apps'].setdefault(aid, {'seen_at': now.strftime(FMT)})
        # 🟢 開始
        first = next((m for m in real if m['role'] == 'candidate' and m['content'] != ENTRY_MARKER), None)
        if first and not mem.get('start'):
            fresh = (now - ts(first['created_at'])).total_seconds() <= START_FRESH_SEC
            mem['start'] = now.strftime(FMT)
            if fresh:
                actions.append(('start', a, first))
        # 🚨 太久沒回
        last = real[-1] if real else None
        if last and last['role'] == 'candidate':
            wait = (now - ts(last['created_at'])).total_seconds()
            key = str(last['id'])
            rec = st['slow'].get(key) or {'n': 0, 'last': None}
            due = wait >= SLOW_SEC and rec['n'] < MAX_ALERTS and (rec['last'] is None or (now - ts(rec['last'])).total_seconds() >= REPEAT_SEC)
            if due:
                fixes = []
                if not rec.get('fixed'):
                    if not fixer['daemon_active']():
                        fixes.append('阿財程式沒在跑，已重啟' if fixer['restart']() else '阿財程式沒在跑，重啟失敗（需要人處理）')
                    if a.get('lock_expires_at') and ts(a['lock_expires_at']) < now:
                        fixer['clear_lock'](aid, now)
                        fixes.append('面談鎖過期沒清，已清除')
                    rec['fixed'] = True
                rec['n'] += 1
                rec['last'] = now.strftime(FMT)
                st['slow'][key] = rec
                actions.append(('slow', a, last, int(wait // 60), rec['n'], fixes))
        # ✅ 結束
        prev = mem.get('state')
        cur = a.get('interview_state')
        if cur in ('done', 'paused'):
            if prev == 'active':
                actions.append(('end', a, cur))
            elif prev is None and cur == 'done' and a.get('interview_ended_at') and not mem.get('end') \
                    and (now - ts(a['interview_ended_at'])).total_seconds() <= END_FRESH_SEC:
                actions.append(('end', a, cur))
                mem['end'] = now.strftime(FMT)
        mem['state'] = cur
    # 清掉 24 小時前的記憶
    cut = (now - datetime.timedelta(hours=24)).strftime(FMT)
    st['apps'] = {k: v for k, v in st['apps'].items() if v.get('seen_at', '') >= cut or v.get('state') == 'active'}
    st['slow'] = {k: v for k, v in st['slow'].items() if (v.get('last') or '') >= cut}
    return actions, st


# ── 資料 ──────────────────────────────────────────────────────
def fetch(now):
    cutoff = (now - datetime.timedelta(seconds=WINDOW_SEC)).strftime(FMT)
    rows = d1(f"SELECT id, application_id, role, content, created_at FROM messages "
              f"WHERE id > (SELECT COALESCE(MAX(id), 0) FROM messages) - {RECENT_MSGS} ORDER BY id")
    rows = [r for r in rows if r['created_at'] >= cutoff]
    msgs = {}
    for r in rows:
        msgs.setdefault(r['application_id'], []).append(r)
    if not msgs:
        return [], {}
    ids = ','.join("'" + i.replace("'", "") + "'" for i in msgs)
    apps = d1(f"SELECT id, name, job_title, job_slug, phone, interview_state, interview_started_at, interview_ended_at, lock_expires_at "
              f"FROM applications WHERE id IN ({ids}) AND interview_state IN ('active','paused','done')")
    return apps, msgs


def load_state():
    try:
        return json.load(open(STATE_PATH, encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + '.tmp'
    json.dump(st, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
    os.replace(tmp, STATE_PATH)


def render(act, prefix=''):
    kind = act[0]
    if kind == 'start':
        return msg_start(act[1], act[2], prefix)
    if kind == 'slow':
        return msg_slow(act[1], act[2], act[3], act[4], act[5], prefix)
    return msg_end(act[1], act[2], prefix)


def run(dry=False):
    now = tw_now()
    state = load_state()
    try:
        apps, msgs = fetch(now)
    except Exception as e:
        state['d1_fail'] = state.get('d1_fail', 0) + 1
        log(f'⚠️ 查不到 D1（連續第 {state["d1_fail"]} 次）：{e}')
        if state['d1_fail'] >= D1_FAIL_ALERT and not state.get('d1_alerted') and not dry:
            if dm('⚠️ 阿財備援通知（acai_watch）連續 %d 次查不到資料庫——這段時間人選有沒有被晾著，備援看不到，請留意。' % state['d1_fail']):
                state['d1_alerted'] = True
        if not dry:
            save_state(state)
        return
    if state.get('d1_fail', 0) >= D1_FAIL_ALERT and state.get('d1_alerted') and not dry:
        dm('✅ 阿財備援通知（acai_watch）已經恢復，查得到資料庫了。')
    state['d1_fail'] = 0; state['d1_alerted'] = False
    fixer = {'daemon_active': daemon_active, 'restart': restart_daemon, 'clear_lock': clear_expired_lock}
    if dry:     # --dry 不修
        fixer = {'daemon_active': lambda: True, 'restart': lambda: True, 'clear_lock': lambda *a: None}
    actions, new_state = decide(now, apps, msgs, state, fixer)
    new_state['d1_fail'] = 0; new_state['d1_alerted'] = False
    log(f'看了 {len(apps)} 場面談、{sum(len(v) for v in msgs.values())} 則最近訊息；要通知 {len(actions)} 件')
    for act in actions:
        text = render(act)
        if dry:
            log('（--dry）會送：\n' + text + '\n')
            continue
        if not dm(text):
            # 沒送出去就不要記成「通知過了」，下一輪再試
            if act[0] == 'slow':
                new_state['slow'].pop(str(act[2]['id']), None)
            elif act[0] == 'start':
                new_state['apps'][act[1]['id']].pop('start', None)
            elif act[0] == 'end':
                new_state['apps'][act[1]['id']]['state'] = 'active'
        else:
            log(f'已私訊 Jacky：{act[0]}｜{act[1]["name"]}')
    if not dry:
        save_state(new_state)


def test_send():
    """用假資料把三種通知各發一則給 Jacky，開頭標明【測試】。"""
    now = tw_now()
    a = {'id': 'test-0000', 'name': '測試人選（假資料）', 'job_title': '測試職缺', 'phone': '0900-000-000',
         'interview_state': 'active', 'lock_expires_at': None}
    first = {'id': 1, 'role': 'candidate', 'content': '您好，我準備好了', 'created_at': now.strftime(FMT)}
    last = {'id': 2, 'role': 'candidate', 'content': '請問這個職缺的工作地點是在哪裡？薪資範圍大概多少？', 'created_at': (now - datetime.timedelta(minutes=4)).strftime(FMT)}
    P = '【測試，請忽略，以下是假資料】\n'
    for t in (msg_start(a, first, P), msg_slow(a, last, 4, 1, ['阿財程式沒在跑，已重啟', '面談鎖過期沒清，已清除'], P), msg_end(a, 'done', P)):
        print(t, '\n', '→ 已送出' if dm(t) else '→ 送出失敗', '\n')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--test-send', action='store_true')
    args = ap.parse_args()
    if args.test_send:
        test_send()
    else:
        run(dry=args.dry)
