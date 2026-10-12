#!/usr/bin/env python3
"""把週報快照寫進 D1 seo_weekly_reports（同一週重跑會覆蓋，不會多一列），再發一則 TG 摘要（E33）。

用法：publish.py [--date YYYY-MM-DD] [--out 目錄] [--dry-run] [--no-tg]
  --dry-run：只印出「會寫什麼、會發什麼」，不碰 D1、不發 TG。
頁面 https://step1ne.com/seo-report/ 透過後台 /admin/seo-report/index、/get 讀這張表。
TG 去向：D1 tg_routes 有 key='seo_report' 就發那裡；沒有就發 Jacky 私訊（8365775688，跟之前 Mac 排程一樣）。
"""
import argparse, json, os, sys, urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.expanduser(os.environ.get('SEO_REPORT_OUT') or '~/aijob-automation/state/seo_report')
DB_ID = '67077b4b-42d9-4086-8d04-b3658a89cffd'   # step1ne-recruit
JACKY = '8365775688'
URL = 'https://step1ne.com/seo-report/'


def _env(name):
    v = {}
    for l in open(os.path.expanduser(f'~/.config/workflow-os/{name}'), encoding='utf-8'):
        l = l.strip()
        if l and not l.startswith('#') and '=' in l:
            k, _, x = l.partition('='); v[k.strip()] = x.strip().strip('"\'')
    return v


def d1(sql, params=None):
    e = _env('cf.env')
    r = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{e['CLOUDFLARE_ACCOUNT_ID']}/d1/database/{DB_ID}/query",
        data=json.dumps({'sql': sql, 'params': params or []}).encode(),
        headers={'Authorization': 'Bearer ' + e['CLOUDFLARE_API_TOKEN'], 'Content-Type': 'application/json'})
    try:
        return json.load(urllib.request.urlopen(r, timeout=60))['result'][0]['results']
    except urllib.error.HTTPError as ex:
        raise RuntimeError('D1 ' + ex.read().decode()[:300])


def summary(d):
    """三行重點，數字全部從快照讀。"""
    w = d.get('weeks', [])
    ent = [x.get('enterprise', {}).get('c', 0) for x in w]
    now4, prev4 = sum(ent[-4:]), sum(ent[-8:-4])
    pct = f'{(now4 - prev4) / prev4 * 100:+.0f}%' if prev4 else '前期 0'
    kw = {r['q']: r for r in d.get('kw', [])}
    def pos(q):
        r = kw.get(q)
        if not r or r.get('p') is None:
            return f'{q}：這期沒排到'
        was = f'（前期 {r["pp"]}）' if r.get('pp') is not None else ''
        return f'{q}：第 {r["p"]} 名{was}'
    g = d.get('ga4', {})
    lead = lambda x: (x or {}).get('employer_lead_submit', 0) + (x or {}).get('hiring_assessment_lead', 0)
    n = d.get('notes', {})
    due = sum(1 for x in n.get('next', []) if '到期' in x[0])
    return [f'企業端近 4 週點擊 {now4}（前 4 週 {prev4}，{pct}）',
            f'{pos("獵頭公司")}；{pos("台灣獵頭公司推薦")}',
            f'企業需求表單 {lead(g.get("now"))} 筆（前期 {lead(g.get("prev"))}）；這週做了 {len(n.get("done", []))} 件'
            + (f'，{due} 件到期要回頭看' if due else '')]


def tg(text):
    e = _env('step1ne-tg.env')
    tok = e.get('TG_BOT_TOKEN') or e.get('TELEGRAM_BOT_TOKEN')
    chat, thread = JACKY, None
    try:
        r = d1("SELECT chat_id, thread_id FROM tg_routes WHERE key='seo_report'")
        if r and r[0].get('chat_id'):
            chat, thread = str(r[0]['chat_id']), r[0].get('thread_id')
    except Exception:
        pass
    body = {'chat_id': chat, 'text': text, 'disable_web_page_preview': True}
    if thread:
        body['message_thread_id'] = int(thread)
    req = urllib.request.Request(f'https://api.telegram.org/bot{tok}/sendMessage', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    json.load(urllib.request.urlopen(req, timeout=30))
    return chat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date'); ap.add_argument('--out'); ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-tg', action='store_true')
    ap.add_argument('--fail', help='前面步驟失敗時只發失敗通知（內容＝這段文字）')
    a = ap.parse_args()
    out = os.path.abspath(os.path.expanduser(a.out)) if a.out else OUT
    rep = a.date or date.today().isoformat()
    if a.fail:
        msg = f'⚠️ Step1ne 搜尋成效週報（{rep}）這週沒更新成功\n{a.fail}\n頁面還是上一週的：{URL}'
        print(msg)
        if not (a.dry_run or a.no_tg):
            tg(msg)
        return 1
    p = os.path.join(out, 'data', f'{rep}.json')
    d = json.load(open(p, encoding='utf-8'))
    raw = json.dumps(d, ensure_ascii=False)
    src = os.environ.get('SEO_REPORT_SOURCE', 'wsl2-weekly')
    msg = '\n'.join([f'📊 Step1ne 搜尋成效週報已更新（{rep}）'] + summary(d) + [URL])
    if a.dry_run:
        print(f'[dry-run] 會寫入 D1 seo_weekly_reports：report_date={rep} source={src} 大小={len(raw)} 字')
        print('[dry-run] TG 內容：\n' + msg)
        return 0
    d1("INSERT INTO seo_weekly_reports (report_date, data, source, created_at) VALUES (?, ?, ?, datetime('now','+8 hours')) "
       "ON CONFLICT(report_date) DO UPDATE SET data=excluded.data, source=excluded.source, created_at=excluded.created_at",
       [rep, raw, src])
    chk = d1('SELECT length(data) n, source, created_at FROM seo_weekly_reports WHERE report_date=?', [rep])
    if not chk or chk[0]['n'] < 1000:
        raise SystemExit(f'寫入後查不到或太小：{chk}')
    print('ok d1', rep, chk[0])
    if not a.no_tg:
        print('ok tg ->', tg(msg))
    return 0


if __name__ == '__main__':
    sys.exit(main())
