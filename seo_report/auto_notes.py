#!/usr/bin/env python3
"""週報的兩段人工區自動產生（E33，2026-10-12 Jacky）：「這週做了什麼」(done) 與「回收日與下一步」(next)。

用法：auto_notes.py [--date YYYY-MM-DD] [--out 目錄] [--print] [--no-llm]
  寫到 <out>/notes/<date>.json（預設 out＝~/aijob-automation/state/seo_report，不寫進 repo，免得擋 git pull）
  如果 repo 的 seo_report/notes/<date>.json 已經有人工版 → 什麼都不做（人工版優先）。

done 的證據（報告日前 7 天，週一～週日）：
  1. seo_report/actions.jsonl（log_action.py done 記的，原文照用、排最前面）
  2. 官網 repo（step1ne-stopgap-site；找指向 step1nework016-eng/step1ne 的 remote，先 fetch 只讀）
     只挑動到文章／關於我們／企業頁／sitemap／llms.txt／robots／轉址的 commit，後台、滑卡、資安那些不算
  3. step1ne-recruit 的 docs/wsl2/回報/ 新增、檔名跟 SEO／GEO／搜尋有關的
  → 交給 claude -p 改寫成 ≤6 句白話；CLI 失敗就直接列 commit 標題（不會空著）。
next：seo_report/followups.json 裡還沒做完的；日期已過的標「到期，要回頭看」。
geo／geo_note／extra_leads：沿用上一份 notes；overrides.json 有更新的就用新的。
"""
import argparse, json, os, re, subprocess, sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
RECRUIT = os.path.dirname(HERE)
OUT = os.path.expanduser(os.environ.get('SEO_REPORT_OUT') or '~/aijob-automation/state/seo_report')
SITE_REPO = os.path.expanduser(os.environ.get('SEO_SITE_REPO') or '~/claude-projects/step1ne-stopgap-site')
SITE_URL = 'https://github.com/step1nework016-eng/step1ne.git'
SITE_CACHE = os.path.expanduser('~/aijob-automation/state/step1ne-site-ro')
TAGS = ['新文章', '補內容', '改標題', '信任', '收錄', '修正']

SEO_PATH = re.compile(r'^(articles/|about/|commission-recruiting/|hiring-assessment/|faq/|success-stories/|reviews/'
                      r'|sitemap\.xml$|llms\.txt$|robots\.txt$|_redirects$|index\.html$|[0-9a-f]{32}\.txt$)')
SEO_WORD = re.compile(r'新文章|文章|標題|meta|schema|sitemap|llms|IndexNow|收錄|轉址|301|內鏈|結構化|關於我們|常見問題|FAQ', re.I)
NOT_SEO = re.compile(r'顧問後台|frontend_v2|滑卡|pick|資安|/sec|週報|seo-report|apply-beta|應徵表單|面談頁|上架職缺|職缺頁|關閉')


def sh(args, cwd=None, timeout=120):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[:300])
    return r.stdout


def site_ref():
    """回傳 (repo 路徑, 要看的 ref)。只做 fetch／clone，不動任何工作目錄。"""
    repo = SITE_REPO if os.path.isdir(os.path.join(SITE_REPO, '.git')) else None
    if repo:
        for line in sh(['git', 'remote', '-v'], repo).splitlines():
            name, url = line.split()[:2]
            if re.search(r'step1nework016-eng/step1ne(\.git)?$', url):
                try:
                    sh(['git', 'fetch', '-q', name, 'main'], repo)
                except Exception as e:
                    print('⚠️ 官網 repo fetch 失敗，改看本機紀錄：', e, file=sys.stderr)
                    return repo, 'HEAD'
                return repo, f'{name}/main'
        return repo, 'HEAD'
    # 這台沒有官網 repo → 只讀複本（不抓檔案內容，只要歷史）
    try:
        if not os.path.isdir(SITE_CACHE):
            sh(['git', 'clone', '-q', '--filter=blob:none', '--no-checkout', SITE_URL, SITE_CACHE], timeout=300)
        else:
            sh(['git', 'fetch', '-q', 'origin', 'main'], SITE_CACHE)
        return SITE_CACHE, 'origin/main'
    except Exception as e:
        print('⚠️ 拿不到官網 repo 歷史：', e, file=sys.stderr)
        return None, None


def site_commits(since, until):
    repo, ref = site_ref()
    if not repo:
        return []
    raw = sh(['git', 'log', ref, f'--since={since} 00:00', f'--until={until} 00:00', '--date=short',
              '--name-only', '--pretty=format:@@%h|%ad|%s'], repo)
    out = []
    for blk in raw.split('@@')[1:]:
        lines = [l for l in blk.strip().splitlines() if l.strip()]
        h, d, subj = lines[0].split('|', 2)
        files = lines[1:]
        if NOT_SEO.search(subj):
            continue
        if any(SEO_PATH.match(f) for f in files) or SEO_WORD.search(subj):
            out.append({'date': d, 'subject': subj, 'files': files[:6]})
    return out


def report_titles(since, until):
    """step1ne-recruit 的 docs/wsl2/回報/ 這週新增、跟 SEO 有關的檔案 → 第一行標題。"""
    try:
        raw = sh(['git', 'log', f'--since={since} 00:00', f'--until={until} 00:00', '--diff-filter=A',
                  '--name-only', '--pretty=format:', '--', 'docs/wsl2/回報/'], RECRUIT)
    except Exception:
        return []
    out = []
    for f in sorted({l.strip() for l in raw.splitlines() if l.strip()}):
        if not re.search(r'SEO|GEO|GSC|搜尋|週報|文章|E33', f, re.I):
            continue
        p = os.path.join(RECRUIT, f)
        if os.path.exists(p):
            first = next((l.lstrip('# ').strip() for l in open(p, encoding='utf-8') if l.strip()), '')
            out.append(first[:120])
    return out


def logged_actions(since, rep):
    out = []
    try:
        for l in open(os.path.join(HERE, 'actions.jsonl'), encoding='utf-8'):
            if l.strip():
                x = json.loads(l)
                if since <= x.get('date', '') < rep:   # 跟官網紀錄同一個區間：上週一～週日
                    out.append([x['tag'], x['text']])
    except OSError:
        pass
    return out


def fallback_done(actions, commits):
    done = list(actions)
    for c in commits:
        s = re.sub(r'（[^）]*frontend[^）]*）', '', c['subject'])
        tag = ('新文章' if '新文章' in s else '收錄' if re.search(r'IndexNow|sitemap|收錄|robots', s, re.I)
               else '信任' if '關於我們' in s else '改標題' if re.search(r'標題|title|meta', s, re.I)
               else '補內容' if re.search(r'補|新增|加', s) else '修正')
        done.append([tag, s[:70]])
    return done[:6]


def llm_done(actions, commits, reports):
    if not (commits or reports):
        return None
    ev = '\n'.join([f'- [已記錄] {t}：{x}' for t, x in actions] +
                   [f'- [官網 {c["date"]}] {c["subject"]}（動到：{", ".join(c["files"][:3])}）' for c in commits] +
                   [f'- [回報] {r}' for r in reports])
    prompt = (
        '你在幫獵頭公司老闆（不是工程師）寫官網「搜尋成效週報」的「這週做了什麼」。\n'
        '下面是這週官網的改動紀錄。請整理成最多 6 條，每條是 [分類, 一句白話]。\n'
        f'分類只能用：{"／".join(TAGS)}。\n'
        '規則：標 [已記錄] 的一定要保留、原文照用；同一件事的多個改動合成一條；文章名用〈〉；'
        '不要工程術語（commit、schema、flex、frontend 之類都不准出現）；不要編紀錄裡沒有的事；每條 40 字內。\n'
        '只輸出 JSON 陣列，例如 [["新文章","〈招募成本怎麼算？〉上線"]]，不要其他文字。\n\n' + ev)
    cmd = ['claude', '-p', '--model', os.environ.get('SEO_NOTES_MODEL', 'claude-sonnet-5'), '--strict-mcp-config',
           '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '', '--output-format', 'text']
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=240)
        m = re.search(r'\[\s*\[.*\]\s*\]', r.stdout, re.S)
        items = json.loads(m.group(0)) if m else None
        if not (isinstance(items, list) and items and all(
                isinstance(x, list) and len(x) == 2 and all(isinstance(y, str) and y.strip() for y in x) for x in items)):
            raise ValueError('格式不對：' + r.stdout[:200] + r.stderr[:200])
        return [[x[0] if x[0] in TAGS else '修正', x[1].strip()] for x in items][:6]
    except Exception as e:
        print('⚠️ claude 整理失敗，改用原始紀錄：', e, file=sys.stderr)
        return None


def next_items(rep):
    try:
        fu = json.load(open(os.path.join(HERE, 'followups.json'), encoding='utf-8'))
    except (OSError, ValueError):
        return []
    open_ = [x for x in fu if not x.get('done')]
    open_.sort(key=lambda x: (x.get('due') is None, x.get('due') or ''))
    out = []
    for x in open_[:6]:
        if x.get('due') and x['due'] < rep:
            out.append([f'{int(x["due"][5:7])}/{x["due"][8:10]}（到期）', '【到期，要回頭看】' + x['what']])
        else:
            out.append([x['label'], x['what']])
    return out


def prev_notes(rep):
    dirs = [os.path.join(HERE, 'notes'), os.path.join(OUT, 'notes')]
    names = sorted({f for d in dirs if os.path.isdir(d) for f in os.listdir(d)
                    if re.match(r'\d{4}-\d{2}-\d{2}\.json$', f) and f < f'{rep}.json'})
    if not names:
        return None, {}
    f = names[-1]
    p = next(os.path.join(d, f) for d in dirs if os.path.exists(os.path.join(d, f)))
    return f[:-5], json.load(open(p, encoding='utf-8'))


def build(rep, use_llm=True):
    rd = date.fromisoformat(rep)
    since = (rd - timedelta(days=7)).isoformat()
    actions = logged_actions(since, rep)
    commits = site_commits(since, rep)
    reports = report_titles(since, rep)
    done = (llm_done(actions, commits, reports) if use_llm else None) or fallback_done(actions, commits)
    pdate, prev = prev_notes(rep)
    n = {'done': done, 'next': next_items(rep),
         'geo': prev.get('geo', []), 'geo_note': prev.get('geo_note', ''), 'extra_leads': prev.get('extra_leads', '')}
    try:
        ov = json.load(open(os.path.join(HERE, 'overrides.json'), encoding='utf-8'))
    except (OSError, ValueError):
        ov = {}
    for k in ('geo_note', 'extra_leads'):
        if ov.get(k) and (not pdate or ov[k]['updated'] >= pdate):
            n[k] = ov[k]['text']
    if ov.get('geo') and ov['geo'].get('rows') and (not pdate or ov['geo']['updated'] >= pdate):
        n['geo'] = ov['geo']['rows']
    n['auto'] = {'generated': date.today().isoformat(), 'from_notes': pdate,
                 'evidence': {'logged': len(actions), 'site_commits': len(commits), 'reports': len(reports)}}
    return n


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--date'); ap.add_argument('--out'); ap.add_argument('--print', action='store_true')
    ap.add_argument('--no-llm', action='store_true')
    a = ap.parse_args()
    if a.out:
        OUT = os.path.abspath(os.path.expanduser(a.out))
    rep = a.date or date.today().isoformat()
    if os.path.exists(os.path.join(HERE, 'notes', f'{rep}.json')):
        print('repo 裡已有人工版 notes/%s.json，跳過自動產生' % rep); sys.exit(0)
    n = build(rep, use_llm=not a.no_llm)
    if a.print:
        print(json.dumps(n, ensure_ascii=False, indent=1))
    d = os.path.join(OUT, 'notes'); os.makedirs(d, exist_ok=True)
    json.dump(n, open(os.path.join(d, f'{rep}.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    print('ok notes', rep, 'done', len(n['done']), 'next', len(n['next']), '->', os.path.join(d, f'{rep}.json'))
