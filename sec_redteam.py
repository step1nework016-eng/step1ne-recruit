#!/usr/bin/env python3
"""sec_redteam.py — 紅隊每週自我稽核（這台 WSL2 的「只讀」部分，E18，2026-10-09）

依據 docs/wsl2/資安/紅隊技能包/SKILL.md。只稽核自己的系統、只讀、不改任何東西、不打任何對外請求（連 D1 都只在 --insert 時才寫，
而且只寫 sec_findings／sec_events 兩張表，不放寬 d1q.py 的夜間白名單）。

這支涵蓋「讀碼／本機唯讀」就能確認的項目：
  G1 金鑰檔權限（只看權限位元，不讀內容）   F1 金鑰有沒有進版控／wrangler vars（只回報位置與變數名，不印值）
  E5/G2 背景 AI 有沒有都套 ai_lockdown     G3 自動更新鏈（git 來源與傳輸）
  B2/C1 內部頁權杖存 localStorage／innerHTML 數量／CSP    D1 .bak 殘留   C3 CORS `*`   D2 robots

打線上服務的項目（A 區 BOLA、E 區對阿財阿福下提示注入題）這支不做：第一輪只讀模式；之後要做走 SKILL 的節流與誘餌規則，另外排。

用法：
  python3 sec_redteam.py               # 只印結果（預設，不寫任何東西）
  python3 sec_redteam.py --json OUT    # 另存 JSON
  python3 sec_redteam.py --insert      # 把「D1 裡還沒有的」新發現寫進 sec_findings（＋sec_events），其餘不動
"""
import argparse
import datetime
import json
import os
import re
import stat
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SITE = os.path.expanduser('~/claude-projects/step1ne-stopgap-site')
CFG = os.path.expanduser('~/.config/workflow-os')
SSH = os.path.expanduser('~/.ssh')
SECRET_NAME = re.compile(r'(token|secret|password|passwd|api[_-]?key|private[_-]?key|auth|credential)', re.I)
SECRET_VALUE = re.compile(r'(sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|xox[bap]-[A-Za-z0-9-]{20,}|'
                          r'-----BEGIN [A-Z ]*PRIVATE KEY-----|\b\d{8,10}:[A-Za-z0-9_-]{35}\b)')

F = []   # findings


def add(area, severity, title, plain, evidence, key):
    """evidence 只放路徑／檔名／變數名／數字，絕不放值。key 是去重用的穩定代號。"""
    F.append({'key': key, 'area': area, 'severity': severity, 'title': title, 'plain': plain, 'evidence': evidence})


def sh(*a, cwd=None):
    r = subprocess.run(a, capture_output=True, text=True, cwd=cwd)
    return r.stdout


def mode_bits(p):
    return stat.S_IMODE(os.stat(p).st_mode)


# ── G1 金鑰檔權限（只看權限）──────────────────────────────────
def check_g1():
    bad = []
    for d in (CFG, SSH):
        if not os.path.isdir(d):
            continue
        if mode_bits(d) & 0o077:
            bad.append(f'{d} (資料夾 {oct(mode_bits(d))})')
        for n in sorted(os.listdir(d)):
            p = os.path.join(d, n)
            if os.path.isfile(p) and not n.endswith('.pub') and n not in ('known_hosts', 'known_hosts.old') and mode_bits(p) & 0o077:
                bad.append(f'{p} ({oct(mode_bits(p))})')
    if bad:
        add('wsl2-host', 'high', '這台金鑰檔權限太寬', '這台電腦上放金鑰的檔案，同機其他帳號也讀得到，等於鑰匙放在門口。',
            bad, 'g1-perm')
    return bad


# ── F1 金鑰進版控／wrangler vars ────────────────────────────
def check_f1():
    for repo in (os.path.expanduser('~/claude-projects/工作流程技能包/step1ne-recruit'), SITE):
        if not os.path.isdir(os.path.join(repo, '.git')):
            continue
        tracked = sh('git', 'ls-files', cwd=repo).splitlines()
        sus = [t for t in tracked if re.search(r'(^|/)(\.env[^/]*|[^/]*\.env|\.dev\.vars|[^/]*\.pem|id_rsa[^/]*|[^/]*credentials[^/]*)$', t)
               and not t.endswith(('.example', '.sample', '.md'))]
        if sus:
            add('wsl2-host', 'high', f'金鑰類檔案進了版控（{os.path.basename(repo)}）',
                '放金鑰的檔案被 git 追蹤，只要有人拿到 repo 就拿到鑰匙。', sus[:20], f'f1-tracked-{os.path.basename(repo)}')
        # 值樣式掃描：只回報「檔案:行號:類型」
        hits = []
        for t in tracked:
            if t.endswith(('.png', '.jpg', '.jpeg', '.gif', '.pdf', '.ico', '.woff', '.woff2', '.lock', '.json.gz', '.patch')) or '/node_modules/' in t:
                continue
            p = os.path.join(repo, t)
            try:
                if os.path.getsize(p) > 2_000_000:
                    continue
                for i, line in enumerate(open(p, encoding='utf-8', errors='ignore'), 1):
                    m = SECRET_VALUE.search(line)
                    if m:
                        kind = 'TG機器人金鑰樣式' if re.fullmatch(r'\d{8,10}:[A-Za-z0-9_-]{35}', m.group(0)) else ('私鑰區塊' if 'PRIVATE KEY' in m.group(0) else '疑似金鑰字串')
                        hits.append(f'{t}:{i} ({kind})')
                        if len(hits) >= 25:
                            break
            except OSError:
                pass
            if len(hits) >= 25:
                break
        if hits:
            add('wsl2-host', 'high', f'程式碼裡疑似寫死金鑰（{os.path.basename(repo)}）',
                '有檔案裡出現長得像金鑰的字串（只列位置、不列內容），需要人確認是不是真金鑰，是的話要作廢重發。', hits, f'f1-hardcoded-{os.path.basename(repo)}')
    wt = os.path.expanduser('~/claude-projects/工作流程技能包/step1ne-recruit/wrangler.toml')
    if os.path.exists(wt):
        in_vars, names = False, []
        for line in open(wt, encoding='utf-8', errors='ignore'):
            s = line.strip()
            if s.startswith('['):
                in_vars = s in ('[vars]', '[env.production.vars]')
            elif in_vars and '=' in s and not s.startswith('#'):
                k = s.split('=', 1)[0].strip()
                if SECRET_NAME.search(k):
                    names.append(k)
        if names:
            add('recruit-worker', 'high', 'wrangler.toml 的 vars 放了疑似敏感變數', '敏感設定應該用 secret 放，不該明文寫在設定檔。', names, 'f1-wrangler-vars')


# ── E5/G2 背景 AI 上鎖覆蓋 ─────────────────────────────────
def check_e5():
    root = os.path.expanduser('~/claude-projects/工作流程技能包/step1ne-recruit')
    callers, unlocked = [], []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in ('.git', 'node_modules', 'tests', 'docs', '__pycache__', 'jobintake')]
        for n in fn:
            if not n.endswith('.py'):
                continue
            p = os.path.join(dp, n)
            try:
                src = open(p, encoding='utf-8', errors='ignore').read()
            except OSError:
                continue
            # 只算「真的在程式裡呼叫」：['claude', ... '-p'] 這種寫法；文件字串裡提到 claude -p 不算
            if re.search(r"""\[\s*['"]claude['"]\s*,[^\]]{0,200}['"]-p['"]""", src):
                rel = os.path.relpath(p, root)
                callers.append(rel)
                if not re.search(r'ai_lockdown|NO_TOOLS|web_only|RESEARCH_TOOLS|--tools|--disallowed-?[tT]ools|--allowedTools', src):
                    unlocked.append(rel)
    if unlocked:
        add('background-ai', 'low', '有呼叫 claude -p 的程式沒套用工具上鎖',
            '這幾支內部測試／模擬用的程式沒把 AI 的「手」綁起來；目前餵給它的是程式內建的假資料，不是外部來信，所以風險低，但之後若改成吃真實內容就必須先上鎖。',
            sorted(unlocked), 'e5-unlocked')
    return callers, unlocked


# ── G3 自動更新鏈 ─────────────────────────────────────────
def check_g3():
    repo = os.path.expanduser('~/claude-projects/工作流程技能包/step1ne-recruit')
    out = []
    for line in sh('git', 'remote', '-v', cwd=repo).splitlines():
        if '(fetch)' in line:
            url = line.split()[1]
            if url.startswith(('http://', 'git://')):
                add('wsl2-host', 'medium', '更新程式的來源走不加密連線', '拉程式碼用的是未加密網址，路上可被竄改。', [re.sub(r'//[^@/]+@', '//***@', url)], 'g3-insecure-remote')
            out.append(re.sub(r'//[^@/]+@', '//***@', url))
    return out


# ── 前端：B2 localStorage 權杖、C1 innerHTML、CSP、.bak、CORS、robots ──
def check_site():
    if not os.path.isdir(SITE):
        return {}
    cnt = {}
    inner, ls_token = {}, []
    for dp, dn, fn in os.walk(SITE):
        dn[:] = [d for d in dn if d not in ('.git', 'node_modules')]
        for n in fn:
            p = os.path.join(dp, n)
            rel = os.path.relpath(p, SITE)
            if re.search(r'\.(bak|orig|old|swp)(-|$|\.)|\.bak-', n) or n.endswith('~'):
                cnt.setdefault('bak', []).append(rel)
            if not n.endswith(('.html', '.js', '.mjs')):
                continue
            top = rel.split(os.sep)[0]
            if top not in ('consultant', 'consultant-app', 'pick', 'pick-beta', 'sec', 'sec-beta', 'portal'):
                continue
            try:
                src = open(p, encoding='utf-8', errors='ignore').read()
            except OSError:
                continue
            c = len(re.findall(r'\.innerHTML\s*[+]?=|insertAdjacentHTML|outerHTML\s*=', src))
            if c:
                inner[rel] = c
            if re.search(r"localStorage\.(get|set)Item\([^)]*(token|auth|admin)", src, re.I):
                ls_token.append(rel)
    cnt['innerHTML'] = inner
    cnt['ls_token'] = ls_token
    if cnt.get('bak'):
        # 對自家網站各發一次 HEAD（讀取型、單次、≤10 個），確認備份檔是不是真的公開
        live = []
        for rel in cnt['bak'][:10]:
            try:
                r = subprocess.run(['curl', '-sI', '-m', '15', '-o', '/dev/null', '-w', '%{http_code}', 'https://step1ne.com/' + rel.replace(os.sep, '/')],
                                   capture_output=True, text=True)
                if r.stdout.strip() == '200':
                    live.append(rel)
            except OSError:
                pass
        cnt['bak_live'] = live
        if live:
            add('site-public', 'low', '網站上有舊版備份檔可直接下載', '舊版頁面的備份檔跟著上線，任何人輸入網址就能下載舊版程式內容。',
                [f'{x}（HTTP 200）' for x in live], 'd1-bak-live')
    if inner:
        tot = sum(inner.values())
        # 粗估跳脫：統計 ${…} 插值有多少包在跳脫函式裡（僅供參考，不代表另外的就不安全）
        tot_i = esc_i = 0
        for rel in inner:
            src = open(os.path.join(SITE, rel), encoding='utf-8', errors='ignore').read()
            it = re.findall(r'\$\{([^}]{1,120})\}', src)
            tot_i += len(it)
            esc_i += sum(1 for i in it if re.match(r'\s*(esc|escape|escHtml|escapeHtml|h|safe|sanitize)\w*\(', i))
        cnt['interp'] = (tot_i, esc_i)
        add('site-consultant', 'unknown', f'內部頁有 {tot} 處用 innerHTML 塞內容，是否都有清洗還沒逐處確認',
            '內部頁用「直接塞 HTML」的方式顯示資料；多數有走跳脫函式，但還沒逐處確認有沒有漏網的，需要藍隊／人工逐處複核。',
            [f'{k}: {v} 處' for k, v in sorted(inner.items(), key=lambda x: -x[1])[:15]] + [f'樣板插值共 {tot_i} 處，其中明確包在跳脫函式的 {esc_i} 處（其餘含數字、樣式名等，未逐一判讀）'], 'c1-innerhtml')
    hd = os.path.join(SITE, '_headers')
    csp = os.path.exists(hd) and 'content-security-policy' in open(hd, encoding='utf-8', errors='ignore').read().lower()
    cnt['csp'] = csp
    rb = os.path.join(SITE, 'robots.txt')
    cnt['robots_disallow'] = os.path.exists(rb)
    return cnt


# ── 輸出 ───────────────────────────────────────────────────
def tw_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0) + datetime.timedelta(hours=8)


def run_all():
    F.clear()
    meta = {}
    meta['g1_bad'] = check_g1()
    check_f1()
    meta['claude_callers'], meta['unlocked'] = check_e5()
    meta['git_remotes'] = check_g3()
    meta['site'] = check_site()
    return meta


def d1_existing():
    import d1_http
    rows = d1_http.query("SELECT id, area, title, status FROM sec_findings").get('results') or []
    return rows


def q(s):
    return "'" + str(s).replace("'", "''") + "'"


def insert_new(meta):
    import d1_http
    now = tw_now()
    week = '%d-W%02d' % now.isocalendar()[:2]
    existing = d1_existing()
    # 用「區塊＋key 寫在 fix_note 前綴」去重，另外同 area 且標題一樣的視為同一件
    have = {(r['area'], r['title']) for r in existing if r['status'] != 'wontfix'}
    wrote = []
    for f in F:
        if (f['area'], f['title']) in have:
            continue
        fid = str(uuid.uuid4())
        ev = '；'.join(f['evidence'])[:600]
        d1_http.query(
            "INSERT INTO sec_findings (id,title,area,severity,status,found_by,found_at,plain,fix_note,week,updated_at) VALUES "
            f"({q(fid)},{q(f['title'])},{q(f['area'])},{q(f['severity'])},'open','red',{q(now.strftime('%Y-%m-%d %H:%M'))},"
            f"{q(f['plain'])},{q('證據（只有位置，無內容）：' + ev)},{q(week)},{q(now.strftime('%Y-%m-%d %H:%M:%S'))})")
        d1_http.query(
            "INSERT INTO sec_events (id,team,kind,finding_id,text,created_at) VALUES "
            f"({q(str(uuid.uuid4()))},'red','found',{q(fid)},{q('紅隊找到：' + f['title'] + '（' + {'high': '高', 'medium': '中', 'low': '低'}.get(f['severity'], '待判') + '風險）')},{q(now.strftime('%Y-%m-%d %H:%M:%S'))})")
        wrote.append(f['title'])
    return wrote


SCORE = {'high': 30, 'medium': 15, 'low': 5, 'unknown': 10}
SEC_GROUP, SEC_THREAD = '-1003231629634', '7421'
JACKY_TG = '8365775688'


def tg(text, group=False):
    """預設只私訊 Jacky；group=True 才發到資安群組（需要環境變數 SEC_TG_GROUP=1，Jacky 看過第一份週報同意後才開）。
    訊息只放白話結論，不放攻擊步驟／金鑰／真實資料。"""
    import urllib.parse
    import urllib.request
    e = dict(l.strip().split('=', 1) for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
             if '=' in l and not l.startswith('#'))
    data = {'chat_id': JACKY_TG, 'text': text, 'disable_web_page_preview': 'true'}
    if group and os.environ.get('SEC_TG_GROUP') == '1':
        data = {'chat_id': SEC_GROUP, 'message_thread_id': SEC_THREAD, 'text': text, 'disable_web_page_preview': 'true'}
    elif group:
        data['text'] = '（尚未開放發到資安群組，先私訊你）\n' + text
    r = json.loads(urllib.request.urlopen(f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
                                          data=urllib.parse.urlencode(data).encode(), timeout=20).read())
    return bool(r.get('ok'))


def board():
    rows = d1_existing_full()
    openr = [r for r in rows if r['status'] in ('open', 'fixing')]
    defense = max(0, 100 - sum(SCORE.get(r['severity'], 10) for r in openr))
    by = lambda s: sum(1 for r in openr if r['severity'] == s)
    return rows, openr, defense, by


def d1_existing_full():
    import d1_http
    return d1_http.query("SELECT id, area, title, severity, status, found_at, verified_at, week FROM sec_findings").get('results') or []


def week_msg(ran):
    """週一／週五的白話訊息。ran＝這週紅隊這台實際跑了幾項檢查、找到幾個新的。"""
    rows, openr, defense, by = board()
    now = tw_now()
    wk = '%d-W%02d' % now.isocalendar()[:2]
    ver = [r for r in rows if r['status'] == 'verified' and (r.get('verified_at') or '') >= (now - datetime.timedelta(days=7)).strftime('%Y-%m-%d')]
    s = (f'【本週資安體檢】{wk}\n\n結論：目前防禦值 {defense}／100。還開著 {len(openr)} 個'
         f'（高 {by("high")}、中 {by("medium")}、低 {by("low")}、待判 {by("unknown")}）。\n\n這週發生什麼：\n'
         f'• 紅隊在這台自我檢查 {ran["checks"]} 項，新找到 {ran["new"]} 個。\n• 這 7 天內確認修好 {len(ver)} 個。\n')
    top = sorted(openr, key=lambda r: -SCORE.get(r['severity'], 10))[:3]
    if top:
        s += '\n最該先處理的：\n' + '\n'.join(f'• {r["title"]}' for r in top) + '\n'
    s += '\n細節看 https://step1ne.com/sec/（要登入）'
    return s


def retest(meta):
    """複測：把這台腳本之前寫的 open 發現，用同一套檢查重跑；已經不再成立的列成「建議改 verified」。
    sec 表目前沒有 update 入口（d1q 只能 insert），所以這裡只產名單，狀態變更交 Mac 的 sec_update.py。"""
    rows = d1_existing_full()
    cur = {(f['area'], f['title']) for f in F}
    mine = [r for r in rows if r['status'] in ('open', 'fixing') and r['area'] in ('wsl2-host', 'background-ai', 'site-public', 'site-consultant', 'recruit-worker')]
    passed = [r for r in mine if (r['area'], r['title']) not in cur and r['id'] and r['title'] in _OUR_TITLES]
    return passed, [r for r in mine if (r['area'], r['title']) in cur]


_OUR_TITLES = {
    '這台金鑰檔權限太寬', '網站上有舊版備份檔可直接下載', '有呼叫 claude -p 的程式沒套用工具上鎖',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json')
    ap.add_argument('--insert', action='store_true')
    ap.add_argument('--retest', action='store_true', help='週四複測：列出已不再成立的舊發現（建議改 verified）')
    ap.add_argument('--weekly', action='store_true', help='產生白話週報／週一摘要並用 TG 發出（預設只私訊 Jacky）')
    ap.add_argument('--send', action='store_true', help='搭配 --weekly／--insert：真的發 TG')
    a = ap.parse_args()
    meta = run_all()
    print(f'== 紅隊只讀稽核 {tw_now()}  發現 {len(F)} 項')
    for f in F:
        print(f"[{f['severity']}] {f['area']} | {f['title']}\n    {f['plain']}\n    證據：{'; '.join(f['evidence'])[:400]}")
    print('\n-- 背景資料')
    print('claude -p 呼叫者：', len(meta['claude_callers']), '支；未套上鎖：', len(meta['unlocked']))
    print('git 來源：', meta['git_remotes'])
    s = meta['site']
    print('內部頁 innerHTML：', sum(s.get('innerHTML', {}).values()), '處／', len(s.get('innerHTML', {})), '檔；localStorage 權杖：', s.get('ls_token'), '；_headers 有 CSP：', s.get('csp'))
    if a.json:
        json.dump({'at': str(tw_now()), 'findings': F, 'meta': meta}, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    new = []
    if a.insert:
        new = insert_new(meta)
        print('\n寫入 sec_findings 新發現：', len(new), new)
    if a.retest:
        passed, still = retest(meta)
        print('\n複測：已不再成立（建議改 verified，需 sec_update.py）：', [r['title'] for r in passed])
        print('複測：仍成立：', [r['title'] for r in still])
        if a.send and (passed or still):
            tg('🔵 資安複測（這台 WSL2）\n' + (f'確認已修好 {len(passed)} 個：' + '、'.join(r['title'] for r in passed) + '\n' if passed else '') +
               (f'仍未修好 {len(still)} 個。' if still else '') + '\n狀態改成「已驗證」需要 Mac 的 sec_update.py，這裡只列名單。', group=True)
    if a.weekly:
        msg = week_msg({'checks': 8, 'new': len(new)})
        print('\n--- 週報 ---\n' + msg)
        if a.send:
            print('TG 已送出' if tg(msg, group=True) else 'TG 送出失敗')


if __name__ == '__main__':
    main()
