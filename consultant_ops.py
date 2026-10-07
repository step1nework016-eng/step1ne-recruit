#!/usr/bin/env python3
"""顧問助理的工具箱（TG「Step1ne AI 顧問室」→ 🧑‍💼 顧問助理 topic 用）。

2026-10-07 建立。顧問在 TG 跟助理講話，助理只能跑這一支（bot.py 用
--permission-mode dontAsk + --allowedTools 鎖死），所以這支本身就是安全邊界：

  * 查詢類（find / job / today / reminders / pending）：唯讀，直接回答。
  * 會寄信或寫人選卡片的（call-mail / call-confirm / decline / summary / book）：
    這裡**只做預覽**——跟後台要草稿、存一筆 consultant_ops_actions(status=pending)、
    用 step1nechi_bot 在同一個 topic 貼「確認卡」（TG inline 按鈕）。
    真正寄出／寫入只發生在 step1ne-recruit Worker 的按鈕 callback（cop_ok），
    而且只有 Jacky、Phoebe（BD_APPROVERS）或提出的那位顧問本人按才算。
    這支程式**沒有任何「直接寄出」的指令**，助理也就不可能自己寄。
  * remind / cancel-remind：只是發 TG 提醒給顧問自己，不對外，直接建立。

誰在講話、在哪個 topic，由 bot.py 放在環境變數（COPS_TG_ID / COPS_TG_USERNAME /
COPS_TG_NAME / COPS_CHAT / COPS_THREAD），不吃命令列參數，助理改不了。

輸出是給助理看的純文字；助理再用白話轉述給顧問。
"""
import argparse, datetime as dt, json, os, re, secrets, sys, urllib.error, urllib.parse, urllib.request

HOME = os.path.expanduser('~')
CFG = f'{HOME}/.config/workflow-os'
DB_ID = '67077b4b-42d9-4086-8d04-b3658a89cffd'   # step1ne-recruit
BACKOFFICE = 'https://step1ne-backoffice-worker.aiagentg888.workers.dev'
UA = 'Mozilla/5.0 (Macintosh; step1ne-consultant-ops)'   # CF 會擋 urllib 預設 UA
TZ = dt.timezone(dt.timedelta(hours=8))
EXPIRE_H = 24
# TG username → 顧問顯示名（信件署名用）。跟 consultants 表 / BD_APPROVERS 對齊。
TG_TO_CONSULTANT = {'jackyyuqi': 'Jacky', 'behe10': 'Phoebe'}
DECLINE_REASONS = {'fit': '條件有落差', 'employer': '用人單位這次選了其他方向',
                   'filled': '職缺已有人選進最後階段', 'paused': '職缺暫停招募'}
CALL_TOPICS = {'will': '意願與想了解的地方', 'pay': '期望待遇、到職時間', 'gap': '面談還沒聊完的細節',
               'lang': '外語簡單聊幾句', 'now': '目前工作狀況與換工作時間', 'work': '最近工作實際內容',
               'lead': '帶人經驗', 'place': '地點／通勤／出差', 'flow': '面試流程與時間', 'other': '其他想了解的職缺方向'}
# 候選人看得到的文字：最後一道本機檢查（後台與 Worker 也各擋一次）
BANNED = re.compile(r'客戶|年齡|\d+\s*歲|歲以[上下]|性別|男性|女性|男生|女生|已婚|未婚|懷孕|生育|小孩|外貌|身高|體重|國籍|宗教|政治')


def _env(path):
    out = {}
    try:
        for line in open(path, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"\'')
    except OSError:
        pass
    return out


_TOK = _env(f'{CFG}/tokens.env')
_CF = _env(f'{CFG}/cf.env')
_TG = _env(f'{CFG}/step1ne-tg.env')


def die(msg, code=1):
    print(f'❌ {msg}')
    sys.exit(code)


def now_tpe():
    return dt.datetime.now(TZ).replace(tzinfo=None)


def fmt(t):
    return t.strftime('%Y-%m-%d %H:%M')


# ── D1（有參數綁定，不用自己轉義）──
def d1(sql, params=None):
    tok, acc = _CF.get('CLOUDFLARE_API_TOKEN'), _CF.get('CLOUDFLARE_ACCOUNT_ID')
    if not (tok and acc):
        die('找不到 Cloudflare 設定（cf.env）')
    req = urllib.request.Request(
        f'https://api.cloudflare.com/client/v4/accounts/{acc}/d1/database/{DB_ID}/query',
        data=json.dumps({'sql': sql, 'params': params or []}).encode(),
        headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'})
    try:
        body = json.load(urllib.request.urlopen(req, timeout=40))
    except urllib.error.HTTPError as e:
        die(f'資料庫連線失敗 HTTP {e.code}')
    if not body.get('success'):
        die(f'資料庫錯誤：{body.get("errors")}')
    return body['result'][0]


def rows(sql, params=None):
    return d1(sql, params).get('results') or []


# ── 後台 API ──
def bo(path, body=None, method=None):
    tok = _TOK.get('RECRUIT_ADMIN_TOKEN')
    if not tok:
        die('找不到後台金鑰設定')
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BACKOFFICE + path, data=data, method=method or ('POST' if data else 'GET'),
                                 headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json', 'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {'ok': False, 'error': f'HTTP {e.code}'}


# ── 誰在講話（bot.py 給的，助理改不了）──
def who():
    tg_id = os.environ.get('COPS_TG_ID', '').strip()
    uname = os.environ.get('COPS_TG_USERNAME', '').strip().lower()
    name = os.environ.get('COPS_TG_NAME', '').strip()
    chat = os.environ.get('COPS_CHAT', '').strip()
    thread = os.environ.get('COPS_THREAD', '').strip()
    display = TG_TO_CONSULTANT.get(uname) or name or uname or ''
    return {'tg_id': tg_id, 'username': uname, 'name': display, 'chat': chat, 'thread': thread}


def need_where(w):
    if not w['chat'] or not w['tg_id']:
        die('不知道是誰在哪裡下的指令（這支只能從 TG 顧問助理裡用）')


# ── 找人選 ──
def resolve(key):
    key = (key or '').strip()
    if not key:
        die('請給人選姓名或編號')
    if re.fullmatch(r'[0-9a-f]{8}-[0-9a-f-]{27}', key):
        r = rows('SELECT id,name,job_slug,job_title,created_at FROM applications WHERE id=?', [key])
        if not r:
            die('找不到這個編號的人選')
        return r[0]
    r = rows("""SELECT a.id,a.name,a.job_slug,COALESCE(j.title,a.job_title) AS job_title,a.created_at,a.status
                  FROM applications a LEFT JOIN jobs j ON j.slug=a.job_slug
                 WHERE a.name LIKE ? AND a.superseded_by IS NULL AND COALESCE(a.status,'')<>'duplicate'
                 ORDER BY a.created_at DESC LIMIT 12""", [f'%{key}%'])
    if not r:
        die(f'查不到「{key}」這位人選（名字可以只打一部分試試）')
    if len(r) > 1:
        print(f'「{key}」對到 {len(r)} 筆應徵，請用編號指定：')
        for x in r:
            print(f'  {x["id"]}｜{x["name"]}｜{x["job_title"] or x["job_slug"]}｜{str(x["created_at"])[:10]}')
        sys.exit(2)
    return r[0]


def clip(t, n):
    t = re.sub(r'\s+', ' ', str(t or '')).strip()
    return t if len(t) <= n else t[:n] + '…'


def cmd_find(a):
    # 先把同名的全部列出來（同一個人投兩個缺時，兩筆都要讓顧問知道）
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f-]{27}', a.who.strip()):
        r = rows("""SELECT a.id FROM applications a WHERE a.name LIKE ? AND a.superseded_by IS NULL
                    AND COALESCE(a.status,'')<>'duplicate' ORDER BY a.created_at DESC LIMIT 12""", [f'%{a.who.strip()}%'])
        if len(r) > 1 and not a.all:
            resolve(a.who)   # 會列清單並結束
        ids = [x['id'] for x in r] if r else [resolve(a.who)['id']]
    else:
        ids = [resolve(a.who)['id']]
    for i, app_id in enumerate(ids):
        if i:
            print('\n' + '═' * 20 + '\n')
        show_one(app_id)


def show_one(app_id):
    card = bo(f'/admin/application/{urllib.parse.quote(app_id)}/card')
    if not card.get('ok'):
        die(card.get('error') or '讀不到人選卡片')
    ap = card.get('application') or {}
    job = rows("SELECT title, confidential_client, status FROM jobs WHERE slug=?", [ap.get('job_slug') or ''])
    job = job[0] if job else {}
    print(f'【{ap.get("name")}】編號 {ap.get("id")}')
    print(f'應徵：{job.get("title") or ap.get("job_title") or "（未指定職缺）"}（{ap.get("job_slug")}）'
          + ('｜⚠️ 保密客戶職缺：對人選不能講公司名' if job.get('confidential_client') else '')
          + (f'｜職缺狀態 {job.get("status")}' if job.get('status') and job.get('status') != 'open' else ''))
    fw = card.get('forwards') or []
    if fw:
        for f in fw:
            st = f.get('stage_label') or ''
            extra = []
            if f.get('client_rejected_at'): extra.append(f'用人單位婉拒（{clip(f.get("client_reject_reason"), 40)}）')
            if f.get('advisor_not_recommended_at'): extra.append('顧問標不推薦')
            if f.get('no_show_at'): extra.append('不報到')
            print(f'階段：已推薦給 {f.get("company_name")}（{str(f.get("forwarded_at"))[:10]}）→ 目前「{st}」' + (f'｜{"、".join(extra)}' if extra else ''))
    else:
        rep = card.get('report') or {}
        dec = {'forwarded': '已推薦', 'declined': '已婉拒', 'need_more': '需補問', 'hold': '保留'}.get(rep.get('consultant_decision') or '', '')
        state = {'done': '阿財面談完成', 'in_progress': '阿財面談中', 'not_started': '還沒開始面談'}.get(ap.get('interview_state') or '', ap.get('interview_state') or '')
        print(f'階段：{ap.get("manual_stage") or dec or state or ap.get("status") or "—"}（系統狀態 {ap.get("status")}）')
    print(f'電話：{ap.get("phone") or "—"}　Email：{ap.get("email") or "—"}')
    print(f'期望薪資：{ap.get("expected_salary") or "—"}　可到職：{ap.get("available_date") or "—"}')
    print(f'LINE：{"已綁定" if card.get("line_binding") else "沒有綁"}')
    if ap.get('decline_email_sent_at'):
        print(f'⚠️ 已寄過婉拒信：{str(ap["decline_email_sent_at"])[:16]}')
    rep = card.get('report') or {}
    cj = {}
    try:
        cj = json.loads(rep.get('content_json') or '{}')
    except Exception:
        cj = {}
    if rep:
        print(f'\n阿財報告（{str(rep.get("created_at"))[:10]}，分流 {rep.get("route_label") or "—"}）')
        for k, lb in (('verdict', '結論'), ('one_liner', '一句話'), ('top_selling_point', '最大賣點'), ('top_risk', '最大風險')):
            v = cj.get(k)
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            if v:
                print(f'・{lb}：{clip(v, 220)}')
        fu = cj.get('consultant_followups') or []
        if fu:
            print('・電話要追問：')
            for x in fu[:6]:
                t = x if isinstance(x, str) else (x.get('question') or x.get('item') or x.get('text') or json.dumps(x, ensure_ascii=False))
                print(f'   - {clip(t, 120)}')
    else:
        print('\n阿財報告：還沒有')
    if ap.get('call_summary_md'):
        print(f'\n電洽摘要（節錄）：{clip(ap["call_summary_md"], 300)}')
    # 最近聯繫：把五種來源放在一起排
    ev = []
    for n in card.get('notes') or []:
        ev.append((n.get('created_at'), f'備註「{n.get("type")}」{clip(n.get("content"), 60)}（{n.get("created_by") or "—"}）'))
    for m in rows("SELECT subject,note,sent_by,sent_at,status,reply_at FROM call_booking_mails WHERE application_id=? ORDER BY created_at DESC LIMIT 5", [app_id]):
        if m.get('sent_at'):
            ev.append((m['sent_at'], f'寄信「{clip(m.get("subject"), 40)}」{clip(m.get("note"), 40)}（{m.get("sent_by") or "—"}）'))
        if m.get('reply_at'):
            ev.append((m['reply_at'], '人選回信（約電話那封）'))
    for m in rows("SELECT subject,matched,created_at FROM inbound_mail_seen WHERE matched_id=? ORDER BY created_at DESC LIMIT 5", [app_id]):
        ev.append((m['created_at'], f'收到人選來信「{clip(m.get("subject"), 40)}」'))
    for m in rows("SELECT stage,status,confirmed_slot,created_at,created_by FROM interview_appointments WHERE application_id=? ORDER BY created_at DESC LIMIT 5", [app_id]):
        lbl = '顧問電話' if m.get('stage') == 0 else f'第{m.get("stage")}關面試'
        ev.append((m['created_at'], f'{lbl}：{m.get("status")}' + (f' 時間 {m["confirmed_slot"]}' if m.get('confirmed_slot') else '')))
    for m in rows("SELECT f.forwarded_at, c.display_name FROM candidate_forwards f LEFT JOIN client_companies c ON c.id=f.company_id WHERE f.application_id=? ORDER BY f.forwarded_at DESC LIMIT 5", [app_id]):
        ev.append((m['forwarded_at'], f'推薦給 {m.get("display_name")}'))
    for m in rows("SELECT call_at,status,requested_by FROM consultant_reminders WHERE application_id=? AND status IN ('pending','sending') ORDER BY call_at", [app_id]):
        ev.append((m['call_at'], f'⏰ 已約電話 {m["call_at"]}（{m.get("requested_by") or "—"}，會提前提醒）'))
    ev = [e for e in ev if e[0]]
    ev.sort(key=lambda e: str(e[0]), reverse=True)
    if ev:
        print(f'\n最近聯繫（新→舊，最近一次 {str(ev[0][0])[:16]}）：')
        for t, s in ev[:8]:
            print(f'・{str(t)[:16]}　{s}')
    else:
        print('\n最近聯繫：系統裡沒有任何紀錄')


# ── 職缺 ──
def cmd_job(a):
    k = a.key.strip()
    r = rows("""SELECT slug,title,status,confidential_client FROM jobs
                 WHERE slug=? OR title LIKE ? OR client_name LIKE ? ORDER BY (status='open') DESC, updated_at DESC LIMIT 10""",
             [k, f'%{k}%', f'%{k}%'])
    if not r:
        die(f'找不到跟「{k}」有關的職缺')
    if len(r) > 1 and r[0]['slug'] != k:
        print(f'對到 {len(r)} 個職缺，請用代號指定：')
        for x in r:
            print(f'  {x["slug"]}｜{x["title"]}｜{x["status"]}')
        sys.exit(2)
    d = bo(f'/admin/jobs/{urllib.parse.quote(r[0]["slug"])}/intro')
    if not d.get('ok'):
        die(d.get('error') or '讀不到職缺')
    j = d['job']
    sal = ''
    if j.get('salary_min') or j.get('salary_max'):
        sal = f'{j.get("salary_min") or "?"}～{j.get("salary_max") or "?"} {j.get("salary_unit") or ""}'
    print(f'【{j.get("title")}】（{j.get("slug")}）狀態 {r[0]["status"]}')
    print(f'公司：{j.get("client_name") or "—"}' + ('｜⚠️ 保密：對人選不能講公司名' if r[0].get('confidential_client') else ''))
    for k2, lb in (('locations', '地點'), ('employment', '僱用'), ('work_mode', '工作型態'), ('work_hours', '工時'),
                   ('urgency', '急迫度'), ('onboard_by', '到職'), ('interview_process', '面試流程')):
        if j.get(k2):
            print(f'{lb}：{clip(j[k2], 160)}')
    if sal or j.get('salary_note'):
        print(f'薪資：{sal} {clip(j.get("salary_note"), 120)}')
    if j.get('main_duties'):
        print(f'主要工作：{clip(j["main_duties"], 400)}')
    if j.get('nice_to_have_skills'):
        print(f'加分：{clip(j["nice_to_have_skills"], 200)}')
    if j.get('client_intro'):
        print(f'公司介紹：{clip(j["client_intro"], 300)}')
    if j.get('news_pinned'):
        print(f'\n公司近期動態（顧問釘選）：{clip(j["news_pinned"], 500)}')
    if j.get('news_brief'):
        print(f'\n公司近期動態（{str(j.get("news_updated_at") or "")[:10]} 更新）：{clip(j["news_brief"], 700)}')
    print('\n（以上是內部資料：公司名、篩選條件、內部備註都不能直接講給人選聽）')


# ── 今天/明天的電話 ──
def cmd_today(a):
    t0 = now_tpe().replace(hour=0, minute=0, second=0, microsecond=0)
    t1 = t0 + dt.timedelta(days=max(1, a.days))
    lo, hi = fmt(t0), fmt(t1)
    items = []
    for r in rows("""SELECT r.call_at, r.candidate_name, r.application_id, r.requested_by, r.message, r.status, r.id
                       FROM consultant_reminders r WHERE r.call_at>=? AND r.call_at<? AND r.status IN ('pending','sending','sent')
                       ORDER BY r.call_at""", [lo, hi]):
        items.append((r['call_at'], f'☎️ {r.get("candidate_name") or "（沒掛人選）"}｜{r.get("requested_by") or "—"}'
                      + (f'｜{clip(r.get("message"), 50)}' if r.get('message') else '') + f'｜提醒 {r["status"]}（{r["id"]}）'))
    for r in rows("""SELECT i.confirmed_slot, i.stage, i.location, a.name, a.job_title, i.created_by
                       FROM interview_appointments i JOIN applications a ON a.id=i.application_id
                      WHERE i.status='confirmed' AND i.confirmed_slot>=? AND i.confirmed_slot<? ORDER BY i.confirmed_slot""", [lo, hi]):
        lbl = '顧問電話' if r.get('stage') == 0 else f'第{r.get("stage")}關面試'
        items.append((r['confirmed_slot'], f'📅 {r["name"]}｜{lbl}｜{r.get("job_title") or ""}' + (f'｜{r["location"]}' if r.get('location') else '')))
    items.sort(key=lambda x: str(x[0]))
    print(f'{lo[:10]} 起 {a.days} 天內的電話／面試：')
    if not items:
        print('（沒有排定的）')
    for t, s in items:
        print(f'・{str(t)[5:16]}　{s}')
    # 顧問在後台手寫的「電洽預約」備註（自由文字，時間要看內容）——顧問助理建的已經在上面了，不重複列
    notes = rows("""SELECT n.content, n.created_at, a.name FROM candidate_notes n JOIN applications a ON a.id=n.application_id
                      WHERE n.type='電洽預約' AND n.created_at >= ?
                        AND NOT EXISTS (SELECT 1 FROM consultant_reminders r WHERE r.application_id=n.application_id
                                         AND substr(n.content,1,40) LIKE '%' || r.call_at || '%')
                      ORDER BY n.created_at DESC LIMIT 10""", [fmt(t0 - dt.timedelta(days=7))])
    if notes:
        print('\n最近 7 天手寫登記的電洽預約（時間請看內容，可能不在上面的範圍內）：')
        for n in notes:
            print(f'・{n["name"]}：{clip(n["content"], 90)}')
    # 寄了「確認電話時間」信但沒登記的
    cc = rows("""SELECT b.note, b.sent_at, a.name FROM call_booking_mails b JOIN applications a ON a.id=b.application_id
                   WHERE b.status='sent' AND b.note LIKE '已約電話：%' AND b.sent_at >= ? ORDER BY b.sent_at DESC LIMIT 8""",
              [fmt(t0 - dt.timedelta(days=3))])
    if cc:
        print('\n最近 3 天寄出的「確認電話時間」信：')
        for n in cc:
            print(f'・{n["name"]}：{clip(n["note"], 80)}（{str(n["sent_at"])[5:16]} 寄）')
    print('\n註：有時間的清單只算系統裡登記的（顧問助理約的電話＋面試排程）；下面兩段是文字紀錄，時間請自己看。')


# ── 建立待確認動作＋貼確認卡 ──
def tg(method, payload):
    tok = _TG.get('TG_BOT_TOKEN')
    if not tok:
        die('找不到系統 bot 設定')
    req = urllib.request.Request(f'https://api.telegram.org/bot{tok}/{method}', data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        return json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {'ok': False, 'description': f'HTTP {e.code}'}


def create_action(kind, app, params, to, subject, text, card_text, buttons):
    w = who()
    need_where(w)
    aid = 'cop' + secrets.token_hex(6)
    now = now_tpe()
    d1("""INSERT INTO consultant_ops_actions (id,kind,application_id,candidate_name,params_json,preview_to,preview_subject,preview_text,
             requested_by,requested_tg_id,requested_tg_username,chat_id,thread_id,status,created_at,expires_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
       [aid, kind, app['id'], app['name'], json.dumps(params, ensure_ascii=False), to, subject, text,
        w['name'], w['tg_id'], w['username'], w['chat'], w['thread'] or None, fmt(now), fmt(now + dt.timedelta(hours=EXPIRE_H))])
    if os.environ.get('COPS_TEST') == '1':   # 工程測試用：卡片標明，避免有人誤按
        card_text = '【測試・請勿按確認，等一下會自動取消】\n' + card_text
    kb = {'inline_keyboard': [[{'text': lb, 'callback_data': f'{cb}:{aid}'} for lb, cb in buttons]]}
    body = {'chat_id': w['chat'], 'text': card_text[:4000], 'reply_markup': kb, 'disable_web_page_preview': True}
    if w['thread']:
        body['message_thread_id'] = int(w['thread'])
    r = tg('sendMessage', body)
    if not r.get('ok'):
        d1("UPDATE consultant_ops_actions SET status='failed', result_json=? WHERE id=?",
           [json.dumps({'error': 'card_post_failed', 'tg': r.get('description')}, ensure_ascii=False), aid])
        die(f'確認卡貼不出去：{r.get("description")}')
    d1('UPDATE consultant_ops_actions SET tg_message_id=? WHERE id=?', [str(r['result']['message_id']), aid])
    return aid


def check_candidate_text(app_id, text):
    """候選人看得到的文字：禁用字＋保密客戶公司名。回傳問題字串或 None。"""
    m = BANNED.search(text or '')
    if m:
        return f'內容出現「{m.group(0)}」，候選人看得到的信不能有這類字（「客戶」請改「用人單位」）'
    r = rows("""SELECT j.confidential_client, j.client_name, c.display_name, c.aliases FROM applications a
                  LEFT JOIN jobs j ON j.slug=a.job_slug LEFT JOIN client_companies c ON c.id=j.company_id WHERE a.id=?""", [app_id])
    if r and r[0].get('confidential_client'):
        names = [r[0].get('client_name'), r[0].get('display_name')] + re.split(r'[,，、;\n]', r[0].get('aliases') or '')
        for n in names:
            n = (n or '').strip()
            core = re.sub(r'(股份有限公司|有限公司|集團|公司)$', '', n)
            for x in {n, core}:
                if len(x) >= 2 and x in (text or ''):
                    return f'這是保密客戶職缺，信裡出現了公司名「{x}」，不能寄'
    return None


def card_head(title, app, to, subject):
    return f'📧 {title}\n人選：{app["name"]}（{app.get("job_title") or app.get("job_slug") or "—"}）\n收件：{to}\n主旨：{subject}\n' + '─' * 14 + '\n'


def card_tail(w, extra=''):
    return ('─' * 14 + '\n' + (extra + '\n' if extra else '') +
            f'由 {w["name"] or "顧問"} 透過顧問助理產生。按「✅ 確認寄出」才會真的寄（Jacky、Phoebe 或 {w["name"] or "提出的人"} 本人可按），{EXPIRE_H} 小時內有效。')


def cmd_call_mail(a):
    app = resolve(a.who)
    w = who(); need_where(w)
    params = {'action': 'preview', 'by': w['name'], 'note': a.note or ''}
    if a.topics:
        keys = [x.strip() for x in a.topics.split(',') if x.strip()]
        bad = [k for k in keys if k not in CALL_TOPICS]
        if bad:
            die(f'不認得的主題代號：{bad}；可用：{CALL_TOPICS}')
        params['topics'] = keys
    if a.topic_extra:
        params['topic_extra'] = a.topic_extra
    if a.ask_resume in ('yes', 'no'):
        params['ask_resume'] = a.ask_resume == 'yes'
    d = bo(f'/admin/applications/{app["id"]}/call-mail', params)
    if not d.get('ok'):
        die(d.get('error') or '產生草稿失敗')
    if d.get('blocked'):
        die(f'現在不能寄：{d["blocked"]}')
    prob = check_candidate_text(app['id'], d['subject'] + '\n' + d['text'])
    if prob:
        die(prob)
    send_params = {k: v for k, v in params.items() if k != 'action'}
    extra = []
    if d.get('line_bound'):
        extra.append('ℹ️ 這位人選有綁 LINE')
    if d.get('followups'):
        extra.append('電話要追問（只給你看，不在信裡）：\n' + '\n'.join(f'・{clip(x, 80)}' for x in d['followups'][:5]))
    card = card_head('確認寄出｜約電話信', app, d['to'], d['subject']) + d['text'] + '\n' + card_tail(w, '\n'.join(extra))
    aid = create_action('call_mail', app, send_params, d['to'], d['subject'], d['text'], card,
                        [('✅ 確認寄出', 'cop_ok'), ('✖ 不寄了', 'cop_no')])
    print(f'✅ 已在這個 topic 貼出確認卡（{aid}），還沒寄。\n收件：{d["to"]}\n主旨：{d["subject"]}\n--- 信件內容 ---\n{d["text"]}')


def parse_when(s):
    s = (s or '').strip().replace('：', ':').replace('／', '/')
    now = now_tpe()
    m = re.fullmatch(r'(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})', s)
    if m:
        return dt.datetime(*map(int, m.groups()))
    m = re.fullmatch(r'(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})', s)
    if m:
        mo, d, h, mi = map(int, m.groups())
        t = dt.datetime(now.year, mo, d, h, mi)
        if t < now - dt.timedelta(days=60):
            t = t.replace(year=now.year + 1)
        return t
    die(f'時間格式看不懂「{s}」，請用「2026-10-08 14:00」或「10/8 14:00」')


def cmd_call_confirm(a):
    app = resolve(a.who)
    w = who(); need_where(w)
    params = {'action': 'preview', 'by': w['name'], 'slot': a.slot}
    if a.salutation:
        params['salutation'] = a.salutation
    if a.phone:
        params['phone'] = a.phone
    if a.extra:
        params['extra'] = a.extra
    d = bo(f'/admin/applications/{app["id"]}/call-confirm', params)
    if not d.get('ok'):
        die(d.get('error') or '產生草稿失敗')
    prob = check_candidate_text(app['id'], d['subject'] + '\n' + d['text'])
    if prob:
        die(prob)
    send_params = {k: v for k, v in params.items() if k != 'action'}
    extra = ''
    if a.at:
        t = parse_when(a.at)
        send_params['_book_at'] = fmt(t)
        send_params['_before'] = a.before
        extra = f'⏰ 按確認後也會：記到人選卡片「電洽預約 {fmt(t)}」，並在 {fmt(t - dt.timedelta(minutes=a.before))[5:]} 私訊提醒你。'
    card = card_head('確認寄出｜回信確認電話時間', app, d['to'], d['subject']) + d['text'] + '\n' + card_tail(w, extra)
    aid = create_action('call_confirm', app, send_params, d['to'], d['subject'], d['text'], card,
                        [('✅ 確認寄出', 'cop_ok'), ('✖ 不寄了', 'cop_no')])
    print(f'✅ 已貼出確認卡（{aid}），還沒寄。\n收件：{d["to"]}\n主旨：{d["subject"]}\n--- 信件內容 ---\n{d["text"]}' + (f'\n{extra}' if extra else ''))


def cmd_decline(a):
    app = resolve(a.who)
    w = who(); need_where(w)
    if a.reason not in DECLINE_REASONS:
        die(f'婉拒理由只能是：{DECLINE_REASONS}')
    params = {'action': 'preview', 'by': w['name'], 'reason': a.reason, 'extra': a.extra or '', 'keep': not a.no_keep}
    if a.subject:
        params['subject'] = a.subject
    if a.text_file:
        params['text'] = open(a.text_file, encoding='utf-8').read()
    d = bo(f'/admin/applications/{app["id"]}/decline-mail', params)
    if not d.get('ok'):
        die(d.get('error') or '產生草稿失敗')
    if d.get('warn'):
        die(d['warn'])
    if d.get('already') and not a.again:
        die(f'{d["already"]}。真的要再寄一次，請顧問明講「再寄一次」')
    prob = check_candidate_text(app['id'], d['subject'] + '\n' + d['text'])
    if prob:
        die(prob)
    # 寄出時把「預覽看到的主旨＋內文」原樣送回去，確保寄出去的就是確認卡上那份
    send_params = {'by': w['name'], 'reason': a.reason, 'extra': a.extra or '', 'keep': not a.no_keep,
                   'subject': d['subject'], 'text': d['text'], 'again': bool(a.again)}
    card = card_head(f'確認寄出｜婉拒信（理由：{DECLINE_REASONS[a.reason]}）', app, d['to'], d['subject']) + d['text'] + '\n' + \
        card_tail(w, '🧪 想先看實際收到的樣子，可以按「寄測試信」（只寄到內部測試信箱）。')
    aid = create_action('decline_mail', app, send_params, d['to'], d['subject'], d['text'], card,
                        [('✅ 確認寄出', 'cop_ok'), ('🧪 寄測試信', 'cop_test'), ('✖ 不寄了', 'cop_no')])
    print(f'✅ 已貼出確認卡（{aid}），還沒寄。\n收件：{d["to"]}\n主旨：{d["subject"]}\n--- 信件內容 ---\n{d["text"]}')


def cmd_summary(a):
    app = resolve(a.who)
    w = who(); need_where(w)
    if a.file:
        text = open(a.file, encoding='utf-8', errors='replace').read()
    else:
        text = a.text or ''
    text = ''.join(c for c in text if c in '\n\t' or ord(c) >= 32).strip()
    if len(text) < 30:
        die('逐字稿太短了，請把整段貼上來')
    params = {'application_id': app['id'], 'content': text[:60000], 'by': w['name'], 'mode': 'transcript'}
    card = (f'📝 確認寫進人選卡片｜電洽逐字稿\n人選：{app["name"]}（{app.get("job_title") or "—"}）\n'
            f'長度：約 {len(text)} 字\n' + '─' * 14 + '\n' + clip(text, 600) + '\n' + '─' * 14 + '\n'
            '按「✅ 寫進卡片」後：逐字稿接在之前的電洽紀錄後面（不會覆蓋），AI 約 1–2 分鐘整理成電洽摘要。\n'
            f'由 {w["name"] or "顧問"} 透過顧問助理產生，{EXPIRE_H} 小時內有效。')
    aid = create_action('call_summary', app, params, None, None, text[:60000], card,
                        [('✅ 寫進卡片', 'cop_ok'), ('✖ 不要', 'cop_no')])
    print(f'✅ 已貼出確認卡（{aid}）。顧問按「寫進卡片」才會存。')


def cmd_book(a):
    app = resolve(a.who)
    w = who(); need_where(w)
    t = parse_when(a.at)
    if t < now_tpe():
        die(f'{fmt(t)} 已經過了')
    rem = t - dt.timedelta(minutes=a.before)
    params = {'call_at': fmt(t), 'remind_at': fmt(rem), 'note': a.note or ''}
    card = (f'⏰ 確認記下電話時間\n人選：{app["name"]}（{app.get("job_title") or "—"}）\n電話時間：{fmt(t)}\n'
            f'提醒：{fmt(rem)} 私訊 {w["name"] or "你"}（私訊不到就發在這裡）\n' + (f'備註：{a.note}\n' if a.note else '') +
            '─' * 14 + '\n按「✅ 記下」才會寫進人選卡片（電洽預約）並排提醒。不會寄任何東西給人選。')
    aid = create_action('book_call', app, params, None, None, None, card, [('✅ 記下並提醒', 'cop_ok'), ('✖ 不要', 'cop_no')])
    print(f'✅ 已貼出確認卡（{aid}）。電話 {fmt(t)}，提醒 {fmt(rem)}。顧問按「記下並提醒」才會存。')


def cmd_remind(a):
    """只是提醒顧問自己（不寫人選卡片、不對外），直接建立。"""
    w = who(); need_where(w)
    t = parse_when(a.at)
    rem = t - dt.timedelta(minutes=a.before)
    if rem < now_tpe() - dt.timedelta(minutes=1):
        die(f'提醒時間 {fmt(rem)} 已經過了')
    app = resolve(a.app) if a.app else {'id': None, 'name': None}
    rid = 'rem' + secrets.token_hex(6)
    d1("""INSERT INTO consultant_reminders (id,application_id,candidate_name,call_at,remind_at,message,target_chat_id,
             fallback_chat_id,fallback_thread_id,requested_by,requested_tg_id,status,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',?)""",
       [rid, app['id'], app['name'], fmt(t), fmt(rem), (a.text or '')[:500], w['tg_id'], w['chat'], w['thread'] or None,
        w['name'], w['tg_id'], fmt(now_tpe())])
    print(f'✅ 已排提醒（{rid}）：{fmt(rem)} 私訊 {w["name"] or "你"}' + (f'，關於 {app["name"]}' if app['name'] else '') +
          f'（時間到的那一刻起 10 分鐘內會發）。取消：cancel-remind {rid}')


def cmd_reminders(a):
    w = who()
    sql = "SELECT id,call_at,remind_at,candidate_name,message,status,requested_by FROM consultant_reminders WHERE status IN ('pending','sending')"
    p = []
    if not a.all and w['tg_id']:
        sql += ' AND requested_tg_id=?'; p.append(w['tg_id'])
    for r in rows(sql + ' ORDER BY remind_at LIMIT 30', p):
        print(f'・{r["id"]}｜電話 {r["call_at"]}｜提醒 {r["remind_at"]}｜{r.get("candidate_name") or "—"}｜{clip(r.get("message"), 40)}｜{r.get("requested_by")}')
    print('（以上是還沒發的提醒）')


def cmd_cancel_remind(a):
    w = who(); need_where(w)
    r = d1("""UPDATE consultant_reminders SET status='cancelled', cancelled_at=?, cancelled_by=?
               WHERE id=? AND status='pending' AND (requested_tg_id=? OR ? IN ('jackyyuqi','behe10'))""",
           [fmt(now_tpe()), w['name'], a.id, w['tg_id'], w['username']])
    print('✅ 已取消' if (r.get('meta') or {}).get('changes') else '❌ 沒取消到（不存在、已發出，或不是你建的）')


def cmd_pending(a):
    for r in rows("""SELECT id,kind,candidate_name,status,requested_by,created_at,decided_by FROM consultant_ops_actions
                      ORDER BY created_at DESC LIMIT ?""", [a.n]):
        print(f'・{r["id"]}｜{r["kind"]}｜{r["candidate_name"]}｜{r["status"]}｜{r["requested_by"]}｜{r["created_at"]}' + (f'｜按的人 {r["decided_by"]}' if r.get('decided_by') else ''))


def main():
    ap = argparse.ArgumentParser(description='Step1ne 顧問助理工具箱')
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('find', help='查人選'); p.add_argument('who'); p.add_argument('--all', action='store_true', help='同名多筆全部列')
    p.set_defaults(fn=cmd_find)
    p = sp.add_parser('job', help='查職缺與公司近期動態'); p.add_argument('key'); p.set_defaults(fn=cmd_job)
    p = sp.add_parser('today', help='今天/明天的電話'); p.add_argument('--days', type=int, default=2); p.set_defaults(fn=cmd_today)
    p = sp.add_parser('call-mail', help='約電話信（預覽＋確認卡）'); p.add_argument('who'); p.add_argument('--note')
    p.add_argument('--topics', help='逗號分隔：' + ','.join(CALL_TOPICS)); p.add_argument('--topic-extra')
    p.add_argument('--ask-resume', choices=['yes', 'no']); p.set_defaults(fn=cmd_call_mail)
    p = sp.add_parser('call-confirm', help='回信確認電話時間（預覽＋確認卡）'); p.add_argument('who')
    p.add_argument('--slot', required=True, help='信裡寫的時間，例如「10/8（四）下午 2:00」'); p.add_argument('--salutation')
    p.add_argument('--phone'); p.add_argument('--extra'); p.add_argument('--at', help='同時登記電話時間＋提醒，例如 2026-10-08 14:00')
    p.add_argument('--before', type=int, default=30); p.set_defaults(fn=cmd_call_confirm)
    p = sp.add_parser('decline', help='婉拒信（預覽＋確認卡）'); p.add_argument('who')
    p.add_argument('--reason', default='fit', choices=list(DECLINE_REASONS)); p.add_argument('--extra')
    p.add_argument('--no-keep', action='store_true'); p.add_argument('--subject'); p.add_argument('--text-file')
    p.add_argument('--again', action='store_true'); p.set_defaults(fn=cmd_decline)
    p = sp.add_parser('summary', help='電洽逐字稿寫進卡片（確認卡）'); p.add_argument('who'); p.add_argument('--file'); p.add_argument('--text')
    p.set_defaults(fn=cmd_summary)
    p = sp.add_parser('book', help='登記約好的電話＋提醒（確認卡）'); p.add_argument('who'); p.add_argument('--at', required=True)
    p.add_argument('--before', type=int, default=30); p.add_argument('--note'); p.set_defaults(fn=cmd_book)
    p = sp.add_parser('remind', help='只提醒自己（不寫卡片）'); p.add_argument('--at', required=True); p.add_argument('--text', default='')
    p.add_argument('--app'); p.add_argument('--before', type=int, default=30); p.set_defaults(fn=cmd_remind)
    p = sp.add_parser('reminders', help='還沒發的提醒'); p.add_argument('--all', action='store_true'); p.set_defaults(fn=cmd_reminders)
    p = sp.add_parser('cancel-remind'); p.add_argument('id'); p.set_defaults(fn=cmd_cancel_remind)
    p = sp.add_parser('pending', help='最近的確認卡狀態'); p.add_argument('-n', type=int, default=10); p.set_defaults(fn=cmd_pending)
    a = ap.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
