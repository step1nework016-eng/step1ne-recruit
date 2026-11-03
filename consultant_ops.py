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

2026-11-02 Jacky：客戶總表＋小知識庫（AI 一直列出已拿掉的「大樹 軟體專案管理師 PM」、把只有 104 的職缺當成客戶需求）
  * clients：印「主力客戶狀態總表」（D1 client_board，後台「客戶總表」頁同一份）。
    **任何 Claude（Jacky Mac、Phoebe Windows、TG 顧問助理）要列客戶／排職缺／找人之前，先跑這個。**
    「已拿掉／暫停」欄裡的職缺永遠不准再列、不准找人；只有「✅ 客戶確認」的職缺才花時間找人。
  * client-set：Jacky／Phoebe 明確講要改時才用（例如「大樹 PM 拿掉」），每次修改都會留紀錄。
  * kb <關鍵字…>：一次搜書櫃（合約／公司簡介全文）、客戶總表、客戶備註、未簽約職缺備註、
    近 60 天信件（official@＋jackychen@）、人選備註。每筆附出處。
  ⚠️ 硬規則：回答合約條款、價格、期限、客戶條件，一定附出處（表名＋日期＋id）；
     kb 查不到就說「查不到」，不准推測、不准用印象補。總表跟其他資料打架時，以總表為準並講出來。
"""
import argparse, datetime as dt, json, os, re, secrets, subprocess, sys, urllib.error, urllib.parse, urllib.request

HOME = os.path.expanduser('~')
CFG = f'{HOME}/.config/workflow-os'
DB_ID = '67077b4b-42d9-4086-8d04-b3658a89cffd'   # step1ne-recruit
BACKOFFICE = 'https://step1ne-backoffice-worker.aiagentg888.workers.dev'
UA = 'Mozilla/5.0 (Macintosh; step1ne-consultant-ops)'   # CF 會擋 urllib 預設 UA
TZ = dt.timezone(dt.timedelta(hours=8))
EXPIRE_H = 24
# TG username → 顧問顯示名（信件署名用）。跟 consultants 表 / BD_APPROVERS 對齊。
TG_TO_CONSULTANT = {'jackyyuqi': 'Jacky', 'behe10': 'Phoebe'}
# applications.owner → 確認卡送去哪（私訊）／署名。2026-11-01 面談後自動擬約電話信用。
OWNER_TG = {'jacky': {'tg_id': '8365775688', 'username': 'jackyyuqi', 'name': 'Jacky'},
            'phoebe': {'tg_id': '6326743721', 'username': 'behe10', 'name': 'Phoebe'}}
DECLINE_REASONS = {'fit': '條件有落差', 'employer': '用人單位這次選了其他方向',
                   'filled': '職缺已有人選進最後階段', 'paused': '職缺暫停招募'}
CALL_TOPICS = {'will': '意願與想了解的地方', 'pay': '期望待遇、到職時間', 'gap': '面談還沒聊完的細節',
               'lang': '外語簡單聊幾句', 'now': '目前工作狀況與換工作時間', 'work': '最近工作實際內容',
               'lead': '帶人經驗', 'place': '地點／通勤／出差', 'flow': '面試流程與時間', 'other': '其他想了解的職缺方向'}
# 候選人看得到的文字：最後一道本機檢查（後台與 Worker 也各擋一次）
BANNED = re.compile(r'客戶|年齡|\d+\s*歲|歲以[上下]|性別|男性|女性|男生|女生|已婚|未婚|懷孕|生育|小孩|外貌|身高|體重|國籍|宗教|政治')


try:  # 2026-10-23 F002：具名權杖優先（STEP1NE_API_TOKEN），沒有就照舊讀 RECRUIT_ADMIN_TOKEN
    import api_token as _api_token
except Exception:
    _api_token = None


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
    tok = (_api_token.new() if _api_token else None) or _TOK.get('RECRUIT_ADMIN_TOKEN')
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
# 2026-10-29 Jacky：Phoebe 在自己電腦的 Claude Code 跑這支（不是從 TG 顧問助理叫），沒有 COPS_* 環境變數，
# 寄信／確認卡都被 need_where 擋下，她的 Claude 又不肯自己設 TG 身分（覺得是冒用）。
# 改成：沒有環境變數時，依「這台電腦的使用者帳號」認人——這就是本人在自己電腦上操作，不是冒用。
LOCAL_USERS = {
    'jessi': {'tg_id': '6326743721', 'username': 'behe10', 'chat': '6326743721'},      # Phoebe 的 Windows
    'user': {'tg_id': '8365775688', 'username': 'jackyyuqi', 'chat': '8365775688'},    # Jacky 的 Mac
}


def who():
    tg_id = os.environ.get('COPS_TG_ID', '').strip()
    uname = os.environ.get('COPS_TG_USERNAME', '').strip().lower()
    name = os.environ.get('COPS_TG_NAME', '').strip()
    chat = os.environ.get('COPS_CHAT', '').strip()
    thread = os.environ.get('COPS_THREAD', '').strip()
    # 2026-11-01 修：以前本機帳號認人（Mac＝jackyyuqi）會蓋掉 COPS_TG_NAME，署名永遠是 Jacky
    # （10/31 曾詩晴負責人是 Phoebe，信卻署名 Jacky）。順序改成：TG 帳號對照 → 明確給的 COPS_TG_NAME → 本機帳號。
    display = TG_TO_CONSULTANT.get(uname) or name
    if not tg_id:
        loc = LOCAL_USERS.get(re.split(r'[\\/]', HOME.rstrip('/\\'))[-1].lower())
        if loc:
            tg_id, uname, chat = loc['tg_id'], loc['username'], chat or loc['chat']
    display = display or TG_TO_CONSULTANT.get(uname) or uname or ''
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
    _ow = rows('SELECT owner FROM applications WHERE id=?', [ap.get('id')])
    print(f'負責顧問：{((_ow[0].get("owner") or "") if _ow else "").capitalize() or "還沒指派"}')
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
    me_ = (who().get('name') or '').lower()
    allv = bool(getattr(a, 'all', False))
    hidden = 0
    for r in rows("""SELECT r.call_at, r.candidate_name, r.application_id, r.requested_by, r.message, r.status, r.id
                       FROM consultant_reminders r WHERE r.call_at>=? AND r.call_at<? AND r.status IN ('pending','sending','sent')
                       ORDER BY r.call_at""", [lo, hi]):
        # 2026-11-02 Jacky：有指派的只給負責的那位看；沒指派的大家都看得到
        if not allv and me_ and (r.get('requested_by') or '').lower() not in ('', me_):
            hidden += 1
            continue
        items.append((r['call_at'], f'☎️ {r.get("candidate_name") or "（沒掛人選）"}｜{r.get("requested_by") or "—"}'
                      + (f'｜{clip(r.get("message"), 50)}' if r.get('message') else '') + f'｜提醒 {r["status"]}（{r["id"]}）'))
    for r in rows("""SELECT i.confirmed_slot, i.stage, i.location, a.name, a.job_title, i.created_by, LOWER(COALESCE(a.owner,'')) AS owner
                       FROM interview_appointments i JOIN applications a ON a.id=i.application_id
                      WHERE i.status='confirmed' AND i.confirmed_slot>=? AND i.confirmed_slot<? ORDER BY i.confirmed_slot""", [lo, hi]):
        if not allv and me_ and r.get('owner') not in ('', me_):
            hidden += 1
            continue
        lbl = '顧問電話' if r.get('stage') == 0 else f'第{r.get("stage")}關面試'
        items.append((r['confirmed_slot'], f'📅 {r["name"]}｜{lbl}｜{r.get("job_title") or ""}' + (f'｜{r["location"]}' if r.get('location') else '')))
    items.sort(key=lambda x: str(x[0]))
    print(f'{lo[:10]} 起 {a.days} 天內的電話／面試：')
    if not items:
        print('（沒有排定的）')
    for t, s in items:
        print(f'・{str(t)[5:16]}　{s}')
    if hidden:
        print(f'（另有 {hidden} 筆是其他顧問負責的，沒列；要看全部加 --all）')
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


def create_action(kind, app, params, to, subject, text, card_text, buttons, w=None, only_if_none=None, also_chats=()):
    """w：誰提出、卡片貼哪（不給就用 who()）。
    only_if_none：kind 清單；這位人選已經有這些 kind 的確認卡（任何狀態）就不建、回 None——
    先占位再貼卡，兩台機器同時跑也只會出一張。
    also_chats：同一張卡再貼到這些私訊（按鈕是同一個編號，Worker 先搶狀態，誰先按都只會寄一次）。"""
    w = w or who()
    need_where(w)
    aid = 'cop' + secrets.token_hex(6)
    now = now_tpe()
    vals = [aid, kind, app['id'], app['name'], json.dumps(params, ensure_ascii=False), to, subject, text,
            w['name'], w['tg_id'], w['username'], w['chat'], w['thread'] or None, fmt(now), fmt(now + dt.timedelta(hours=EXPIRE_H))]
    cols = """INSERT INTO consultant_ops_actions (id,kind,application_id,candidate_name,params_json,preview_to,preview_subject,preview_text,
             requested_by,requested_tg_id,requested_tg_username,chat_id,thread_id,status,created_at,expires_at)"""
    if only_if_none:
        ph = ','.join('?' * len(only_if_none))
        r = d1(cols + f""" SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?
                WHERE NOT EXISTS (SELECT 1 FROM consultant_ops_actions WHERE application_id=? AND kind IN ({ph}))""",
               vals + [app['id']] + list(only_if_none))
        if not (r.get('meta') or {}).get('changes'):
            return None
    else:
        d1(cols + " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)", vals)
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
    for ch in also_chats:
        if ch and str(ch) != str(w['chat']):
            r2 = tg('sendMessage', {'chat_id': ch, 'text': card_text[:4000], 'reply_markup': kb, 'disable_web_page_preview': True})
            if not r2.get('ok'):
                print(f'⚠️ 另一份確認卡貼不到 {ch}：{r2.get("description")}')
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


def owner_of(app_id):
    """人選的負責顧問：回傳 (owner id, 顯示名)；沒有就 (None, None)。"""
    r = rows("""SELECT a.owner, c.display_name FROM applications a LEFT JOIN consultants c ON c.id=a.owner WHERE a.id=?""", [app_id])
    if not r or not r[0].get('owner'):
        return None, None
    o = r[0]['owner']
    return o, (OWNER_TG.get(o) or {}).get('name') or r[0].get('display_name') or None


def call_mail_draft(app, by, note='', topics=None, topic_extra=None, ask_resume=None):
    """跟後台要約電話信草稿＋檢查。手動（call-mail 指令）跟面談後自動（auto_call_mail.py）共用這一份。
    回傳 (send_params, 草稿 d, 卡片附註 extra)；不能寄就 die。"""
    params = {'action': 'preview', 'by': by, 'note': note or ''}
    if topics:
        params['topics'] = topics
    if topic_extra:
        params['topic_extra'] = topic_extra
    if ask_resume is not None:
        params['ask_resume'] = ask_resume
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
    return send_params, d, extra


def cmd_call_mail(a):
    app = resolve(a.who)
    w = who(); need_where(w)
    keys = None
    if a.topics:
        keys = [x.strip() for x in a.topics.split(',') if x.strip()]
        bad = [k for k in keys if k not in CALL_TOPICS]
        if bad:
            die(f'不認得的主題代號：{bad}；可用：{CALL_TOPICS}')
    # 2026-11-01：署名跟負責顧問一致（--by 指定 ＞ 負責顧問 ＞ 下指令的人 ＞ Jacky）
    by = (a.by or '').strip() or owner_of(app['id'])[1] or w['name'] or 'Jacky'
    send_params, d, extra = call_mail_draft(app, by, a.note, keys, a.topic_extra,
                                            (a.ask_resume == 'yes') if a.ask_resume in ('yes', 'no') else None)
    card = card_head('確認寄出｜約電話信', app, d['to'], d['subject']) + d['text'] + '\n' + card_tail(w, '\n'.join(extra))
    # 2026-11-02 Jacky：約電話信的確認卡一律也送一份到 Jacky 私訊（他自己下的指令就不重複）
    jk = OWNER_TG['jacky']
    also = () if w['username'] == jk['username'] else (jk['tg_id'],)
    aid = create_action('call_mail', app, send_params, d['to'], d['subject'], d['text'], card,
                        [('✅ 確認寄出', 'cop_ok'), ('✖ 不寄了', 'cop_no')], also_chats=also)
    print(f'負責顧問：{by}' + ('（確認卡也送一份到 Jacky 私訊）' if also else ''))
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


def cmd_free_mail(a):
    """自由信件（2026-10-14 Jacky：Phoebe 的 Claude 也要能寄信給客戶、人選）。
    只產生確認卡；Jacky／Phoebe／提出的顧問在 TG 按「確認寄出」才會從 official@ 寄出。可附檔（pdf、word、excel、ppt、圖片）。"""
    import base64
    import mimetypes
    w = who()
    if not a.now:
        need_where(w)
    subject = (a.subject or '').strip()
    text = open(a.text_file, encoding='utf-8').read().strip() if a.text_file else (a.text or '').strip()
    if not subject or not text:
        die('主旨和內文都要有（--subject、--text-file 或 --text）')
    app = {'id': '', 'name': a.company or a.to, 'job_title': '', 'job_slug': ''}
    if a.who:
        app = resolve(a.who)
    if not a.to:
        # 2026-10-19 Jacky：寄給人選不要再叫人去查信箱，直接用卡片上的 Email
        em = rows('SELECT email FROM applications WHERE id=?', [app['id']]) if app.get('id') else []
        a.to = (em[0].get('email') or '').strip() if em else ''
        if not a.to:
            die('沒有收件信箱：請加 --to，或用 --who 指定一位卡片上有 Email 的人選')
    # 日期跟星期對不上、客戶名寫錯，在貼確認卡之前就擋（寄信程式寄出前也會再擋一次）
    try:
        from weekday_check import weekday_errors
        from client_name_check import client_name_errors
        e1 = weekday_errors(subject + '\n' + text)
        if e1:
            die('星期寫錯了：' + '；'.join(e1))
        e2 = client_name_errors(subject + '\n' + text)
        if e2:
            die('客戶名稱寫法不對：' + '；'.join(e2))
    except SystemExit:
        raise
    except Exception:
        pass
    if app.get('id'):
        prob = check_candidate_text(app['id'], subject + '\n' + text)
        if prob:
            die(prob)
    atts = []
    for path in (a.attach or []):
        path = os.path.expanduser(path)
        if not os.path.isfile(path):
            die(f'找不到附件：{path}')
        mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        atts.append({'name': os.path.basename(path), 'mime': mime, 'b64': base64.b64encode(open(path, 'rb').read()).decode()})
    d = bo('/admin/free-mail', {'action': 'draft', 'to': a.to, 'cc': a.cc or '', 'subject': subject, 'text': text, 'attachments': atts})
    if not d.get('ok'):
        die(d.get('error') or '產生草稿失敗')
    files = d.get('files') or []
    # 2026-10-19：--file-id 直接附系統裡已存在的檔案（例如公司簡介 PROFILE_FILE_ID），不用每次重新上傳一份
    for fid in (a.file_id or []):
        fid, _, fname = fid.partition(':')
        fr = rows('SELECT id, filename, mime FROM files WHERE id=?', [fid.strip()])
        if not fr:
            die(f'系統裡找不到這個檔案：{fid}')
        files.append({'file_id': fr[0]['id'], 'filename': fname.strip() or fr[0]['filename'] or 'attachment.pdf',
                      'mime': fr[0]['mime'] or 'application/octet-stream'})
    # 2026-10-29 Jacky：Phoebe 的 Claude 也要能把「系統做好的匿名履歷」附給客戶——用人選姓名／編號就好，不用自己讀 104 PDF
    for key in (a.anon or []):
        ap = resolve(key)
        ar = rows('SELECT code, file_id, filename FROM candidate_anon_codes WHERE application_id=?', [ap['id']])
        if not ar or not ar[0].get('file_id'):
            die(f'{ap["name"]} 的匿名履歷還沒做好：先跑 anon {key}，等 TG「客戶履歷人工確認」按確認、PDF 產出後再寄')
        files.append({'file_id': ar[0]['file_id'], 'filename': ar[0].get('filename') or f'匿名人選推薦_{ar[0]["code"]}.pdf',
                      'mime': 'application/pdf'})
    if len(files) > 5:
        die('附件最多 5 個')
    if a.now:
        # 2026-10-29 Jacky：Phoebe 明確說「寄」就直接寄，不用再按確認卡（檢查一樣照跑：星期、客戶名、人選文字）
        r = bo('/admin/free-mail', {'action': 'send', 'to': d['to'], 'cc': ','.join(d.get('cc') or []), 'subject': d['subject'], 'text': text,
                                    'files': files, 'by': w['name'] or '顧問', 'from_name': a.from_name or '', 'html': (open(a.html_file).read() if a.html_file else ''), 'application_id': app.get('id') or None, 'company': a.company or ''})
        if not r.get('ok'):
            die(r.get('error') or '寄送失敗')
        print(f'✅ 已送出寄信（從 official@，約 1 分鐘內寄出並存寄件備份）。\n收件：{d["to"]}\n主旨：{d["subject"]}' + (f'\n附件：{"、".join(f["filename"] for f in files)}' if files else ''))
        return
    params = {'cc': ','.join(d.get('cc') or []), 'files': files, 'company': a.company or '', 'by': w['name'], 'from_name': a.from_name or '', 'html': (open(a.html_file).read() if a.html_file else '')}
    who_line = f'人選：{app["name"]}（{app.get("job_title") or app.get("job_slug") or "—"}）\n' if app.get('id') else (f'公司：{a.company}\n' if a.company else '')
    card = ('📧 確認寄出｜自由信件\n' + who_line + f'收件：{d["to"]}\n' + (f'副本：{", ".join(d["cc"])}\n' if d.get('cc') else '') +
            f'主旨：{d["subject"]}\n' + (f'附件：{"、".join(f["filename"] for f in files)}\n' if files else '') + '─' * 14 + '\n' + text + '\n' +
            card_tail(w, '寄出後約 1 分鐘內從 official@ 寄出，並存進寄件備份。'))
    if a.card_note:
        card = a.card_note.strip() + '\n' + card
    aid = create_action('free_mail', app, params, d['to'], d['subject'], text, card, [('✅ 確認寄出', 'cop_ok'), ('✖ 不寄了', 'cop_no')])
    print(f'✅ 已貼出確認卡（{aid}），還沒寄。\n收件：{d["to"]}\n主旨：{d["subject"]}' + (f'\n附件：{len(files)} 個' if files else ''))


def cmd_anon(a):
    """製作匿名履歷（2026-10-29 Jacky：Phoebe 的 Claude 也要能做）。走系統標準流程：後台讀人選卡片上的履歷→AI 改寫匿名版
    →草稿貼 TG「客戶履歷人工確認」→ 顧問按確認才產 PDF。Claude 不用、也不要自己讀 104 PDF 內容。"""
    w = who()
    ap = resolve(a.who)
    ex = rows('SELECT code, file_id, filename FROM candidate_anon_codes WHERE application_id=?', [ap['id']])
    if a.status:
        if not ex:
            print(f'{ap["name"]}：還沒做過匿名履歷'); return
        print(f'{ap["name"]}：代號 {ex[0]["code"]}，' + (f'PDF 已產出（{ex[0].get("filename") or ex[0]["file_id"]}），可用 free-mail --anon "{a.who}" 附給客戶'
              if ex[0].get('file_id') else 'PDF 還沒產出（等 TG 確認）')); return
    body = {'anonymous': True, 'by': w.get('name') or '顧問'}
    if a.job:
        body['job_slug'] = a.job
    r = bo(f'/admin/application/{urllib.parse.quote(ap["id"])}/client-formal-report', body)
    if not r.get('ok'):
        die(r.get('error') or '送出失敗')
    code = rows('SELECT code FROM candidate_anon_codes WHERE application_id=?', [ap['id']])
    print(f'✅ 已送出 {ap["name"]} 的匿名履歷（代號 {code[0]["code"] if code else "產生中"}）。約 1～3 分鐘後草稿貼到 TG「客戶履歷人工確認」，按確認才產 PDF。'
          f'\n之後寄給客戶：free-mail --company 公司 --to 客戶信箱 --subject … --text-file … --anon "{a.who}"')


def _anon_req(ap):
    r = rows("""SELECT id, status, ai_job_id, synthetic_content_json FROM client_report_requests
                 WHERE application_id=? AND anon_code IS NOT NULL ORDER BY requested_at DESC LIMIT 1""", [ap['id']])
    if not r:
        die(f'{ap["name"]} 還沒做過匿名履歷，先跑 anon {ap["name"]}')
    return r[0]


def cmd_anon_edit(a):
    """改匿名履歷（2026-10-29 Jacky：Phoebe 說要怎麼改就直接改）。等同在 TG 回覆草稿打修改意見：
    把修改意見＋前一版內容交給 AI 重新整理，1～3 分鐘後新版草稿貼 TG；PDF 已產出過的也能改（確認後覆蓋舊 PDF）。"""
    ap = resolve(a.who)
    req = _anon_req(ap)
    if req['status'] in ('awaiting_synthesis', 'pending'):
        die('上一版還在整理中，等新版草稿出來再改')
    old = rows('SELECT payload_json FROM ai_jobs WHERE id=?', [req['ai_job_id']]) if req.get('ai_job_id') else []
    try:
        payload = json.loads(old[0]['payload_json']) if old else {}
    except Exception:
        payload = {}
    payload['edit_instruction'] = a.instruction.strip()
    try:
        payload['previous_output'] = json.loads(req.get('synthetic_content_json') or '{}')
    except Exception:
        payload['previous_output'] = None
    jid = secrets.token_hex(16)
    d1("INSERT INTO ai_jobs (id, kind, payload_json, status, created_at) VALUES (?, 'client_report_synthesize', ?, 'pending', ?)",
       [jid, json.dumps(payload, ensure_ascii=False), fmt(now_tpe())])
    d1("UPDATE client_report_requests SET status='awaiting_synthesis', ai_job_id=? WHERE id=?", [jid, req['id']])
    print(f'✅ 已送出修改：{ap["name"]}（{clip(a.instruction, 60)}）。約 1～3 分鐘後新版草稿貼到 TG「客戶履歷人工確認」；'
          f'沒問題就 anon-confirm {a.who} 產 PDF。')


def cmd_anon_confirm(a):
    """確認匿名履歷草稿、產 PDF（等同 TG 按「✅ 確認，產出PDF」）。"""
    ap = resolve(a.who)
    req = _anon_req(ap)
    if req['status'] != 'awaiting_confirm':
        die(f'現在狀態是 {req["status"]}，不是等確認（還在整理就等草稿出來；已產出可直接寄）')
    d1("UPDATE client_report_requests SET status='confirmed' WHERE id=?", [req['id']])
    print(f'✅ 已確認 {ap["name"]} 的匿名履歷，約 1～3 分鐘產出 PDF；之後 anon {a.who} --status 看好了沒，再用 free-mail --anon 附給客戶。')


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
    # 2026-10-12：沒掛人選卡（例如 LINE 來還沒建卡）的電話，candidate_name 空著會被發財基地行事曆同步略過——
    # 用 --name 帶名字（人選名＋職缺），行事曆才會出現。
    if not app.get('name') and getattr(a, 'name', None):
        app = {**app, 'name': a.name}
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


def cmd_match(a):
    """從現有人選配對（等同後台客戶卡片按鈕）。--company/--job 開始；--run 查結果；--brief 對選定人選產匿名摘要。"""
    who_ = os.environ.get('COPS_TG_NAME', '') or os.environ.get('COPS_TG_USERNAME', '')
    if a.company and a.job:
        r = bo('/admin/bd/job-match', {'company': a.company, 'job_slug': a.job, 'by': who_})
        if not r.get('ok'):
            die(f'配對沒啟動：{r.get("error")}')
        print(f'✅ 已排進 AI 配對（run_id：{r["run_id"]}）。約 3～8 分鐘，之後用 --run {r["run_id"]} 看結果。')
        return
    if a.brief:
        # 匿名履歷一律走既有標準流程（做出 A-01 範本那套）：AI 整理→草稿貼 TG「客戶履歷人工確認」→ 顧問確認才產 PDF。
        # 不再用自己發明的格式（2026-10-14 Jacky：舊格式含「與職缺對照／需確認」，不能給未簽約客戶看）。
        ids = [x.strip() for x in a.brief.split(',') if x.strip()]
        for aid in ids:
            r = bo(f'/admin/application/{urllib.parse.quote(aid)}/client-formal-report', {'anonymous': True, 'by': who_ or '顧問'})
            print(f'・{aid}：' + ('✅ 已送出，草稿 1～2 分鐘後貼到 TG「客戶履歷人工確認」，確認後才產 PDF' if r.get('ok') else f'❌ {r.get("error")}'))
        return
    if a.run:
        r = bo(f'/admin/bd/job-match?run_id={urllib.parse.quote(a.run)}')
        if not r.get('ok'):
            die(f'查不到：{r.get("error")}')
        run = r['run']
        print(f'{run["company"]}｜{run["job_title"]}｜狀態：{run["status"]}')
        if run['status'] != 'done':
            print('還沒好，過幾分鐘再查。' + (f' 錯誤：{run.get("error")}' if run.get('error') else ''))
            return
        res = r.get('result') or {}
        if not res.get('picks'):
            print(res.get('note') or '沒有合適的人選')
        for p in res.get('picks', []):
            print(f'\n{p["code"]} {p["name"]}｜適合度 {p["fit"]}｜{p["status"]}｜期望 {p["expected"]}｜可到職 {p["available"]}｜application_id {p["application_id"]}')
            for m in p['matches']: print(f'   符合：{m}')
            for g in p['gaps']: print(f'   落差：{g}')
            if p.get('note'): print(f'   備註：{p["note"]}')
        for b in r.get('briefs', []):
            print(f'\n匿名摘要 {b["code"]}：{b["status"]}' + (f'（{b["filename"]}）' if b.get('filename') else ''))
        return
    die('用法：--company 公司 --job 職缺slug 開始；--run ID 看結果；--brief 人選application_id,… 產匿名履歷')


# ── 2026-11-02 客戶總表（唯一真相）＋小知識庫 ──
KB_RULE = ('⚠️ 規則：回答合約條款、價格、期限、客戶條件一定附上面的出處；查不到就說查不到，不准推測。'
           '客戶總表跟其他資料不一致時，以客戶總表為準，並把不一致講出來。')
PRIVATE_BOX = 'jackychen@step1ne.com'


def _board(include_archived=False):
    # 沒有資料庫金鑰的電腦（例如 Phoebe 的 Windows）改走後台 API，同一份資料
    if not (_CF.get('CLOUDFLARE_API_TOKEN') and _CF.get('CLOUDFLARE_ACCOUNT_ID')):
        res = bo('/admin/client-board')
        if not res.get('ok'):
            die(res.get('error') or '讀不到客戶總表')
        return [r for r in res['items'] if include_archived or not r.get('archived')]
    rs = rows('SELECT * FROM client_board' + ('' if include_archived else ' WHERE COALESCE(archived,0)=0') + ' ORDER BY archived, sort, name')
    for r in rs:
        for k, col in (('jobs', 'jobs_json'), ('removed', 'removed_json')):
            try:
                r[k] = json.loads(r.get(col) or '[]')
            except ValueError:
                r[k] = []
    return rs


def _nz(t):
    return re.sub(r'[\s（）()【】「」\[\]・·,，、/]+', '', str(t or '')).lower()


def _board_print(r, full=True):
    print(f'■ {r["name"]}　（出處：client_board {r["id"][:8]}，最後修改 {str(r.get("updated_at") or "")[:16]} {r.get("updated_by") or ""}）')
    print(f'   合約：{r.get("contract") or "—"}')
    print(f'   窗口：{r.get("contacts") or "—"}')
    if r['jobs']:
        print('   找人中的職缺：')
        for j in r['jobs']:
            mark = '✅ 客戶確認' if j.get('confirmed') else '❓ 未經客戶確認（不要花時間找人）'
            print(f'     ・{j["title"]}｜{mark}' + (f'｜{j["date"]}' if j.get('date') else '') + (f'｜{j["note"]}' if j.get('note') and full else '')
                  + (f'\n        確認來源：{j["source"]}' if j.get('source') and full else ''))
    else:
        print('   找人中的職缺：無（需求未確認，先不要找人）')
    if r['removed']:
        print('   ⛔ 已拿掉／暫停（不准再列、不准找人）：')
        for j in r['removed']:
            st = '已拿掉' if j.get('status') == 'removed' else '暫停'
            print(f'     ・{j["title"]}｜{st}｜{j.get("by") or "?"} {j.get("date") or ""}' + (f'｜{j["reason"]}' if j.get('reason') else ''))
    print(f'   管道限制：{r.get("channel_rules") or "—"}')
    print(f'   下一步：{r.get("next_step") or "—"}')
    if r.get('note') and full:
        print(f'   備註：{r["note"]}')


def cmd_clients(a):
    if a.log:
        b = _board_pick(a.log, True)
        if not _CF.get('CLOUDFLARE_API_TOKEN'):
            for x in (bo(f'/admin/client-board/{urllib.parse.quote(b["id"])}/log').get('items') or [])[:a.n]:
                print(f'・{x["changed_at"]}｜{x["changed_by"]}｜{x["field"]}：{clip(x["old_value"], 160) or "（空）"} → {clip(x["new_value"], 200) or "（空）"}')
            return
        for x in rows('SELECT * FROM client_board_log WHERE board_id=? ORDER BY changed_at DESC LIMIT ?', [b['id'], a.n]):
            print(f'・{x["changed_at"]}｜{x["changed_by"]}｜{x["field"]}：{clip(x["old_value"], 160) or "（空）"} → {clip(x["new_value"], 200) or "（空）"}')
        return
    rs = _board(a.all)
    if a.name:
        rs = [r for r in rs if _board_match(r, a.name)]
        if not rs:
            die(f'客戶總表沒有「{a.name}」')
    print(f'主力客戶狀態總表（唯一真相；後台「客戶總表」頁同一份；讀取時間 {fmt(now_tpe())}）')
    print('規則：⛔ 欄的職缺永遠不准再列／找人；只有 ✅ 客戶確認的職缺才花時間找人；管道限制要遵守。\n')
    for r in rs:
        _board_print(r, full=not a.short)
        print()


def _board_match(r, key):
    k = _nz(key)
    return bool(k) and any(k in _nz(x) or (_nz(x) and _nz(x) in k) for x in [r['name']] + str(r.get('aliases') or '').split('\n') if x.strip())


def _board_pick(key, include_archived=False):
    rs = [r for r in _board(include_archived) if _board_match(r, key)]
    if not rs:
        die(f'客戶總表沒有「{key}」')
    if len(rs) > 1:
        die('對到好幾家：' + '、'.join(r['name'] for r in rs) + '，請打完整一點')
    return rs[0]


def cmd_client_set(a):
    b = _board_pick(a.client, True)
    w = who()
    by = (w.get('name') or w.get('username') or '').strip()
    if not by:
        die('不知道是誰在改（修改一定要留名字）')
    by = f'{by}（經 AI）'
    f = {}
    for opt, col in (('contract', 'contract'), ('contacts', 'contacts'), ('channel', 'channel_rules'), ('next', 'next_step'), ('note', 'note')):
        v = getattr(a, opt)
        if v is not None:
            f[col] = v
    jobs, removed = list(b['jobs']), list(b['removed'])
    today = now_tpe().strftime('%Y-%m-%d')
    if a.add_job:
        if any(_nz(j['title']) == _nz(a.add_job) for j in jobs):
            die(f'「{a.add_job}」已經在找人中的職缺裡')
        jobs.append({'title': a.add_job, 'confirmed': bool(a.confirmed), 'source': a.source or '', 'date': a.date or today, 'note': a.job_note or ''})
        removed = [j for j in removed if _nz(j['title']) != _nz(a.add_job)]
    for title, st in ((a.drop_job, 'removed'), (a.pause_job, 'paused')):
        if not title:
            continue
        hit = [j for j in jobs if _nz(title) in _nz(j['title'])]
        if len(hit) > 1:
            die('對到好幾個職缺：' + '、'.join(j['title'] for j in hit))
        name = hit[0]['title'] if hit else title
        jobs = [j for j in jobs if j['title'] != name]
        removed = [j for j in removed if _nz(j['title']) != _nz(name)]
        removed.append({'title': name, 'status': st, 'by': w.get('name') or by, 'date': today, 'reason': a.reason or ''})
    if a.confirm_job:
        hit = [j for j in jobs if _nz(a.confirm_job) in _nz(j['title'])]
        if len(hit) != 1:
            die('要確認的職缺對不到（或對到好幾個）')
        if not a.source:
            die('標成客戶確認一定要給 --source（客戶怎麼確認的：哪封信／哪通電話）')
        hit[0].update({'confirmed': True, 'source': a.source, 'date': a.date or today})
    if jobs != b['jobs']:
        f['jobs'] = jobs
    if removed != b['removed']:
        f['removed'] = removed
    if not f:
        die('沒有要改的東西')
    r = bo(f'/admin/client-board/{urllib.parse.quote(b["id"])}', {'by': by, 'fields': f})
    if not r.get('ok'):
        die(r.get('error') or '改不成功')
    print(f'✅ 已改「{b["name"]}」{r.get("changed", 0)} 個欄位（紀錄：{by}）。現在的樣子：\n')
    _board_print(_board_pick(b['name'], True))


def _kb_terms(q):
    return [t for t in re.split(r'[\s,，、]+', ' '.join(q)) if t]


def _kb_like(cols, terms):
    hay = "(' '||" + "||' '||".join(f"COALESCE({c},'')" for c in cols) + ')'
    return ' AND '.join([f'{hay} LIKE ?'] * len(terms)), [f'%{t}%' for t in terms]


def _snips(text, terms, n=3, width=90):
    t = re.sub(r'[ \t　]+', ' ', str(text or ''))
    flat = re.sub(r'\s+', '', t)
    out, used = [], []
    # 先找最不像公司名的詞（通常是最後一個），找不到再往前
    for term in list(reversed(terms)):
        tk = re.sub(r'\s+', '', term)
        for m in re.finditer(re.escape(tk), flat, re.I):
            if any(abs(m.start() - u) < width for u in used):
                continue
            used.append(m.start())
            s0 = max(0, m.start() - width // 2)
            out.append(('…' if s0 else '') + flat[s0:m.start() + width] + '…')
            if len(out) >= n:
                return out
        if out:
            return out
    return out


def cmd_kb(a):
    terms = _kb_terms(a.q)
    if not terms:
        die('請給關鍵字，例如：kb 大樹 付款')
    if not (_CF.get('CLOUDFLARE_API_TOKEN') and _CF.get('CLOUDFLARE_ACCOUNT_ID')):
        die('這台電腦沒有資料庫權限，kb 跑不了。請在 TG「顧問助理」裡請它查（它會跑同一個 kb），或看後台「客戶總表」頁。不准用推測回答。')
    w = who()
    can_private = w.get('username') in ('jackyyuqi', 'behe10')
    board = _board(True)
    removed_by_co = {}
    for r in board:
        for j in r['removed']:
            removed_by_co.setdefault(r['id'], []).append(j)
    hits = 0

    def head(label, src):
        nonlocal hits
        hits += 1
        print(f'\n【{label}】出處：{src}')

    # ① 客戶總表
    for r in board:
        hay = _nz(' '.join([r['name'], r.get('aliases') or '', r.get('contract') or '', r.get('contacts') or '', r.get('channel_rules') or '',
                            r.get('next_step') or '', r.get('note') or '', json.dumps(r['jobs'], ensure_ascii=False), json.dumps(r['removed'], ensure_ascii=False)]))
        if all(_nz(t) in hay for t in terms):
            head('客戶總表（唯一真相）', f'client_board {r["id"][:8]}，最後修改 {str(r.get("updated_at"))[:16]} {r.get("updated_by") or ""}')
            _board_print(r)
    # ② 書櫃全文
    docs = rows("""SELECT k.file_id, k.title AS ktitle, k.text, k.extracted_at, k.method, d.id AS doc_id, d.title, d.layer, d.is_active, d.uploaded_at, d.note,
                          cc.display_name AS company
                     FROM kb_doc_text k LEFT JOIN doc_library d ON d.file_id = k.file_id LEFT JOIN client_companies cc ON cc.id = d.company_id""")
    for d in docs:
        if d.get('doc_id') and not d.get('is_active') and not d.get('company') and not a.all:
            continue   # 舊版公版不搜（--all 才搜）
        title = d.get('title') or d.get('ktitle') or ''
        hay = _nz(' '.join([title, d.get('company') or '', d.get('note') or '', d.get('text') or '']))
        if not all(_nz(t) in hay for t in terms):
            continue
        tag = '使用中' if d.get('is_active') else ('客戶專用' if d.get('company') else ('舊版' if d.get('doc_id') else '匯入文件'))
        src = (f'書櫃 doc_library {d["doc_id"][:8]}「{title}」（{tag}，上傳 {str(d.get("uploaded_at") or "日期不明")[:10]}）'
               f'，檔案 /admin/file/{d["file_id"]}' if d.get('doc_id') else f'知識庫匯入「{title}」（{d.get("method")}，匯入 {str(d.get("extracted_at"))[:10]}）')
        head('書櫃全文', src)
        if d.get('note'):
            print(f'   書櫃備註：{d["note"]}')
        body_terms = [t for t in terms if _nz(t) in _nz(d.get('text'))] or terms
        for sn in _snips(d.get('text'), body_terms, n=a.n_snip):
            print(f'   ▸ {sn}')
    # ③ 客戶帳號備註
    sql, ps = _kb_like(['display_name', 'aliases', 'note', 'relation_note'], terms)
    for r in rows(f'SELECT id, display_name, relation, note, relation_note, updated_at FROM client_companies WHERE {sql} LIMIT 10', ps):
        head('客戶帳號備註', f'client_companies {r["id"]}「{r["display_name"]}」（關係 {r.get("relation") or "—"}，更新 {str(r.get("updated_at") or "")[:10]}）')
        for sn in _snips((r.get('note') or '') + '\n' + (r.get('relation_note') or ''), terms, n=a.n_snip, width=160):
            print(f'   ▸ {sn}')
    # ④ 未簽約／社群職缺備註
    sql, ps = _kb_like(['so.company', 'so.aliases', 'so.client_note', 'j.title'], terms)
    for r in rows(f"""SELECT so.job_slug, so.company, so.client_note, so.src_kind, so.created_at, j.title, j.status
                        FROM job_social_only so LEFT JOIN jobs j ON j.slug = so.job_slug WHERE {sql} ORDER BY so.created_at DESC LIMIT 15""", ps):
        warn = ''
        for b in board:
            if _board_match(b, r['company']):
                for j in removed_by_co.get(b['id'], []):
                    if _nz(j['title']) in _nz(r['title']) or _nz(r['title']) in _nz(j['title']):
                        warn = f'  ⛔ 客戶總表：{"已拿掉" if j["status"] == "removed" else "暫停"}（{j.get("by")} {j.get("date")}）——以總表為準，不要找人'
                if not warn and not any(_nz(j['title']) in _nz(r['title']) or _nz(r['title']) in _nz(j['title']) for j in b['jobs']):
                    warn = '  ❓ 客戶總表沒有列這個職缺＝客戶沒確認，不要花時間找人'
        head('未簽約職缺（job_social_only）', f'job_social_only {r["job_slug"]}（{r["company"]}，來源 {r.get("src_kind") or "—"}，建立 {str(r["created_at"])[:10]}）')
        print(f'   職缺：{r.get("title")}｜系統狀態 {r.get("status")}{warn}')
        if r.get('client_note'):
            print(f'   ▸ {clip(r["client_note"], 300)}')
    # ⑤ 近 60 天信件
    since = (now_tpe() - dt.timedelta(days=a.days)).strftime('%Y-%m-%d')
    sql, ps = _kb_like(['subject', 'body_text', 'from_addr', 'from_name', 'to_addrs', 'link_name'], terms)
    extra = '' if can_private else ' AND mailbox <> ?'
    ms = rows(f"""SELECT id, mailbox, folder, sent_at, from_addr, to_addrs, subject, body_text FROM mail_archive
                   WHERE sent_at >= ? AND COALESCE(is_system_copy,0)=0 AND {sql}{extra} ORDER BY sent_at DESC LIMIT ?""",
              [since] + ps + ([] if can_private else [PRIVATE_BOX]) + [a.mails])
    for m in ms:
        head('信件', f'mail_archive #{m["id"]}（{m["mailbox"]} {"寄出" if m["folder"] == "sent" else "收到"}，{str(m["sent_at"])[:16]}）')
        print(f'   主旨：{m.get("subject")}｜{m.get("from_addr")} → {clip(m.get("to_addrs"), 80)}')
        body = re.split(r'\n-{3,}\s*Original message|\n\s*On .{5,80} wrote:|\n\s*於 .{5,80}寫道', str(m.get('body_text') or ''), maxsplit=1)[0]
        for sn in _snips(body, terms, n=2, width=140):
            print(f'   ▸ {sn}')
    if not can_private:
        print(f'\n（{PRIVATE_BOX} 的信只有 Jacky／Phoebe 本人查得到，這次沒搜）')
    # ⑥ 人選備註
    sql, ps = _kb_like(['n.content', 'a.name'], terms)
    for r in rows(f"""SELECT n.id, n.application_id, n.type, n.content, n.created_at, n.created_by, a.name
                        FROM candidate_notes n LEFT JOIN applications a ON a.id = n.application_id WHERE {sql} ORDER BY n.created_at DESC LIMIT 10""", ps):
        head('人選備註', f'candidate_notes {r["id"][:8]}（人選 {r.get("name") or "?"}，application_id {r["application_id"]}，{str(r["created_at"])[:10]} {r.get("created_by") or ""}）')
        for sn in _snips(r.get('content'), terms, n=2, width=140):
            print(f'   ▸ {sn}')
    print(f'\n共 {hits} 筆（關鍵字：{"＋".join(terms)}；信件只搜近 {a.days} 天）。')
    if not hits:
        print('❌ 查不到。不准推測——請直接跟顧問說「系統裡查不到」，並問要去哪裡確認。')
    print(KB_RULE)


def cmd_pending(a):
    for r in rows("""SELECT id,kind,candidate_name,status,requested_by,created_at,decided_by FROM consultant_ops_actions
                      ORDER BY created_at DESC LIMIT ?""", [a.n]):
        print(f'・{r["id"]}｜{r["kind"]}｜{r["candidate_name"]}｜{r["status"]}｜{r["requested_by"]}｜{r["created_at"]}' + (f'｜按的人 {r["decided_by"]}' if r.get('decided_by') else ''))


# ── 2026-11-02 阿財 bot 私訊對話（Jacky：顧問直接跟阿財講就更新系統，不用開後台） ──
def cmd_note(a):
    """把顧問口頭回報寫進人選卡片（電洽紀錄／備註）。內部紀錄，不對外寄任何東西。"""
    app = resolve(a.who)
    w = who()
    text = (open(a.text_file, encoding='utf-8').read() if a.text_file else (a.text or '')).strip()
    if not text:
        die('要寫什麼？用 --text')
    kind = a.type or '電洽紀錄'
    d1("INSERT INTO candidate_notes (id, application_id, type, content, created_at, created_by) VALUES (lower(hex(randomblob(8))),?,?,?,datetime('now','+8 hours'),?)",
       [app['id'], kind, text[:6000], w['name'] or '顧問'])
    print(f'✅ 已寫進 {app["name"]}（{app.get("job_title") or app.get("job_slug") or "—"}）的卡片：{kind}｜{clip(text, 80)}')


def _forms_of(app_id):
    return rows("""SELECT jf.id AS job_form_id, jf.name AS form_name, f.company_id, c.display_name AS company,
                          s.status, s.submitted_at
                     FROM candidate_forwards f JOIN applications a ON a.id = f.application_id
                     JOIN job_forms jf ON jf.job_slug = a.job_slug
                     LEFT JOIN client_companies c ON c.id = f.company_id
                     LEFT JOIN candidate_form_submissions s ON s.job_form_id = jf.id AND s.application_id = f.application_id AND s.company_id = f.company_id
                    WHERE f.application_id = ?""", [app_id])


def cmd_form_upload(a):
    """上傳人選填好的人事資料表（客戶專區立刻看得到）。已經交過的要加 --replace 才會換掉。"""
    import base64
    app = resolve(a.who)
    path = os.path.expanduser(a.file)
    if not os.path.isfile(path):
        die(f'找不到檔案：{path}')
    fs = _forms_of(app['id'])
    if a.company:
        fs = [f for f in fs if a.company in (f.get('company') or '')]
    if not fs:
        die(f'{app["name"]} 還沒推薦給任何設定了表單的客戶，沒地方放人事資料表（先推薦、或到職缺頁設定「客戶指定表單」）')
    if len(fs) > 1:
        print('這位人選有好幾份表單，請用 --company 指定是哪一家：')
        for f in fs:
            print(f'  ・{f.get("company")}｜{f["form_name"]}｜{f.get("status") or "還沒寄"}')
        sys.exit(2)
    f = fs[0]
    if f.get('status') == 'submitted' and not a.replace:
        print(f'⚠️ {app["name"]} 給 {f.get("company")} 的「{f["form_name"]}」{str(f.get("submitted_at") or "")[:16]} 已經交過一份。'
              '要換成這份新的，先問顧問確定，再加 --replace 重跑。')
        sys.exit(2)
    r = bo('/admin/candidate-form-submissions/upload', {'application_id': app['id'], 'company_id': f['company_id'], 'job_form_id': f['job_form_id'],
            'file_b64': base64.b64encode(open(path, 'rb').read()).decode(), 'file_name': a.name or os.path.basename(path),
            'file_mime': 'application/pdf' if path.lower().endswith('.pdf') else 'application/octet-stream'})
    if not r.get('ok'):
        die(r.get('error') or '上傳失敗')
    print(f'✅ 已上傳 {app["name"]} 給 {f.get("company")} 的「{f["form_name"]}」{"（換掉舊的）" if f.get("status") == "submitted" else ""}，客戶專區立刻看得到。')


def cmd_resume_upload(a):
    """把履歷檔掛到人選卡片（已經有履歷會換成這份，舊的不會刪）。"""
    import base64
    app = resolve(a.who)
    path = os.path.expanduser(a.file)
    if not os.path.isfile(path):
        die(f'找不到檔案：{path}')
    r = bo(f'/admin/applications/{app["id"]}/resume-upload', {'file_b64': base64.b64encode(open(path, 'rb').read()).decode(),
                                                             'file_name': a.name or os.path.basename(path)})
    if not r.get('ok'):
        die(r.get('error') or '上傳失敗')
    print(f'✅ 已把履歷放進 {app["name"]} 的卡片。')


def cmd_inbox(a):
    """看收件匣（official@，Jacky／Phoebe 另含 jackychen@）。可依人選／公司／寄件人／關鍵字篩。"""
    w = who()
    boxes = ['official@step1ne.com'] + (['jackychen@step1ne.com'] if w.get('username') in ('jackyyuqi', 'behe10') else [])
    where, ps = ["folder='in'", "COALESCE(is_system_copy,0)=0", f"mailbox IN ({','.join('?' * len(boxes))})",
                 "sent_at >= datetime('now','+8 hours',?)"], boxes + [f'-{a.days} days']
    if a.q:
        where.append("(from_addr LIKE ? OR from_name LIKE ? OR subject LIKE ? OR body_text LIKE ? OR link_name LIKE ?)")
        ps += [f'%{a.q}%'] * 5
    rs = rows(f"""SELECT id, mailbox, sent_at, from_name, from_addr, subject, att_count, link_name FROM mail_archive
                   WHERE {' AND '.join(where)} ORDER BY sent_at DESC LIMIT ?""", ps + [a.n])
    if not rs:
        print(f'近 {a.days} 天收件匣沒有符合的信。')
        return
    for r in rs:
        print(f'・#{r["id"]}｜{str(r["sent_at"])[5:16]}｜{r.get("from_name") or ""} <{r["from_addr"]}>｜{r.get("subject") or "（無主旨）"}'
              + (f'｜附件 {r["att_count"]}' if r.get('att_count') else '') + (f'｜對到：{r["link_name"]}' if r.get('link_name') else '')
              + ('' if r['mailbox'].startswith('official') else '｜jackychen@'))
    print('\n看全文：mail-read <#編號>。回信：free-mail --to <對方信箱> --subject "Re: 原主旨"（系統會自動接在原信底下）。')


def cmd_mail_read(a):
    w = who()
    r = rows('SELECT * FROM mail_archive WHERE id=?', [str(a.id).lstrip('#')])
    if not r:
        die('找不到這封信')
    m = r[0]
    if m['mailbox'] == PRIVATE_BOX and w.get('username') not in ('jackyyuqi', 'behe10'):
        die('這封在 Jacky 的私人信箱，只有 Jacky／Phoebe 看得到')
    body = re.split(r'\n-{3,}\s*Original message|\n\s*On .{5,80} wrote:|\n\s*於 .{5,80}寫道', str(m.get('body_text') or ''), maxsplit=1)[0]
    print(f'信箱：{m["mailbox"]}（{"收到" if m["folder"] == "in" else "寄出"}）\n時間：{m["sent_at"]}\n寄件：{m.get("from_name") or ""} <{m["from_addr"]}>\n收件：{m.get("to_addrs")}'
          + (f'\n副本：{m["cc_addrs"]}' if m.get('cc_addrs') else '') + f'\n主旨：{m.get("subject")}'
          + (f'\n附件：{m["att_count"]} 個（存了 {m.get("att_saved") or 0} 個）' if m.get('att_count') else '') + '\n' + '─' * 14 + '\n' + body.strip()[:6000])
    atts = rows('SELECT part_idx, filename, size FROM mail_archive_attachments WHERE archive_id=? ORDER BY part_idx', [m['id']])
    if atts:
        print('─' * 14 + '\n附件（看內容：mail-att #' + str(m['id']) + ' [第幾個]）：')
        for i, x in enumerate(atts, 1):
            print(f'  {i}. {x["filename"]}（{round((x.get("size") or 0) / 1024)} KB）')


def _file_bytes(file_id):
    """用後台金鑰把系統裡的檔案（R2／D1）抓回來。"""
    tok = (_api_token.new() if _api_token else None) or _TOK.get('RECRUIT_ADMIN_TOKEN')
    req = urllib.request.Request(f'{BACKOFFICE}/admin/file/{urllib.parse.quote(str(file_id))}',
                                 headers={'Authorization': f'Bearer {tok}', 'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def _doc_text(path):
    """Word／Excel 直接抽文字（PDF、圖片讓阿財用 Read 看）。"""
    import zipfile
    low = path.lower()
    try:
        if low.endswith('.docx'):
            x = zipfile.ZipFile(path).read('word/document.xml').decode('utf-8', 'ignore')
            x = re.sub(r'</w:p>', '\n', x); x = re.sub(r'</w:tc>', '｜', x)
            return re.sub(r'<[^>]+>', '', x)
        if low.endswith('.xlsx'):
            z = zipfile.ZipFile(path)
            ss = re.findall(r'<t[^>]*>([^<]*)</t>', z.read('xl/sharedStrings.xml').decode('utf-8', 'ignore')) if 'xl/sharedStrings.xml' in z.namelist() else []
            return '｜'.join(ss)
        if low.endswith('.pdf'):
            r = subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True, text=True, timeout=60)
            return r.stdout if r.returncode == 0 else ''
    except Exception as e:
        return f'（抽文字失敗：{e}）'
    return ''


def cmd_mail_att(a):
    """2026-11-02 Jacky：阿財要能看信件附件——下載到阿財的工作資料夾，Word／Excel／PDF 直接印出文字，圖片／掃描檔用 Read 看。"""
    w = who()
    r = rows('SELECT id, mailbox, subject FROM mail_archive WHERE id=?', [str(a.id).lstrip('#')])
    if not r:
        die('找不到這封信')
    m = r[0]
    if m['mailbox'] == PRIVATE_BOX and w.get('username') not in ('jackyyuqi', 'behe10'):
        die('這封在 Jacky 的私人信箱，只有 Jacky／Phoebe 看得到')
    atts = rows('SELECT part_idx, file_id, filename, mime, size FROM mail_archive_attachments WHERE archive_id=? ORDER BY part_idx', [m['id']])
    if not atts:
        die('這封信沒有存下來的附件（可能太大或格式不收）')
    pick = atts if not a.n else [atts[a.n - 1]] if 0 < a.n <= len(atts) else die(f'只有 {len(atts)} 個附件')
    out = os.path.join(os.getcwd(), 'mail_att', str(m['id']))
    os.makedirs(out, exist_ok=True)
    for x in pick:
        name = re.sub(r'[\\/:*?"<>|]', '_', x.get('filename') or f'附件{x["part_idx"]}')
        path = os.path.join(out, name)
        try:
            open(path, 'wb').write(_file_bytes(x['file_id']))
        except Exception as e:
            print(f'❌ {name} 抓不到：{e}')
            continue
        print(f'📎 {name} → {path}')
        t = _doc_text(path).strip()
        if t:
            print('──── 內容（前 5000 字）────\n' + re.sub(r'\n{3,}', '\n\n', t)[:5000])
        else:
            print('（這是圖片或掃描檔，用 Read 打開上面的路徑看）')
    print('\n要交進人選卡片：form-upload <人選> <檔案路徑>（人事資料表）或 resume-upload <人選> <檔案路徑>（履歷）。')


def cmd_tell(a):
    """顧問之間傳話：用阿財 bot 私訊另一位顧問（2026-11-02 Jacky：顧問傳話一律走阿財私訊，不發群組）。"""
    w = who()
    to = rows('SELECT display_name, tg_chat_id FROM consultant_tg WHERE lower(display_name)=lower(?) OR lower(tg_username)=lower(?)', [a.to, a.to.lstrip('@')])
    if not to or not to[0].get('tg_chat_id'):
        die(f'找不到「{a.to}」的阿財私訊（只能傳給已綁定的顧問：' + '、'.join(r['display_name'] for r in rows('SELECT display_name FROM consultant_tg')) + '）')
    if str(to[0]['tg_chat_id']) == str(w.get('chat') or ''):
        die('這是你自己')
    text = (a.text or '').strip()
    if not text:
        die('要傳什麼？用 --text')
    r = tg('sendMessage', {'chat_id': to[0]['tg_chat_id'], 'text': f'💬 {w["name"] or "顧問"} 請阿財傳話：\n{text}\n\n（要回他的話，直接跟我說「回 {w["name"] or "他"}：…」）'})
    if not r.get('ok'):
        die(f'傳不出去：{r.get("description")}')
    print(f'✅ 已私訊 {to[0]["display_name"]}：{clip(text, 60)}')


def cmd_new_card(a):
    """建新的人選卡片（跟後台「＋新增人選」同一條路）。一人一卡：電話或 Email 對到現有人選就不建，回報那張卡。"""
    import base64
    w = who()
    name = (a.name or '').strip()
    if not name:
        die('要填姓名')
    if not (a.phone or a.email):
        die('至少要有電話或 Email 其中一個（之後才認得出是同一個人）')
    if not a.consent:
        die('要先跟顧問確認「本人同意建檔」，確認了再加 --consent')
    digits = re.sub(r'\D', '', a.phone or '')
    hits = []
    if digits:
        hits += rows("""SELECT id,name,job_slug,created_at,owner FROM applications WHERE superseded_by IS NULL AND COALESCE(status,'')<>'duplicate'
                          AND replace(replace(replace(replace(COALESCE(phone,''),'-',''),' ',''),'+886','0'),'(','') LIKE ?""", [f'%{digits[-9:]}%'])
    if a.email:
        hits += rows("SELECT id,name,job_slug,created_at,owner FROM applications WHERE superseded_by IS NULL AND COALESCE(status,'')<>'duplicate' AND lower(email)=lower(?)", [a.email.strip()])
    if hits:
        seen = {}
        for h in hits:
            seen[h['id']] = h
        print('⚠️ 系統裡已經有這個人（電話或 Email 對得上），一人一卡，不建新的：')
        for h in seen.values():
            print(f'  {h["id"]}｜{h["name"]}｜{h["job_slug"]}｜{str(h["created_at"])[:10]}｜負責 {h.get("owner") or "—"}')
        print('要記這次的事就用 note 寫進那張卡；要改應徵職缺請顧問到後台改。')
        sys.exit(2)
    job = 'unspecified'
    if a.job:
        js = rows("SELECT slug,title FROM jobs WHERE slug=? OR title LIKE ? ORDER BY (status='open') DESC LIMIT 6", [a.job, f'%{a.job}%'])
        if len(js) != 1:
            print('職缺對不到唯一一個，請顧問選：' if js else '找不到這個職缺：')
            for j in js:
                print(f'  {j["slug"]}｜{j["title"]}')
            sys.exit(2)
        job = js[0]['slug']
    body = {'source_kind': 'new', 'name': name, 'email': (a.email or '').strip(), 'phone': (a.phone or '').strip(), 'job_slug': job,
            'owner': (w['name'] or '').lower() or None, 'source_channel': a.source or '顧問建檔（阿財）', 'resume_source': '顧問提供',
            'call_notes': (a.note or '').strip(), 'consent_confirmed': True, 'by': w['name'] or '顧問'}
    if a.resume:
        path = os.path.expanduser(a.resume)
        if not os.path.isfile(path):
            die(f'找不到履歷檔：{path}')
        body.update({'resume_b64': base64.b64encode(open(path, 'rb').read()).decode(), 'resume_name': os.path.basename(path),
                     'resume_mime': 'application/pdf' if path.lower().endswith('.pdf') else 'application/octet-stream'})
    r = bo('/admin/pipeline/manual-forward', body)
    if not r.get('ok', True) or r.get('error'):
        die(r.get('error') or '建卡失敗')
    print(f'✅ 已建好 {name} 的人選卡片（負責：{w["name"]}，職缺：{job}）。{r.get("message") or ""}')


# ── 2026-11-02 阿財補齊顧問常用功能（Jacky：「全部做完」）──
# 會影響外面的人（客戶看得到、人選收得到、錢）一律走確認卡（Worker bo_call，真人按才執行）；只改內部紀錄的直接做。
def _confirm_bo(path, body, app, card, done_label, kind_label):
    w = who()
    need_where(w)
    aid = create_action('bo_call', app, {'path': path, 'body': body, 'done_label': done_label}, '', kind_label, card, card,
                        [('✅ 確認執行', 'cop_ok'), ('✖ 不要', 'cop_no')], w=w)
    print(f'✅ 確認卡貼好了（{aid}），按「確認執行」才會真的做：{done_label}')


def _company(app, name):
    """把顧問講的公司名對到 company_id：先找這位人選推薦過的公司，再找全部客戶。"""
    fw = rows("""SELECT f.company_id, c.display_name FROM candidate_forwards f LEFT JOIN client_companies c ON c.id=f.company_id
                  WHERE f.application_id=?""", [app['id']]) if app.get('id') else []
    if name:
        hit = [r for r in fw if name in (r.get('display_name') or '')]
        if not hit:
            hit = rows("SELECT id AS company_id, display_name FROM client_companies WHERE display_name LIKE ? OR aliases LIKE ? LIMIT 5", [f'%{name}%', f'%{name}%'])
    else:
        hit = fw
    if len(hit) == 1:
        return hit[0]['company_id'], hit[0].get('display_name') or name
    if not hit:
        die(f'對不到「{name or "客戶"}」：' + ('這位人選還沒推薦給任何客戶' if not name else '查無此客戶'))
    print('對到好幾家，請顧問選（用 --company 指定）：')
    for r in hit:
        print(f'  ・{r.get("display_name")}')
    sys.exit(2)


def cmd_recommend(a):
    """推薦人選給客戶（記成已推薦、客戶專區看得到）。一律確認卡。"""
    app = resolve(a.who)
    cid, cname = _company(app, a.company)
    warn = []
    cfr = rows("SELECT consultant_confirmed_at, file_id FROM client_formal_reports WHERE source_id=? AND company_id=? ORDER BY generated_at DESC LIMIT 1", [app['id'], cid])
    if not cfr or not cfr[0].get('file_id'):
        warn.append('還沒有推薦履歷（客戶專區只會看到名字）')
    if rows("SELECT 1 x FROM candidate_forwards WHERE application_id=? AND company_id=?", [app['id'], cid]):
        die(f'{app["name"]} 已經推薦給 {cname} 過了')
    body = {'application_id': app['id'], 'company_id': cid, 'job_slug': a.job or None, 'note': a.note or None, 'send_forms': bool(a.send_forms)}
    card = (f'📤 確認推薦｜{app["name"]} → {cname}\n職缺：{a.job or app.get("job_title") or app.get("job_slug")}\n'
            + (f'給客戶的備註：{a.note}\n' if a.note else '') + ('會同時寄人事資料表給人選\n' if a.send_forms else '')
            + (''.join(f'⚠️ {x}\n' for x in warn)) + '按了：系統記成已推薦，客戶專區立刻看得到這位人選。')
    _confirm_bo('/admin/forward-candidate', body, app, card, f'已推薦 {app["name"]} 給 {cname}', '推薦人選')


STAGE_ZH = {'stage1': '第一階段（一面）', 'stage2': '第二階段（二面）', 'stage3': '第三階段', 'stage4': '第四階段', 'offer': '錄取', 'onboard': '報到', 'care': '到職關懷', 'confirm': '顧問確認', 'screening': '阿財初審'}


def cmd_stage(a):
    app = resolve(a.who)
    cid, cname = _company(app, a.company)
    if a.to not in STAGE_ZH:
        die('階段只能是：' + '、'.join(f'{k}={v}' for k, v in STAGE_ZH.items()))
    body = {'application_id': app['id'], 'company_id': cid, 'stage': a.to, 'note': a.note or None}
    if a.date:
        body['onboard_date'] = a.date
    card = f'📶 確認改階段｜{app["name"]}（{cname}）→ {STAGE_ZH[a.to]}' + (f'（{a.date}）' if a.date else '') + (f'\n備註：{a.note}' if a.note else '') + '\n按了才會改；有綁 LINE 的人選可能會收到進度通知。'
    _confirm_bo('/admin/set-manual-stage', body, app, card, f'{app["name"]} 改成 {STAGE_ZH[a.to]}', '改人選階段')


def cmd_not_push(a):
    app = resolve(a.who)
    cid, cname = _company(app, a.company)
    if not a.reason:
        die('要寫不推進的原因（客戶看得到，用寫給對方看的措辭）')
    card = f'⛔ 確認標不推進｜{app["name"]}（{cname}）\n原因（客戶看得到）：{a.reason}'
    _confirm_bo('/admin/pipeline/forward-decision', {'application_id': app['id'], 'company_id': cid, 'not_recommended': True, 'reason': a.reason},
                app, card, f'{app["name"]} 在 {cname} 標不推進', '標不推進')


def cmd_withdraw(a):
    app = resolve(a.who)
    cid, cname = _company(app, a.company)
    if not a.reason:
        die('要寫人選放棄的原因')
    card = f'🚪 確認人選已放棄｜{app["name"]}（{cname}）\n原因：{a.reason}'
    _confirm_bo('/admin/pipeline/candidate-withdraw', {'application_id': app['id'], 'company_id': cid, 'reason': a.reason},
                app, card, f'{app["name"]} 已放棄 {cname}', '人選放棄')


def cmd_form_send(a):
    app = resolve(a.who)
    fs = _forms_of(app['id'])
    if a.company:
        fs = [f for f in fs if a.company in (f.get('company') or '')]
    if len(fs) != 1:
        if not fs:
            die('這位人選沒有可以寄的表單（要先推薦給有設定表單的客戶）')
        print('有好幾份，請用 --company 指定：'); [print(f'  ・{f.get("company")}｜{f["form_name"]}｜{f.get("status") or "還沒寄"}') for f in fs]; sys.exit(2)
    f = fs[0]
    if f.get('status') == 'submitted':
        die(f'{app["name"]} 的「{f["form_name"]}」已經交回了（{str(f.get("submitted_at"))[:16]}）')
    if f.get('status') == 'sent':
        sub = rows("SELECT id FROM candidate_form_submissions WHERE job_form_id=? AND application_id=? AND company_id=?", [f['job_form_id'], app['id'], f['company_id']])
        card = f'📨 確認催填｜{app["name"]}：{f.get("company")}「{f["form_name"]}」\n按了會再寄一次提醒信給人選。'
        _confirm_bo(f'/admin/candidate-form-submissions/{sub[0]["id"]}/resend', {}, app, card, f'已催 {app["name"]} 填表', '催填人事資料表')
        return
    card = f'📨 確認寄人事資料表｜{app["name"]}：{f.get("company")}「{f["form_name"]}」\n按了會寄信給人選請他填。'
    _confirm_bo('/admin/candidate-form-submissions/trigger', {'application_id': app['id'], 'company_id': f['company_id'], 'job_form_id': f['job_form_id']},
                app, card, f'已寄人事資料表給 {app["name"]}', '寄人事資料表')


def cmd_line_invite(a):
    app = resolve(a.who)
    card = f'💚 確認用 LINE 通知面談｜{app["name"]}\n按了會立刻送一則 LINE 給人選，附他的阿財面談連結。'
    _confirm_bo('/admin/line-notify-interview', {'application_id': app['id']}, app, card, f'已用 LINE 通知 {app["name"]} 面談', 'LINE 面談通知')


def cmd_client_report(a):
    """做推薦履歷（具名、客戶版）AI 草稿。做好會貼到「客戶履歷人工確認」，按確認才產 PDF。"""
    app = resolve(a.who)
    cid, cname = _company(app, a.company)
    r = bo(f'/admin/application/{app["id"]}/client-formal-report', {'company_id': cid})
    if r.get('ok') is False:
        die(r.get('error') or '沒辦法開始')
    print(f'✅ 已開始做 {app["name"]} 給 {cname} 的推薦履歷草稿，1～3 分鐘後會貼到「客戶履歷人工確認」，按確認才會產 PDF。'
          '提醒：推薦履歷要有中文姓名、年齡、性別、期望薪資，缺的話先用 note 補進卡片再做。')


BD_RES = {'interested': '有興趣', 'no_answer': '沒接', 'not_interested': '不需要／拒絕', 'send_info': '請寄資料', 'callback': '再約時間',
          'got_contact': '問到窗口', 'got_email': '拿到信箱', 'gatekeeper': '被總機擋', 'not_hiring': '沒在徵人', 'other': '其他'}


def cmd_client_log(a):
    """記客戶（開發中公司）的電話／進度，直接寫進客戶卡片；系統約 1 分鐘內照最新內容重排下一步。"""
    w = who()
    co_ = rows("SELECT DISTINCT company FROM bd_outreach WHERE company LIKE ? LIMIT 6", [f'%{a.company}%'])
    if not co_:
        co_ = rows("SELECT company FROM bd_company_status WHERE company LIKE ? LIMIT 6", [f'%{a.company}%'])
    if len(co_) != 1:
        if not co_:
            die(f'開發名單裡找不到「{a.company}」')
        print('對到好幾家，請顧問選：'); [print(f'  ・{r["company"]}') for r in co_]; sys.exit(2)
    company = co_[0]['company']
    if a.result and a.result not in BD_RES:
        die('結果只能是：' + '、'.join(f'{k}={v}' for k, v in BD_RES.items()))
    body = {'company': company, 'kind': a.kind or 'call', 'result': a.result or None, 'note': (a.note or '').strip(), 'by': w['name'] or '顧問',
            'next_step': a.next or '', 'next_due': a.due or ''}
    if a.tag:
        body['tag'] = a.tag
    r = bo('/admin/bd/log', body)
    if not r.get('ok'):
        die(r.get('error') or '沒寫進去')
    print(f'✅ 已記到 {company}：{BD_RES.get(a.result, "紀錄")}｜{clip(a.note, 60)}' + (f'｜下一步 {a.next} {a.due or ""}' if a.next else '｜下一步系統約 1 分鐘內照這筆重排'))


def cmd_billing(a):
    rs = rows("""SELECT i.id, i.label, i.due_date, i.amount_taxed, i.client_submitted_at, i.paid_at, i.confirmed_received, p.candidate_name, p.client_name
                   FROM placement_billing_installments i JOIN placements p ON p.id=i.placement_id
                  WHERE i.paid_at IS NULL OR (i.client_submitted_at IS NOT NULL AND i.confirmed_received IS NULL)
                  ORDER BY i.due_date LIMIT 20""")
    if not rs:
        print('目前沒有待收的款項。'); return
    for r in rs:
        st = '客戶說已匯款，待確認' if r.get('client_submitted_at') and r.get('confirmed_received') is None else ('未收款' if not r.get('paid_at') else '')
        print(f'・{r["id"]}｜{r.get("client_name")}｜{r.get("candidate_name")}｜{r.get("label")}｜{r.get("amount_taxed") or "—"} 元｜到期 {r.get("due_date") or "—"}｜{st}')


def cmd_billing_confirm(a):
    r = rows("""SELECT i.id, i.label, i.amount_taxed, p.candidate_name, p.client_name FROM placement_billing_installments i JOIN placements p ON p.id=i.placement_id WHERE i.id=?""", [a.id])
    if not r:
        die('找不到這筆款項（先用 billing 列出來）')
    x = r[0]
    app = {'id': '', 'name': x.get('client_name') or ''}
    if a.action == 'received':
        _confirm_bo('/admin/billing-installments/confirm', {'id': a.id, 'received': True}, app,
                    f'💰 確認已收到款項｜{x.get("client_name")}｜{x.get("candidate_name")}｜{x.get("label")}｜{x.get("amount_taxed") or ""} 元', '已確認收到款項', '收款確認')
    elif a.action == 'not-received':
        _confirm_bo('/admin/billing-installments/confirm', {'id': a.id, 'received': False}, app,
                    f'❌ 確認「客戶說匯了但還沒收到」｜{x.get("client_name")}｜{x.get("label")}', '已標記還沒收到', '收款確認')
    else:
        _confirm_bo('/admin/billing-installments/mark-paid', {'id': a.id, 'paid': True}, app,
                    f'💰 確認標記已收款｜{x.get("client_name")}｜{x.get("candidate_name")}｜{x.get("label")}', '已標記收款', '標記收款')



def cmd_funnel(a):
    """2026-11-03 Jacky：某家公司／某職缺「社群點擊 → 應徵 → 阿財面談」人數統計。
    社群點擊＝link_clicks（社群貼文、留言裡的職缺連結被點）；應徵＝applications；面談＝interview_started_at／ended_at。
    ⚠️ 點擊和應徵沒有一對一綁定（點擊不記名），只能看同一職缺的量，不能說「這幾個點擊就是這幾個人」。"""
    k = a.key.strip()
    js = rows("""SELECT j.slug, j.title, j.status FROM jobs j
                 WHERE j.slug=? OR j.title LIKE ? OR j.client_name LIKE ?
                    OR j.company_id IN (SELECT company_id FROM jobs WHERE client_name LIKE ? AND company_id IS NOT NULL)
                 ORDER BY j.status='open' DESC, j.slug""", [k, f'%{k}%', f'%{k}%', f'%{k}%'])
    if a.only:
        js = [j for j in js if a.only in j['title'] or a.only in j['slug']]
    if not js:
        die(f'找不到跟「{k}」有關的職缺')
    since = a.since or '2000-01-01'
    tot = dict(posts=0, clk=0, thr=0, app=0, soc=0, iv=0, done=0)
    print(f'【{k}】社群→應徵→阿財面談（{since} 起；點擊與應徵無法一對一綁定，看量即可）')
    for j in js:
        c = rows("""SELECT count(*) n, count(DISTINCT queue_id) posts,
                           sum(CASE WHEN ua LIKE '%Barcelona%' THEN 1 ELSE 0 END) thr
                    FROM link_clicks WHERE job_slug=? AND clicked_at>=?""", [j['slug'], since])[0]
        ap = rows("""SELECT count(*) n,
                            sum(CASE WHEN interview_started_at IS NOT NULL THEN 1 ELSE 0 END) iv,
                            sum(CASE WHEN interview_ended_at IS NOT NULL THEN 1 ELSE 0 END) done,
                            sum(CASE WHEN coalesce(utm_source,'') NOT IN ('consultant','104','') AND coalesce(utm_medium,'')!='sourced' THEN 1 ELSE 0 END) self
                     FROM applications WHERE job_slug=? AND created_at>=? AND name NOT LIKE '%測試%'""", [j['slug'], since])[0]
        n = lambda v: int(v or 0)
        print(f'・{j["title"]}（{j["status"]}）：貼文 {n(c["posts"])} 篇｜社群點擊 {n(c["n"])}（Threads {n(c["thr"])}）'
              f'｜應徵 {n(ap["n"])}（自己從網站／LINE 來 {n(ap["self"])}）｜阿財面談 開始 {n(ap["iv"])}、完成 {n(ap["done"])}')
        for key, v in (('posts', c['posts']), ('clk', c['n']), ('thr', c['thr']), ('app', ap['n']), ('soc', ap['self']), ('iv', ap['iv']), ('done', ap['done'])):
            tot[key] += n(v)
    print(f'合計：貼文 {tot["posts"]} 篇｜社群點擊 {tot["clk"]}（Threads {tot["thr"]}）｜應徵 {tot["app"]}（自己來 {tot["soc"]}）'
          f'｜阿財面談 開始 {tot["iv"]}、完成 {tot["done"]}')

def cmd_job_status(a):
    j = rows("SELECT slug,title,status FROM jobs WHERE slug=? OR title LIKE ? ORDER BY (status='open') DESC LIMIT 6", [a.job, f'%{a.job}%'])
    if len(j) != 1:
        if not j:
            die('找不到這個職缺')
        print('對到好幾個，請顧問選：'); [print(f'  ・{x["slug"]}｜{x["title"]}｜{x["status"]}') for x in j]; sys.exit(2)
    j = j[0]
    st = 'open' if a.to == 'open' else 'closed'
    _confirm_bo(f'/admin/jobs/{j["slug"]}', {'status': st}, {'id': '', 'name': j['title']},
                f'📋 確認{"開啟" if st == "open" else "關閉"}職缺｜{j["title"]}（目前：{j["status"]}）\n' + ('關閉後官網拿掉、阿財不再面談這個職缺。' if st == 'closed' else '開啟後官網會顯示、阿財開始面談。'),
                f'職缺「{j["title"]}」已{"開啟" if st == "open" else "關閉"}', '開關職缺')


def cmd_line_who(a):
    """LINE 官方帳號上還沒綁定的人：從他們打過的字找電話／Email，對得上就說是誰。只查不改。"""
    rs = rows("""SELECT b.line_user_id, b.display_name, b.state, b.created_at,
                        (SELECT GROUP_CONCAT(l.text, ' ／ ') FROM (SELECT text FROM line_inbound_log WHERE line_user_id=b.line_user_id AND msg_type='text' ORDER BY created_at DESC LIMIT 6) l) AS texts,
                        (SELECT MAX(created_at) FROM line_inbound_log WHERE line_user_id=b.line_user_id) AS last_at,
                        (SELECT COUNT(*) FROM line_inbound_log WHERE line_user_id=b.line_user_id AND msg_type IN ('file','image')) AS files
                   FROM line_bindings b WHERE b.state<>'bound' ORDER BY last_at DESC LIMIT ?""", [a.n])
    if a.q:
        rs = [r for r in rs if a.q in (r.get('texts') or '') or a.q in (r.get('display_name') or '')]
    if not rs:
        print('沒有符合的未綁定 LINE 人選。'); return
    for r in rs:
        t = r.get('texts') or ''
        phones = sorted(set(re.findall(r'09\d{2}[-\s]?\d{3}[-\s]?\d{3}', t)))
        mails = sorted(set(re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', t)))
        match = []
        for ph in phones:
            d = re.sub(r'\D', '', ph)
            match += [m['name'] for m in rows("SELECT name FROM applications WHERE replace(replace(COALESCE(phone,''),'-',''),' ','') LIKE ? AND superseded_by IS NULL LIMIT 2", [f'%{d[-9:]}%'])]
        for em in mails:
            match += [m['name'] for m in rows("SELECT name FROM applications WHERE lower(email)=lower(?) AND superseded_by IS NULL LIMIT 2", [em])]
        print(f'・LINE：{r.get("display_name") or "（沒抓到名稱）"}｜最後訊息 {str(r.get("last_at") or "")[:16]}｜傳過檔案/圖 {r.get("files") or 0}'
              + (f'｜電話 {"、".join(phones)}' if phones else '') + (f'｜信箱 {"、".join(mails)}' if mails else '')
              + (f'｜✅ 對到卡片：{"、".join(sorted(set(match)))}' if match else '｜❓ 還不知道是誰')
              + f'\n   說過：{clip(t, 120)}')


OFFICIAL_LINE = 'https://lin.ee/XcSWPzM'   # 全民獵才 LINE 官方帳號（跟信件版型 mail_v2 LINE_URL 同一個）


def cmd_links(a):
    """全民獵才 LINE 官方帳號＋這位顧問自己的 LINE 加好友連結（社群帳號上設的）。"""
    w = who()
    own = rows("SELECT label, line_link FROM social_accounts WHERE consultant_name=? AND is_active=1 AND COALESCE(line_link,'')<>''", [w['name'] or ''])
    print(f'全民獵才 LINE 官方帳號：{OFFICIAL_LINE}')
    for r in own:
        print(f'{w["name"]} 的 LINE 加好友連結（{r["label"]}）：{r["line_link"]}')
    if not own:
        print(f'（{w["name"]} 還沒有設定自己的 LINE 加好友連結）')


def cmd_social_post(a):
    """幫顧問排一篇社群發文（職缺文或話題文）。草稿照原本的社群流程產生、審核；排程本身走確認卡。"""
    w = who()
    plat = a.platform or 'threads'
    acc = rows("SELECT id, label FROM social_accounts WHERE consultant_name=? AND platform=? AND is_active=1", [w['name'] or '', plat])
    if not acc:
        die(f'{w["name"]} 沒有啟用中的 {plat} 帳號')
    body = {'account_id': acc[0]['id'], 'post_date': a.date or now_tpe().strftime('%Y-%m-%d'), 'post_time': a.time or ''}
    what = ''
    if a.job:
        js = rows("SELECT slug,title FROM jobs WHERE (slug=? OR title LIKE ?) AND COALESCE(status,'open') IN ('open','social_only') ORDER BY updated_at DESC LIMIT 6", [a.job, f'%{a.job}%'])
        if len(js) != 1:
            if not js:
                die('找不到開放中的這個職缺')
            print('對到好幾個職缺，請顧問選：'); [print(f'  ・{j["slug"]}｜{j["title"]}') for j in js]; sys.exit(2)
        body['job_slug'] = js[0]['slug']; what = f'職缺文：{js[0]["title"]}'
    elif a.topic:
        tp = rows("SELECT id, name FROM topic_prompts WHERE CAST(id AS TEXT)=? OR name LIKE ? LIMIT 6", [a.topic, f'%{a.topic}%'])
        if len(tp) != 1:
            if not tp:
                die('找不到這個話題（可以先問顧問要哪一題，或改用職缺文）')
            print('對到好幾個話題，請顧問選：'); [print(f'  ・{t["id"]}｜{t["name"]}') for t in tp]; sys.exit(2)
        body['topic_id'] = tp[0]['id']; what = f'話題文：{tp[0]["name"]}'
        if a.hook:
            body['topic_hook'] = a.hook
    else:
        die('要排職缺文（--job）還是話題文（--topic）？')
    card = f'📣 確認排社群發文｜{acc[0]["label"]}\n{what}\n日期：{body["post_date"]} {body["post_time"] or "（當天排程跑到就產草稿）"}\n按了才排；草稿產好會照原本流程貼來審核，審過才發。'
    _confirm_bo('/admin/social/schedule', body, {'id': '', 'name': acc[0]['label']}, card, f'已排 {acc[0]["label"]} 的{what}', '排社群發文')


def _accounts_for(names, plat):
    w = who()
    names = [n.strip() for n in (names or w['name'] or '').replace('、', ',').split(',') if n.strip()]
    if any(n.lower() != (w['name'] or '').lower() for n in names) and w.get('username') not in ('jackyyuqi', 'behe10'):
        die('只有 Jacky、Phoebe 可以幫別人的帳號排文；你只能排自己的')
    out = []
    for n in names:
        r = rows("SELECT id, label, consultant_name FROM social_accounts WHERE lower(consultant_name)=lower(?) AND platform=? AND is_active=1", [n, plat])
        if not r:
            die(f'{n} 沒有啟用中的 {plat} 帳號（有的帳號：' + '、'.join(x['consultant_name'] for x in rows("SELECT DISTINCT consultant_name FROM social_accounts WHERE platform=? AND is_active=1", [plat])) + '）')
        out.append(r[0])
    return out


def cmd_social_gaps(a):
    """每個帳號還沒發過（也沒排）的開放職缺（含只發社群的未簽約職缺），依客戶分組。"""
    plat = a.platform or 'threads'
    accs = _accounts_for(a.accounts, plat)
    jobs = rows("""SELECT j.slug, j.title, j.status, COALESCE(c.display_name, j.client_name, '（未綁客戶）') AS co FROM jobs j
                    LEFT JOIN client_companies c ON c.id=j.company_id WHERE j.status IN ('open','social_only') AND j.slug NOT LIKE 'pending-so-%'
                    AND j.slug NOT IN ('unspecified','entry-general-match')""")
    if a.company:
        jobs = [j for j in jobs if a.company in (j['co'] or '') or a.company in j['title']]
    for acc in accs:
        done = {r['job_slug'] for r in rows("""SELECT job_slug FROM social_post_queue WHERE account_id=? AND status NOT IN ('skipped','deleted','superseded','expired','blocked','blocked_compliance')
                                               UNION SELECT job_slug FROM social_schedule WHERE account_id=? AND COALESCE(status,'') NOT IN ('cancelled')""", [acc['id'], acc['id']])}
        gap = [j for j in jobs if j['slug'] not in done]
        print(f'\n【{acc["label"]}】還沒發過 {len(gap)} 個：')
        byco = {}
        for j in gap:
            byco.setdefault(j['co'], []).append(j)
        for co_, js in sorted(byco.items()):
            print(f'  {co_}：' + '；'.join(f'{j["title"]}〔{j["slug"]}〕' + ('（未簽約・只發社群）' if j['status'] == 'social_only' else '') for j in js))


def cmd_styles(a):
    """發文公式（風格提示詞不列，系統會自動套顧問自己的語氣）。"""
    for r in rows("SELECT id, name, COALESCE(subtype,'') st FROM style_prompts WHERE COALESCE(subtype,'')<>'' ORDER BY id"):
        print(f'・{r["id"]}｜{r["name"]}' + {'pure': '（純職缺）', 'dialog': '（對話討論型）', 'anon': '（匿名人選文）'}.get(r['st'], ''))
    print('不指定公式＝系統照平常的規則自動選。')


def cmd_social_batch(a):
    """一次排多篇社群發文：多個帳號 × 多個職缺（同一天同帳號可排不同職缺；同帳號同天同職缺同公式系統會擋）。一張確認卡。"""
    import datetime as _dt
    plat = a.platform or 'threads'
    accs = _accounts_for(a.accounts, plat)
    slugs = [x.strip() for x in (a.jobs or '').replace('、', ',').split(',') if x.strip()]
    if not slugs:
        die('要排哪些職缺？用 --jobs slug1,slug2（先用 social-gaps 看代號）')
    jm = {r['slug']: r['title'] for r in rows(f"SELECT slug,title FROM jobs WHERE slug IN ({','.join('?' * len(slugs))}) AND status IN ('open','social_only')", slugs)}
    bad = [x for x in slugs if x not in jm]
    if bad:
        die('這些職缺代號對不到開放中的職缺：' + '、'.join(bad))
    style = None
    if a.style:
        st = rows("SELECT id,name FROM style_prompts WHERE CAST(id AS TEXT)=? OR name LIKE ? LIMIT 3", [a.style, f'%{a.style}%'])
        if len(st) != 1:
            die('公式對不到唯一一個（用 styles 看編號）')
        style = st[0]
    d0 = _dt.date.fromisoformat(a.start) if a.start else now_tpe().date()
    items, lines = [], []
    for ai, acc in enumerate(accs):
        for ji, sl in enumerate(slugs):
            day = d0 + _dt.timedelta(days=(ji if a.spread else 0))
            body = {'account_id': acc['id'], 'post_date': day.isoformat(), 'post_time': a.time or '', 'job_slug': sl}
            if style:
                body['style_id'] = style['id']
            items.append({'path': '/admin/social/schedule', 'body': body})
            lines.append(f'・{day.strftime("%m/%d")}｜{acc["consultant_name"]}｜{jm[sl]}')
    if len(items) > 40:
        die(f'一次 {len(items)} 篇太多了，請分批（一次最多 40 篇）')
    card = (f'📣 確認批量排社群文｜{plat}｜共 {len(items)} 篇' + (f'｜公式：{style["name"]}' if style else '｜公式：系統自動選') + '\n'
            + '\n'.join(lines[:40]) + '\n按了才排；草稿產好會照原本流程一篇篇貼來審核，審過才發。同帳號同天同職缺同公式的會被系統擋下（卡片結果會列出）。')
    w = who(); need_where(w)
    aid = create_action('bo_call', {'id': '', 'name': f'社群批量 {len(items)} 篇'}, {'batch': items, 'done_label': f'已排 {len(items)} 篇社群文'},
                        '', '批量排社群文', card, card, [('✅ 確認排程', 'cop_ok'), ('✖ 不要', 'cop_no')], w=w)
    print(f'✅ 確認卡貼好了（{aid}），共 {len(items)} 篇，按「確認排程」才會排。')


def cmd_set_owner(a):
    app = resolve(a.who)
    ow = None if a.to.lower() in ('none', '無', '取消') else a.to.strip().lower()
    if ow and not rows('SELECT 1 x FROM consultant_tg WHERE lower(display_name)=?', [ow]) and not rows("SELECT 1 x FROM applications WHERE lower(owner)=? LIMIT 1", [ow]):
        die(f'沒有「{a.to}」這位顧問')
    r = bo('/admin/set-owner', {'kind': 'application', 'id': app['id'], 'owner': ow})
    if r.get('ok') is False:
        die(r.get('error') or '沒改成')
    print(f'✅ {app["name"]} 的負責顧問改成 {(ow or "（未指派）").capitalize()}')


QUICK_APPLY_SLUGS = {'bim-mep-drafting-engineer-hsinchu', 'semiconductor-bim-mep-drafting-trainee-hsinchu', 'bim-engineer-houli',
                     'trainee-engineering-design-engineer', 'engineering-drafting-trainee-tongluo'}   # 跟 consultant-app/resumelink.js 同一份


def cmd_resume_link(a):
    """給人選連結：聊過的給「上傳履歷連結」、還沒聊過的給「應徵連結」（貼 LINE 用）；--send 寄 Email（確認卡）。"""
    w = who()
    app = resolve(a.who) if a.who else None
    job = a.job or (app.get('job_slug') if app else '')
    if job in ('unspecified', 'entry-general-match', 'other') or (job or '').startswith('pending-so-'):
        job = ''
    if job:
        jr = rows("SELECT slug,title,status FROM jobs WHERE slug=? OR title LIKE ? LIMIT 3", [job, f'%{job}%'])
        if len(jr) == 1 and jr[0]['status'] not in ('closed', 'social_only', 'draft'):
            job, jt = jr[0]['slug'], jr[0]['title']
        else:
            job, jt = '', ''
    else:
        jt = ''
    r = bo('/admin/resume-upload-link', {'action': 'link', 'by': w['name'], 'job_slug': job, 'application_id': app['id'] if app else None, 'src': 'line'})
    up = r.get('link') if r.get('ok') else None
    if not up:
        die(r.get('error') or '產不出上傳履歷連結')
    site = 'https://step1ne.com'
    import urllib.parse as _u
    apply = (f'{site}/apply-general/' if not job else
             f'{site}{"/apply-quick/" if job in QUICK_APPLY_SLUGS else "/apply/"}?job={_u.quote(job)}&title={_u.quote(jt)}&utm_source=consultant&utm_medium=line&utm_campaign={_u.quote(job)}')
    print(f'已經聊過 → 上傳履歷連結（貼 LINE）：{up}')
    print(f'還沒聊過 → 應徵連結（貼 LINE，會先跟阿財聊）：{apply}')
    print('（人選從上傳履歷連結傳進來，卡片會自動指派給你）')
    if a.send:
        if not app:
            die('寄 Email 要指定人選（--who）')
        em = rows('SELECT email, name FROM applications WHERE id=?', [app['id']])[0]
        if not (em.get('email') or '').strip() or 'placeholder' in (em.get('email') or ''):
            die(f'{app["name"]} 卡片上沒有 Email，只能貼 LINE')
        _confirm_bo('/admin/resume-upload-link', {'action': 'send', 'by': w['name'], 'to': em['email'], 'name': em['name'], 'application_id': app['id'],
                                                   'job_slug': job, 'src': 'email'}, app,
                    f'📧 確認寄上傳履歷連結｜{app["name"]}（{em["email"]}）' + (f'\n職缺：{jt}' if jt else '') + '\n按了會從 official@ 寄出（系統預設的信件內容）。',
                    f'已寄上傳履歷連結給 {app["name"]}', '寄上傳履歷連結')


def main():
    ap = argparse.ArgumentParser(description='Step1ne 顧問助理工具箱', epilog='硬規則：回答合約條款、價格、期限、客戶條件一定附出處（kb 會印）；查不到就說查不到，不准推測。列客戶／職缺前先跑 clients。')
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('find', help='查人選'); p.add_argument('who'); p.add_argument('--all', action='store_true', help='同名多筆全部列')
    p.set_defaults(fn=cmd_find)
    p = sp.add_parser('note', help='把顧問回報寫進人選卡片（電洽紀錄／備註，內部紀錄）'); p.add_argument('who'); p.add_argument('--text'); p.add_argument('--text-file')
    p.add_argument('--type', help='預設「電洽紀錄」，也可以「note」'); p.set_defaults(fn=cmd_note)
    p = sp.add_parser('form-upload', help='上傳人選填好的人事資料表（已交過要 --replace）'); p.add_argument('who'); p.add_argument('--file', required=True)
    p.add_argument('--company'); p.add_argument('--replace', action='store_true'); p.add_argument('--name', help='檔名'); p.set_defaults(fn=cmd_form_upload)
    p = sp.add_parser('resume-upload', help='把履歷檔放進人選卡片'); p.add_argument('who'); p.add_argument('--file', required=True)
    p.add_argument('--name'); p.set_defaults(fn=cmd_resume_upload)
    p = sp.add_parser('inbox', help='看收件匣（可篩人選／公司／寄件人／關鍵字）'); p.add_argument('q', nargs='?'); p.add_argument('--days', type=int, default=7)
    p.add_argument('-n', type=int, default=15); p.set_defaults(fn=cmd_inbox)
    p = sp.add_parser('mail-read', help='看一封信全文'); p.add_argument('id'); p.set_defaults(fn=cmd_mail_read)
    p = sp.add_parser('mail-att', help='下載並看信件附件'); p.add_argument('id'); p.add_argument('n', nargs='?', type=int, default=0); p.set_defaults(fn=cmd_mail_att)
    p = sp.add_parser('tell', help='用阿財私訊傳話給另一位顧問'); p.add_argument('to'); p.add_argument('--text'); p.set_defaults(fn=cmd_tell)
    p = sp.add_parser('recommend', help='推薦人選給客戶（確認卡）'); p.add_argument('who'); p.add_argument('--company', required=True); p.add_argument('--job'); p.add_argument('--note')
    p.add_argument('--send-forms', action='store_true'); p.set_defaults(fn=cmd_recommend)
    p = sp.add_parser('stage', help='改人選在某家客戶的階段（確認卡）'); p.add_argument('who'); p.add_argument('--company'); p.add_argument('--to', required=True)
    p.add_argument('--date', help='報到日 YYYY-MM-DD'); p.add_argument('--note'); p.set_defaults(fn=cmd_stage)
    p = sp.add_parser('not-push', help='標不推進（確認卡，客戶看得到原因）'); p.add_argument('who'); p.add_argument('--company'); p.add_argument('--reason'); p.set_defaults(fn=cmd_not_push)
    p = sp.add_parser('withdraw', help='人選自己放棄（確認卡）'); p.add_argument('who'); p.add_argument('--company'); p.add_argument('--reason'); p.set_defaults(fn=cmd_withdraw)
    p = sp.add_parser('form-send', help='寄／催人事資料表給人選（確認卡）'); p.add_argument('who'); p.add_argument('--company'); p.set_defaults(fn=cmd_form_send)
    p = sp.add_parser('line-invite', help='用 LINE 通知人選來阿財面談（確認卡）'); p.add_argument('who'); p.set_defaults(fn=cmd_line_invite)
    p = sp.add_parser('client-report', help='做推薦履歷（具名）AI 草稿'); p.add_argument('who'); p.add_argument('--company'); p.set_defaults(fn=cmd_client_report)
    p = sp.add_parser('client-log', help='記客戶電話／進度（直接寫進客戶卡片）'); p.add_argument('company'); p.add_argument('--result'); p.add_argument('--note')
    p.add_argument('--kind', choices=['call', 'mail', 'other']); p.add_argument('--next'); p.add_argument('--due'); p.add_argument('--tag', choices=['HOT', 'WARM', 'FUTURE', 'NO', 'MATERIAL'])
    p.set_defaults(fn=cmd_client_log)
    p = sp.add_parser('billing', help='待收款項'); p.set_defaults(fn=cmd_billing)
    p = sp.add_parser('billing-confirm', help='收款確認（確認卡）'); p.add_argument('id'); p.add_argument('--action', choices=['received', 'not-received', 'paid'], default='received')
    p.set_defaults(fn=cmd_billing_confirm)
    p = sp.add_parser('job-status', help='開／關職缺（確認卡）'); p.add_argument('job'); p.add_argument('--to', choices=['open', 'closed'], required=True); p.set_defaults(fn=cmd_job_status)
    p = sp.add_parser('line-who', help='LINE 上還沒綁定的人是誰'); p.add_argument('q', nargs='?'); p.add_argument('-n', type=int, default=20); p.set_defaults(fn=cmd_line_who)
    p = sp.add_parser('links', help='全民獵才 LINE 官方帳號＋自己的 LINE 連結'); p.set_defaults(fn=cmd_links)
    p = sp.add_parser('social-gaps', help='每個帳號還沒發過的職缺'); p.add_argument('--accounts', help='逗號分隔，例如 Jacky,Phoebe,Anna,DR；不給＝自己')
    p.add_argument('--platform', choices=['threads', 'linkedin']); p.add_argument('--company'); p.set_defaults(fn=cmd_social_gaps)
    p = sp.add_parser('styles', help='發文公式清單'); p.set_defaults(fn=cmd_styles)
    p = sp.add_parser('resume-link', help='給人選上傳履歷／應徵連結（--send 寄 Email，確認卡）'); p.add_argument('who', nargs='?'); p.add_argument('--job')
    p.add_argument('--send', action='store_true'); p.set_defaults(fn=cmd_resume_link)
    p = sp.add_parser('social-batch', help='批量排社群文（一張確認卡）'); p.add_argument('--accounts'); p.add_argument('--jobs', required=True)
    p.add_argument('--style'); p.add_argument('--platform', choices=['threads', 'linkedin']); p.add_argument('--start', help='第一天 YYYY-MM-DD')
    p.add_argument('--time'); p.add_argument('--spread', action='store_true', help='每個職缺排在不同天（第 1 個職缺第 1 天、第 2 個第 2 天…）'); p.set_defaults(fn=cmd_social_batch)
    p = sp.add_parser('social-post', help='排一篇社群發文（確認卡）'); p.add_argument('--job'); p.add_argument('--topic'); p.add_argument('--hook', help='話題文的切入角度')
    p.add_argument('--platform', choices=['threads', 'linkedin']); p.add_argument('--date'); p.add_argument('--time'); p.set_defaults(fn=cmd_social_post)
    p = sp.add_parser('new-card', help='建新的人選卡片（一人一卡：電話/Email 對到就不建）'); p.add_argument('name'); p.add_argument('--phone'); p.add_argument('--email')
    p.add_argument('--job', help='職缺代號或關鍵字，不給＝暫無職缺'); p.add_argument('--source', help='哪個管道：104、LinkedIn、LINE、轉介…'); p.add_argument('--note')
    p.add_argument('--resume', help='履歷檔路徑'); p.add_argument('--consent', action='store_true', help='顧問確認本人同意建檔'); p.set_defaults(fn=cmd_new_card)
    p = sp.add_parser('job', help='查職缺與公司近期動態'); p.add_argument('key'); p.set_defaults(fn=cmd_job)
    p = sp.add_parser('funnel', help='某公司／職缺：社群點擊→應徵→阿財面談人數'); p.add_argument('key', help='公司名或職缺關鍵字'); p.add_argument('--only', help='只看標題含這個字的職缺（例：日本）'); p.add_argument('--since', help='起始日 YYYY-MM-DD'); p.set_defaults(fn=cmd_funnel)
    p = sp.add_parser('today', help='今天/明天的電話（只列自己負責＋還沒指派的）'); p.add_argument('--days', type=int, default=2); p.add_argument('--all', action='store_true'); p.set_defaults(fn=cmd_today)
    p = sp.add_parser('set-owner', help='指派人選的負責顧問'); p.add_argument('who'); p.add_argument('--to', required=True, help='Jacky、Phoebe…；none＝取消指派'); p.set_defaults(fn=cmd_set_owner)
    p = sp.add_parser('call-mail', help='約電話信（預覽＋確認卡）'); p.add_argument('who'); p.add_argument('--note')
    p.add_argument('--topics', help='逗號分隔：' + ','.join(CALL_TOPICS)); p.add_argument('--topic-extra')
    p.add_argument('--ask-resume', choices=['yes', 'no']); p.add_argument('--by', help='署名顧問（不填＝負責顧問）')
    p.set_defaults(fn=cmd_call_mail)
    p = sp.add_parser('call-confirm', help='回信確認電話時間（預覽＋確認卡）'); p.add_argument('who')
    p.add_argument('--slot', required=True, help='信裡寫的時間，例如「10/8（四）下午 2:00」'); p.add_argument('--salutation')
    p.add_argument('--phone'); p.add_argument('--extra'); p.add_argument('--at', help='同時登記電話時間＋提醒，例如 2026-10-08 14:00')
    p.add_argument('--before', type=int, default=30); p.set_defaults(fn=cmd_call_confirm)
    p = sp.add_parser('decline', help='婉拒信（預覽＋確認卡）'); p.add_argument('who')
    p.add_argument('--reason', default='fit', choices=list(DECLINE_REASONS)); p.add_argument('--extra')
    p.add_argument('--no-keep', action='store_true'); p.add_argument('--subject'); p.add_argument('--text-file')
    p.add_argument('--again', action='store_true'); p.set_defaults(fn=cmd_decline)
    p = sp.add_parser('free-mail', help='自由信件：寄給客戶或人選（確認卡；可附檔）'); p.add_argument('--to', help='收件信箱；寄給人選時可省略，會自動帶人選卡片上的 Email')
    p.add_argument('--html-file', help='整封信的 HTML（好看版）；不給就用系統預設版型')
    p.add_argument('--from-name', help='寄件人顯示名稱；不填＝「全民獵才 招募團隊」（2026-11-02 Jacky：統一，除非有指定）')
    p.add_argument('--cc'); p.add_argument('--subject', required=True); p.add_argument('--text-file'); p.add_argument('--text')
    p.add_argument('--attach', action='append', help='附件路徑，可重複'); p.add_argument('--who', help='收件人是人選時填人選姓名或編號（會套用人選文字規則、寫進人選卡片）')
    p.add_argument('--company', help='收件人是客戶時填公司名（記在這家的紀錄）')
    p.add_argument('--file-id', action='append', help='附系統裡已存在的檔案：檔案編號，或「編號:顯示檔名」，可重複')
    p.add_argument('--card-note', help='確認卡最上面多一行說明（不會進信裡）')
    p.add_argument('--anon', action='append', help='附上這位人選系統做好的匿名履歷 PDF（姓名或編號，可重複）')
    p.add_argument('--now', action='store_true', help='直接寄出，不貼確認卡（顧問本人明確說「寄」才用）'); p.set_defaults(fn=cmd_free_mail)
    p = sp.add_parser('anon', help='製作匿名履歷（系統流程，TG 確認後產 PDF）'); p.add_argument('who'); p.add_argument('--job', help='對哪個職缺做（職缺 slug），不填用人選原本的職缺'); p.add_argument('--status', action='store_true', help='只看做好了沒')
    p.set_defaults(fn=cmd_anon)
    p = sp.add_parser('anon-edit', help='改匿名履歷（給修改意見，AI 重新整理）'); p.add_argument('who'); p.add_argument('instruction'); p.set_defaults(fn=cmd_anon_edit)
    p = sp.add_parser('anon-confirm', help='確認匿名履歷草稿、產 PDF'); p.add_argument('who'); p.set_defaults(fn=cmd_anon_confirm)
    p = sp.add_parser('summary', help='電洽逐字稿寫進卡片（確認卡）'); p.add_argument('who'); p.add_argument('--file'); p.add_argument('--text')
    p.set_defaults(fn=cmd_summary)
    p = sp.add_parser('book', help='登記約好的電話＋提醒（確認卡）'); p.add_argument('who'); p.add_argument('--at', required=True)
    p.add_argument('--before', type=int, default=30); p.add_argument('--note'); p.set_defaults(fn=cmd_book)
    p = sp.add_parser('remind', help='只提醒自己（不寫卡片）'); p.add_argument('--at', required=True); p.add_argument('--text', default=''); p.add_argument('--name', default='', help='沒掛人選卡時的顯示名稱（例：Stanley Wang（美德 供應鏈主管））；有名字才會進發財基地行事曆')
    p.add_argument('--app'); p.add_argument('--before', type=int, default=30); p.set_defaults(fn=cmd_remind)
    p = sp.add_parser('reminders', help='還沒發的提醒'); p.add_argument('--all', action='store_true'); p.set_defaults(fn=cmd_reminders)
    p = sp.add_parser('cancel-remind'); p.add_argument('id'); p.set_defaults(fn=cmd_cancel_remind)
    p = sp.add_parser('match', help='從現有人選配對＋針對職缺匿名摘要（等同後台客戶卡片按鈕）'); p.add_argument('--company'); p.add_argument('--job', help='職缺 slug'); p.add_argument('--run'); p.add_argument('--brief', help='application_id，逗號分隔'); p.set_defaults(fn=cmd_match)
    p = sp.add_parser('pending', help='最近的確認卡狀態'); p.add_argument('-n', type=int, default=10); p.set_defaults(fn=cmd_pending)
    p = sp.add_parser('clients', help='主力客戶狀態總表（唯一真相）——列客戶／排職缺／找人前先跑'); p.add_argument('name', nargs='?', help='只看這家')
    p.add_argument('--short', action='store_true', help='不印來源與備註'); p.add_argument('--all', action='store_true', help='含已封存的')
    p.add_argument('--log', metavar='客戶', help='看這家的修改紀錄'); p.add_argument('-n', type=int, default=30); p.set_defaults(fn=cmd_clients)
    p = sp.add_parser('client-set', help='改客戶總表（Jacky／Phoebe 明確說要改才用；會留紀錄）'); p.add_argument('client')
    p.add_argument('--contract'); p.add_argument('--contacts'); p.add_argument('--channel', help='管道限制'); p.add_argument('--next', help='下一步'); p.add_argument('--note')
    p.add_argument('--add-job', help='新增找人中的職缺'); p.add_argument('--confirmed', action='store_true', help='搭配 --add-job：客戶已確認')
    p.add_argument('--confirm-job', help='把既有職缺標成客戶確認（要 --source）'); p.add_argument('--source', help='客戶怎麼確認的（哪封信／哪通電話）')
    p.add_argument('--date'); p.add_argument('--job-note')
    p.add_argument('--drop-job', help='拿掉職缺（移到已拿掉）'); p.add_argument('--pause-job', help='暫停職缺'); p.add_argument('--reason')
    p.set_defaults(fn=cmd_client_set)
    p = sp.add_parser('kb', help='小知識庫：一次搜書櫃全文／客戶總表／客戶備註／職缺備註／近 60 天信件／人選備註（每筆附出處）')
    p.add_argument('q', nargs='+', help='關鍵字，多個＝都要有'); p.add_argument('--days', type=int, default=60); p.add_argument('--mails', type=int, default=8)
    p.add_argument('--n-snip', type=int, default=3); p.add_argument('--all', action='store_true', help='連舊版公版合約也搜'); p.set_defaults(fn=cmd_kb)
    a = ap.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
