#!/usr/bin/env python3
"""把網域口袋名單（CSV 正本）同步到內部看板 https://step1ne.com/seo-report/#domains（E34）。

寫到 D1 site_settings key='domain_pocket'（JSON）。看板登入後從後台 GET /admin/settings 讀，
頁面原始碼裡不放任何網域資料。

用法：
  python3 sync_dashboard.py              同步（agent_queue.py finish 最後會自動叫）
  python3 sync_dashboard.py --dry-run    只印出會寫什麼，不碰 D1
  python3 sync_dashboard.py --tg         同步後，有「新的乾淨網域」才發 TG 給 Jacky 私訊（每日排程最後用）
  python3 sync_dashboard.py --restore-csv  資料夾的 CSV 不見時，從 D1 那份還原（D1 存的是全部欄位）

D1 金鑰讀 ~/.config/workflow-os/cf.env；TG 讀 ~/.config/workflow-os/step1ne-tg.env。
"""
import csv, datetime, json, os, sys, urllib.request, urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from df_paths import CACHE, CSVF

DB_ID = '67077b4b-42d9-4086-8d04-b3658a89cffd'   # step1ne-recruit
KEY = 'domain_pocket'
JACKY = '8365775688'
URL = 'https://step1ne.com/seo-report/#domains'
TODAY = str(datetime.date.today())


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


def num(v):
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return None


def prelife_level(text):
    t = (text or '').strip()
    for lv in ('乾淨', '有前科', '查不到'):
        if t.startswith(lv):
            return lv
    return '待查' if not t else '其他'


def verdict(status, level):
    s = (status or '').strip()
    if s == '已買':
        return '已買'
    if level == '有前科':
        return '不建議'
    if s == '預計購買':
        return '預計購買'
    return '可買'


def lines(p):
    return [l for l in p.open(encoding='utf-8') if l.strip()] if p.exists() else []


def queue_status():
    done = {l.split('\t')[0] for l in lines(CACHE / '分數結果.tsv')}
    q = [l.split('\t')[0] for l in lines(CACHE / '佇列.tsv')]
    log = lines(CACHE / '每日紀錄.tsv')
    last = max((l.split('\t')[0] for l in log), default='')
    return {'queued': sum(1 for d in q if d not in done), 'queue_total': len(q),
            'scored_today': sum(1 for l in log if l.startswith(TODAY)),
            'total_scored': len(done), 'last_scored_date': last}


def build():
    rows, header = [], None
    if CSVF.exists():
        rd = csv.DictReader(CSVF.open(encoding='utf-8-sig'))
        rows, header = list(rd), rd.fieldnames
    out = []
    for r in rows:
        if not (r.get('網域') or '').strip():
            continue
        pl = (r.get('前世') or '').strip()
        lv = prelife_level(pl)
        note = pl.split('｜', 1)[1].strip() if '｜' in pl else ('' if lv in ('乾淨', '有前科', '查不到', '待查') else pl)
        out.append({
            'domain': r['網域'].strip(), 'batch': r.get('批次', ''),
            'score': num(r.get('Ubersuggest權重分數')), 'refs': num(r.get('連入網站數')),
            'links': num(r.get('連結總數')), 'nofollow': num(r.get('nofollow連結數')),
            'dr': num(r.get('Ahrefs DR')), 'registrable_checked': r.get('可註冊(查核日)', ''),
            'status': r.get('狀態', ''), 'verdict': verdict(r.get('狀態'), lv),
            'prelife': lv, 'prelife_note': note, 'prelife_raw': pl,
            'price_cf': r.get('Cloudflare 價格', ''), 'price_gd': r.get('GoDaddy 價格', ''),
            'bought_at': r.get('購買平台', ''),
            'godaddy': r.get('GoDaddy 購買', ''), 'cloudflare': r.get('Cloudflare 購買', ''),
            'wayback': r.get('查前世', ''),
        })
    return {'updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'rows': out,
            'queue_status': queue_status(), 'csv_header': header}


def previous():
    """上一份同步內容；還沒有＝{}；讀失敗＝None（這時不發 TG，免得把舊的再通知一次）。"""
    try:
        r = d1("SELECT value FROM site_settings WHERE key=?", [KEY])
        return json.loads(r[0]['value']) if r and r[0].get('value') else {}
    except Exception as ex:
        print('⚠️ 讀不到上一份：', str(ex)[:200])
        return None


def clean_ok(x):
    """值得通知的：前世乾淨、分數 20–40、還沒買。"""
    return x['prelife'] == '乾淨' and x['score'] is not None and 20 <= x['score'] <= 40 and x['verdict'] != '已買'


def tg(text):
    e = _env('step1ne-tg.env')
    tok = e.get('TG_BOT_TOKEN') or e.get('TELEGRAM_BOT_TOKEN')
    body = {'chat_id': JACKY, 'text': text, 'disable_web_page_preview': True}
    req = urllib.request.Request(f'https://api.telegram.org/bot{tok}/sendMessage', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    json.load(urllib.request.urlopen(req, timeout=30))


def restore_csv():
    prev = previous() or {}
    hdr, rows = prev.get('csv_header'), prev.get('rows') or []
    if not hdr:
        sys.exit('D1 那份沒有欄位資料，無法還原')
    if CSVF.exists() and sum(1 for _ in CSVF.open(encoding='utf-8-sig')) > 1:
        sys.exit(f'{CSVF} 已經有資料，不覆蓋')
    m = {'網域': 'domain', '批次': 'batch', 'Ubersuggest權重分數': 'score', '連入網站數': 'refs', '連結總數': 'links',
         'nofollow連結數': 'nofollow', 'Ahrefs DR': 'dr', '可註冊(查核日)': 'registrable_checked',
         'GoDaddy 購買': 'godaddy', 'Cloudflare 購買': 'cloudflare', '查前世': 'wayback', '狀態': 'status',
         'Cloudflare 價格': 'price_cf', 'GoDaddy 價格': 'price_gd', '購買平台': 'bought_at', '前世': 'prelife_raw'}
    with CSVF.open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(hdr)
        for x in rows:
            w.writerow(['' if x.get(m.get(h)) is None else x.get(m.get(h)) for h in hdr])
    print(f'還原 {len(rows)} 筆 → {CSVF}')


def main():
    a = sys.argv[1:]
    if '--restore-csv' in a:
        return restore_csv()
    data = build()
    qs = data['queue_status']
    if '--dry-run' in a:
        print(json.dumps({**data, 'rows': data['rows'][:3]}, ensure_ascii=False, indent=1))
        print(f"（共 {len(data['rows'])} 筆，dry-run 不寫 D1）")
        return
    prev = previous()
    if prev is None:   # 讀不到就不寫：寫了會把「已通知過」清單洗掉，明天又重發一次
        sys.exit('看板這次沒同步（讀不到後台資料庫），名單本身沒受影響，下次會再試')
    notified = set(prev.get('notified_clean') or [])
    data['notified_clean'] = sorted(notified)
    new_clean = [x for x in data['rows'] if clean_ok(x) and x['domain'] not in notified]
    if '--tg' in a and new_clean:
        new_clean.sort(key=lambda x: -(x['score'] or 0))
        top = '、'.join(f"{x['domain']}（{x['score']} 分）" for x in new_clean[:3]) + ('…' if len(new_clean) > 3 else '')
        msg = (f"🌐 網域獵人：今天多了 {len(new_clean)} 個前世乾淨、可以直接註冊的網域\n"
               f"{top}\n"
               f"DR 還沒查，請到 Ahrefs 查完填在看板上 → {URL}")
        try:
            tg(msg)
            data['notified_clean'] = sorted(notified | {x['domain'] for x in new_clean})
            print('ok tg', len(new_clean))
        except Exception as ex:
            print('⚠️ TG 發送失敗（下次會再試）：', str(ex)[:200])
    d1("INSERT INTO site_settings(key,value,updated_at) VALUES(?,?,datetime('now','+8 hours')) "
       "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
       [KEY, json.dumps(data, ensure_ascii=False)])
    print(f"ok 看板已同步：名單 {len(data['rows'])} 筆；佇列剩 {qs['queued']}、今天查了 {qs['scored_today']}、累計 {qs['total_scored']}")


if __name__ == '__main__':
    main()
