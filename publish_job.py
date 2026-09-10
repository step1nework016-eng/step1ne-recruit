#!/usr/bin/env python3
"""職缺上架器：一份 JSON → 職缺頁、列表卡、sitemap、應徵下拉、D1，一次到位。

為什麼要有這支：2026-08-06 顧問問「我能叫助手上架新職缺嗎」，
查了才發現不行——每一頁職缺都是手刻的 HTML，新增或改 JD 要動六個地方：
  ① /jobs/<slug>/index.html   ② jobs/index.html 的卡片
  ③ sitemap.xml               ④ apply/jobs.json
  ⑤ D1 的 jobs 表（阿財讀這裡）⑥ 部署
當天更新 BIM 的 JD 就是六個地方手改，而且線上有四項是錯的（薪資少報一萬、
年終算兩次、寫了不存在的交通車、學歷門檻寫高）。**手改六個地方，就一定會漏。**

用法：
    python3 publish_job.py <職缺.json> --dry     # 只產檔案不部署，先看
    python3 publish_job.py <職缺.json>           # 產檔＋更新 D1
    python3 publish_job.py <職缺.json> --deploy  # 再加上 git push

⚠️ 這支不會自己編內容。JSON 裡沒有的區塊就不會出現在頁面上——
   寧可頁面短一點，也不要生出客戶沒說過的條件（那會害候選人白跑一趟）。
"""
import os, sys, json, re, argparse, subprocess, importlib.util, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.expanduser('~/claude-projects/step1ne-stopgap-site')
spec = importlib.util.spec_from_file_location('d', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec); spec.loader.exec_module(D)
spec2 = importlib.util.spec_from_file_location('bg', os.path.join(HERE, 'jobtpl', 'body_gen.py'))
BG = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(BG)


def ld_description(j):
    """JobPosting 的 description：Google 會直接顯示這段，要帶結構不能只給一句話。

    2026-08-12 發現的問題：原本只放 intro（一句話），但線上 20 頁的 schema
    描述全是帶 <h3>／<ul> 的完整內容。也就是說，任何人拿這支重產既有職缺，
    都會把描述從完整版降級成一句話——**而且不會有任何警告**。
    這支是唯一該用的更新工具，它自己不能是有損的。
    """
    # 不做 HTML 逃逸——這些欄位本來就允許作者寫 <strong> 之類的標記，
    # 頁面本體（body_gen）也是原樣輸出。這裡逃逸會讓 <strong> 變成可見的文字。
    esc = lambda s: s or ''
    p = [f"<p>{esc(j.get('intro') or j['title'])}</p>"]
    if j.get('duties'):
        p.append('<h3>工作內容</h3>')
        for b in j['duties']:
            if b.get('h'):
                p.append(f"<h4>{esc(b['h'])}</h4>")
            p.append('<ul>' + ''.join(f"<li>{esc(i)}</li>" for i in b.get('items', [])) + '</ul>')
    if j.get('must') or j.get('plus'):
        p.append('<h3>應徵條件</h3>')
        if j.get('must'):
            p.append('必要條件<ul>' + ''.join(f"<li>{esc(i)}</li>" for i in j['must']) + '</ul>')
        if j.get('plus'):
            p.append('加分條件<ul>' + ''.join(f"<li>{esc(i)}</li>" for i in j['plus']) + '</ul>')
    return ''.join(p)


def posted_date(j):
    """刊登日：JSON 有就用 JSON 的；沒有就沿用線上那一頁既有的。

    ⚠️ 不能每次重產都填今天——改一個薪資欄位不該讓 Google 以為這是新缺，
    那是謊報刊登日期。只有真的沒有既有頁面時才用今天。
    """
    if j.get('posted'):
        return j['posted']
    page = os.path.join(SITE, 'jobs', j['slug'], 'index.html')
    if os.path.exists(page):
        m = re.search(r'"datePosted"\s*:\s*"([\d-]+)"', open(page, encoding='utf-8').read())
        if m:
            return m.group(1)
    return __import__('datetime').date.today().isoformat()


def jsonld(j):
    """JobPosting 結構化資料。Google 會直接拿去做職缺搜尋結果。

    ⚠️ 產完一定要 json.loads 驗證再寫檔——2026-08-06 手改時就因為刪一個欄位
    留下多餘逗號，把整段結構化資料弄壞，而且是先寫檔才發現。
    """
    d = {
        "@context": "https://schema.org", "@type": "JobPosting",
        "title": j['title'], "description": ld_description(j),
        "datePosted": posted_date(j),
        "validThrough": j.get('valid_through') or f"{__import__('datetime').date.today().year}-12-31",
        "employmentType": j.get('employment') or ["FULL_TIME"],
        "hiringOrganization": {"@type": "Organization", "name": "Step1ne 德仁管理顧問有限公司",
                               "sameAs": "https://step1ne.com/"},
        "jobLocation": {"@type": "Place", "address": {
            "@type": "PostalAddress", "addressLocality": j.get('locality') or '',
            "addressRegion": j.get('region') or '', "addressCountry": "TW"}},
        "directApply": True,
    }
    # 只有下限也要輸出 baseSalary。來源常寫「待遇面議，經常性薪資達 4 萬元以上」，
    # 那是真實的下限，不是沒資料。原本要 min 與 max 都有才輸出，結果這類職缺
    # 在 Google Jobs 完全沒有薪資資訊（2026-08-12 稽核 20 頁時發現）。
    # ⚠️ 上限沒有就不要填——不能為了湊格式編一個數字。
    if j.get('salary_min') or j.get('salary_max'):
        qv = {"@type": "QuantitativeValue", "unitText": "MONTH"}
        if j.get('salary_min'):
            qv["minValue"] = j['salary_min']
        if j.get('salary_max'):
            qv["maxValue"] = j['salary_max']
        d["baseSalary"] = {"@type": "MonetaryAmount", "currency": "TWD", "value": qv}
    if j.get('benefits'):
        d["jobBenefits"] = j['benefits']
    return json.dumps(d, ensure_ascii=False, indent=2)


def render_page(j):
    tpl = open(os.path.join(HERE, 'jobtpl', 'chrome_top.tpl'), encoding='utf-8').read()
    bot = open(os.path.join(HERE, 'jobtpl', 'chrome_bot.html'), encoding='utf-8').read()
    ld = jsonld(j)
    json.loads(ld)                      # 先驗證，壞的就不要寫出去
    # ⚠️ 不能用 .format()——樣板裡有 gtag 的 JavaScript，那些大括號會被當成佔位符
    # （2026-08-06 第一次跑就炸在 dataLayer）。用明確的字串替換。
    vals = {
        'title': j['page_title'], 'description': j['description'],
        'keywords': j.get('keywords', ''), 'slug': j['slug'],
        'og_title': j.get('og_title') or j['page_title'],
        'og_desc': j.get('og_desc') or j['description'],
        'title_enc': urllib.parse.quote(j['title']), 'jsonld': ld,
    }
    top = tpl
    for k, v in vals.items():
        top = top.replace('{' + k + '}', str(v))
    return top + BG.build_body({**j, 'title_enc': urllib.parse.quote(j['title'])}) + bot


def upsert_d1(j):
    q = D.q
    emp = json.dumps(j.get('employment') or ["FULL_TIME"], ensure_ascii=False)
    D.d1(f"""INSERT INTO jobs (slug,title,client_name,employment,salary_min,salary_max,
             salary_unit,locations,status,years_min,must_skills,notes,cv_mode,updated_at)
        VALUES ({q(j['slug'])},{q(j['title'])},{q(j.get('client_name'))},{q(emp)},
             {q(j.get('salary_min')) if j.get('salary_min') else 'NULL'},
             {q(j.get('salary_max')) if j.get('salary_max') else 'NULL'},'MONTH',
             {q(j.get('locations'))},'open',{j.get('years_min') if j.get('years_min') is not None else 'NULL'},
             {q(j.get('must_skills'))},{q(j.get('notes'))},{q(j.get('cv_mode'))},datetime('now'))
        ON CONFLICT(slug) DO UPDATE SET title=excluded.title, client_name=excluded.client_name,
             employment=excluded.employment, salary_min=excluded.salary_min,
             salary_max=excluded.salary_max, locations=excluded.locations,
             years_min=excluded.years_min, must_skills=excluded.must_skills,
             notes=COALESCE(jobs.notes,'')||char(10)||excluded.notes,
             cv_mode=COALESCE(excluded.cv_mode, jobs.cv_mode),
             updated_at=datetime('now')""")


EMPLOYMENT_LABEL = {'FULL_TIME': '正職', 'PART_TIME': '兼職', 'CONTRACTOR': '承攬',
                    'TEMPORARY': '派遣', 'INTERN': '實習', 'PER_DIEM': '日薪'}


def _money(n):
    n = int(n)
    if n % 10000 == 0:
        return f'{n // 10000} 萬'
    if n % 1000 == 0:
        return f'{n / 10000:g} 萬'
    return f'{n:,} 元'


def card_meta(j):
    """列表卡的條件標籤。規格有 card_meta 就用它（那是編輯自己寫的），
    沒有就從薪資／僱用型態／地點推——後台自動上架的規格從來就沒有
    card_meta，結果 2026-09-07 全站 35 張卡片有 28 張是空的。
    ⚠️ 只放規格裡真的有的東西，缺就少一格，不要為了填滿而編。"""
    if j.get('card_meta'):
        return list(j['card_meta'])
    out = []
    lo, hi = j.get('salary_min'), j.get('salary_max')
    unit = {'MONTH': '月薪', 'YEAR': '年薪', 'HOUR': '時薪', 'DAY': '日薪'}.get(j.get('salary_unit') or 'MONTH', '月薪')
    # 2026-09-07 Jacky 定的規則：
    #   月薪 → 一律只寫下限「N 萬起，依經歷面議」，不寫上限。
    #          寫死區間會變成談薪的天花板，而且實際待遇本來就依經歷核定。
    #   年薪 → 才寫區間（高階職缺的年薪區間是招募資訊的一部分）。
    if unit == '年薪' and lo and hi and int(lo) != int(hi):
        a, b = _money(lo), _money(hi)
        for sfx in (' 萬', ' 元'):
            if a.endswith(sfx) and b.endswith(sfx):
                a = a[: -len(sfx)]
                break
        out.append(f'{unit} {a}–{b}')
    elif lo or hi:
        base = lo or hi
        if unit == '月薪':
            out.append(f'{unit} {_money(base)}起，依經歷面議')
        else:
            out.append(f'{unit} {_money(base)}以上')
    emp = j.get('employment') or []
    if isinstance(emp, str):
        emp = [emp]
    labels = [EMPLOYMENT_LABEL[e] for e in emp if e in EMPLOYMENT_LABEL]
    if labels:
        out.append('／'.join(dict.fromkeys(labels)))
    loc = j.get('locations') or ''
    if isinstance(loc, list):
        loc = '、'.join(str(x) for x in loc if x)
    loc = loc.strip()
    if loc:
        out.append(loc.split('、')[0].split(',')[0].strip())
    return out


def update_list(j):
    """職缺列表卡。已存在就取代，不存在就插在第一張卡之前（新的排前面）。"""
    p = os.path.join(SITE, 'jobs', 'index.html'); s = open(p, encoding='utf-8').read()
    desc = j.get('card_desc') or j.get('intro') or j.get('description', '')
    # ⚠️ track／cat 沒給就留空，不要套預設值。
    # 舊版預設是 track="dispatch" cat="service"，結果 2026-09-03 後台上架的
    # 六筆正職工程職缺全部被歸到「派遣・客服行政」的篩選條件底下——
    # 留空只是不出現在產業篩選裡（「全部」還是看得到），套錯值是把人導到錯的分類。
    card = (f'<a class="job" href="/jobs/{j["slug"]}/" data-track="{j.get("track","")}" '
            f'data-cat="{j.get("cat","")}"> '
            f'<div class="job-industry">{j.get("industry","")}</div> '
            f'<h2 class="job-title">{j["title"]}</h2> '
            f'<div class="job-meta">{"".join(f"<span>{t}</span>" for t in card_meta(j))}</div> '
            f'<p class="job-desc">{desc}</p> '
            f'<span class="job-more">查看職缺詳情 →</span> </a>')
    i = s.find(f'href="/jobs/{j["slug"]}/"')
    if i > 0:
        st = s.rfind('<a class="job"', 0, i); en = s.find('</a>', i) + 4
        s = s[:st] + card + s[en:]
    else:
        st = s.find('<a class="job"')
        s = s[:st] + card + s[st:]
    open(p, 'w', encoding='utf-8').write(s)


def backfill_card(slug):
    """規格補不齊的欄位，改從剛寫出去的內頁撈（JobPosting schema ＋ meta description）。

    為什麼要多這一步：規格是上游給的，上游會漏；內頁是這支自己剛產出來的，
    一定有資料。scripts/gen_job_cards.py 就是做這件事的，直接呼叫它，
    不要在這裡再抄一份會慢慢長歪的解析邏輯。
    """
    gen = os.path.join(SITE, 'scripts', 'gen_job_cards.py')
    if not os.path.exists(gen):
        return
    r = subprocess.run([sys.executable, gen, '--slug', slug],
                       cwd=SITE, capture_output=True, text=True)
    for line in (r.stdout or '').splitlines():
        if line.strip().startswith(('補', '⚠️', '❌')):
            print('  ' + line.strip())


def update_sitemap(j):
    p = os.path.join(SITE, 'sitemap.xml'); s = open(p, encoding='utf-8').read()
    url = f'https://step1ne.com/jobs/{j["slug"]}/'
    if url in s:
        return
    blk = f'  <url>\n    <loc>{url}</loc>\n    <changefreq>weekly</changefreq>\n    <priority>0.8</priority>\n  </url>\n'
    s = s.replace('</urlset>', blk + '</urlset>')
    open(p, 'w', encoding='utf-8').write(s)


def update_applyjson(j):
    p = os.path.join(SITE, 'apply', 'jobs.json'); d = json.load(open(p, encoding='utf-8'))
    lst = d if isinstance(d, list) else d['jobs']
    row = {'slug': j['slug'], 'title': j['title'], 'loc': j.get('locations', '')}
    # 應徵表單那邊還會用到 pay／emp／exp／req。這支只認得三個欄位，
    # 原本是整列覆蓋——更新一個既有職缺，那四欄就被無聲刪掉了（2026-08-12 發現）。
    # 改成合併：這支管得到的欄位更新，管不到的保留原值，不要動到別人寫的東西。
    for i, x in enumerate(lst):
        if x['slug'] == j['slug']:
            lst[i] = {**x, **row}; break
    else:
        lst.insert(0, row)
    json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def check_duplicate(j):
    """上架前比對既有職缺標題，撞名就擋下來。

    2026-09-07 Jacky 明確確認：**Step1ne 目前不存在「同一職缺不同客戶」的
    情況，所以出現重複職稱就是錯的。** 原本這裡只比對「同一個 client_name」，
    所以 2026-09-03 後台上架的「資深職業安全衛生工程師」跟 08-26 既有的
    /jobs/ehs-engineer-yunlin/ 撞名沒被擋住，兩筆同時開著。

    風險不只 SEO 互搶：同名職缺會讓阿財可能拿錯 JD 去面談候選人
    （step1ne-job-posting 技能包早就寫過這條）。

    ⚠️ 改成硬擋（sys.exit），不是印出來讓人自己判斷——上一版是「印出來
    讓顧問決定」，實際結果是自動化流程沒有人在看那行字，照樣上架。
    真的要重複就加 --force。
    """
    title = (j.get('title') or '').strip()
    if not title:
        return None
    row = D.d1(
        f"SELECT slug, status, client_name FROM jobs WHERE trim(title)={D.q(title)} "
        f"AND status != 'closed' AND slug != {D.q(j['slug'])} LIMIT 1"
    )
    return row[0] if row else None


PENDING_SLUG = re.compile(r'^pending-')


def check_slug_promoted(j):
    """暫存網址不得對外公開。

    客戶在 portal 自建職缺時，Worker 給的是 `pending-<company_id>-<hash>`
    這種暫時代號（step1ne-backoffice-worker/src/index.js 的 POST /portal/:token/jobs）。
    2026-09-07 查到有 6 筆就這樣公開在架上，網址長成
    /jobs/pending-co_802bdd6b-3b807a75/——對候選人不可讀，對 Google 也沒有語意。

    轉正的地方在 jobintake/jd_regen_tick.py（發布前），這裡是最後一道門：
    沒轉正就不准產頁面。要處理既有那 6 筆（需要 301，不能直接改網址）
    才用 --allow-pending-slug。
    """
    return bool(PENDING_SLUG.match(j.get('slug') or ''))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('spec_json')
    ap.add_argument('--dry', action='store_true', help='只產頁面到 /tmp，不動網站也不動 D1')
    ap.add_argument('--deploy', action='store_true', help='產完直接 git push 部署')
    ap.add_argument('--force', action='store_true', help='忽略撞名警告，強制繼續')
    ap.add_argument('--allow-pending-slug', action='store_true',
                    help='允許用還沒轉正的 pending- 暫存網址產頁面（只有處理既有那幾筆時才用）')
    a = ap.parse_args()

    j = json.load(open(a.spec_json, encoding='utf-8'))
    for k in ('slug', 'title', 'page_title', 'description'):
        if not j.get(k):
            sys.exit(f'缺少必要欄位：{k}')

    # --dry 只寫到 /tmp、不碰網站也不碰 D1，那不算「公開」，不擋。
    if not a.dry:
        if check_slug_promoted(j) and not a.allow_pending_slug:
            sys.exit(f"⚠️ 這個職缺的網址還是暫存代號（{j['slug']}），未轉正不得公開。\n"
                     f"   正常流程會在 jd_regen_tick.py 發布前轉成語意 slug；"
                     f"真的要用暫存網址產頁面請加 --allow-pending-slug。")
        dup = check_duplicate(j)
        if dup and not a.force:
            sys.exit(f"⚠️ 撞名：已經有一筆「{j['title']}」了（slug={dup['slug']}"
                     f"／狀態 {dup.get('status')}／客戶 {dup.get('client_name') or '未填'}）。\n"
                     f"   Step1ne 不存在同一職缺不同客戶的情況，重複職稱通常代表建重複了——"
                     f"請顧問先確認要留哪一筆（另一筆關掉），或加 --force 強制繼續。")

    html = render_page(j)
    if a.dry:
        out = f"/tmp/job_{j['slug']}.html"
        open(out, 'w', encoding='utf-8').write(html)
        print(f'✅ 只產檔（--dry）：{out}　{len(html)} 字')
        return

    d = os.path.join(SITE, 'jobs', j['slug']); os.makedirs(d, exist_ok=True)
    open(os.path.join(d, 'index.html'), 'w', encoding='utf-8').write(html)
    print(f"✅ 職缺頁　jobs/{j['slug']}/index.html　{len(html)} 字")
    update_list(j);      print('✅ 職缺列表卡')
    backfill_card(j['slug'])
    update_sitemap(j);   print('✅ sitemap')
    update_applyjson(j); print('✅ apply/jobs.json')
    upsert_d1(j);        print('✅ D1 jobs')

    # 2026-09-07 加：部署設定介面第一版——這個帳號名稱原本寫死是 Jacky 本人，
    # 換一台機器/換一個人跑這支腳本，GitHub帳號不會自動跟著換。改成先查
    # site_settings 有沒有設定值，沒有就照舊用 'jacky6658' 當預設，行為不變。
    gh_user = 'jacky6658'
    try:
        rows = D.d1("SELECT value FROM site_settings WHERE key='github_publish_user'")
        if rows and rows[0].get('value'):
            gh_user = rows[0]['value']
    except Exception:
        pass  # 查不到就用預設值，不擋主流程

    if a.deploy:
        subprocess.run(['gh', 'auth', 'switch', '-u', gh_user], capture_output=True)
        subprocess.run(['git', 'add', '-A'], cwd=SITE, check=True)
        subprocess.run(['git', 'commit', '-q', '-m',
                        f"上架職缺：{j['title']}（{j['slug']}）\n\n由 publish_job.py 產出，"
                        f"頁面／列表卡／sitemap／apply/jobs.json／D1 一次同步。"], cwd=SITE)
        r = subprocess.run(['git', 'push', 'deploy', 'HEAD:main'], cwd=SITE,
                           capture_output=True, text=True)
        print('✅ 已部署' if r.returncode == 0 else f'❌ 部署失敗：{r.stderr[-300:]}')
    else:
        print(f'\n（尚未部署。確認沒問題後：cd ~/claude-projects/step1ne-stopgap-site && '
              f'gh auth switch -u {gh_user} && git add -A && git commit && git push deploy HEAD:main）')


if __name__ == '__main__':
    main()
