#!/usr/bin/env python3
"""company_research.py — 認識公司「請 AI 研究這家公司」（2026-10-14 Jacky）

顧問在客戶卡片「認識公司」按按鈕 → 後台排 ai_jobs(kind='company_research') → ai_worker.py 呼叫這支。
流程：
  ① 104 搜這家公司目前所有職缺（公司名＋職類關鍵字，104 公司頁 API 會被擋）
  ② 挑最像「值得看」的前 3 個，讀 JD 全文（後台 /admin/bd/read-104）
  ③ 背景 claude（只開 WebSearch／WebFetch）照 prompts/企業開發前深度研究指令_V1.0.md 查公開資料，
     輸出固定格式 JSON（認識公司研究卡用）
  ④ 驗證後寫回 bd_company_profiles.research_json；失敗寫 research_error，畫面會顯示

規則（寫死在程式，不靠提示詞自律）：
- 查不到就標「未查證」，不補猜；平台被擋（403／402／登入牆）就照實寫「被擋」，不繞過驗證
- 不登入任何平台、不使用非公開個資；員工評論匿名單一則不當整家事實
- 背景 claude 只放行 WebSearch／WebFetch，其他工具全部禁用
- 同一家公司同時只跑一個（後台按鈕端擋）
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
import d1_http as D  # noqa: E402

MODEL = 'claude-sonnet-5'
TIMEOUT = 1500
BASE = 'https://step1ne-backoffice-worker.aiagentg888.workers.dev'
PROMPT_MD = os.path.join(HERE, 'prompts', '企業開發前深度研究指令_V1.0.md')
_BAN = ('Task,Bash,Glob,Grep,Edit,Write,NotebookEdit,Read,AskUserQuestion,TodoWrite,BashOutput,KillShell,Skill,'
        'Agent,Artifact,Monitor,CronCreate,CronDelete,CronList')
VOLUME = re.compile(r'計時|大夜|中班|早班|晚班|工讀|兼職|店員|門市人員|儲備|司機|裝卸|理貨|收銀|服務生|外送')
SENIOR = re.compile(r'主管|經理|協理|副理|副總|總監|處長|長$|財務長|資訊長|營運長|Manager|Director|Head|VP|工程師|企劃|採購|法務|稽核|會計|人資|設計|分析')
SHORT_ROLES = ['財務', '會計', '採購', '商品', '營運', '資訊', '工程師', '人資', '企劃', '行銷', '物流', '主管', '經理', '總', '法務', '業務']

SCHEMA = """{
 "version":1,"generated_at":"YYYY-MM-DD","by":"AI 研究（未經顧問核對）","target_job":"主要研究的職缺（沒有就寫『不限特定職缺』）",
 "verdict":{"level":"go 或 wait","label":"一句話：值不值得今天打、定位是什麼","text":"兩句內講理由"},
 "key_question":"這通電話最重要的一個問題（一句）",
 "kpis":[{"label":"","value":"大字（可為『沒公布』）","unit":"","sub":"一句來源或說明","state":"ok 或 unknown 或 warn"}],   // 最多 4 個
 "findings":[{"t":"短標題","d":"一兩句，有事實、有日期"}],                                   // 剛好 5 個，最重要的在前
 "charts":[{"id":"","title":"","sub":"","unit":"","items":[{"label":"","value":數字,"kind":"fact 或 target","note":""}],"read":"看圖重點一句"}],  // 只在有真實數字時才畫（店數、營收、員工數…），沒有就給空陣列 []
 "timeline":[{"when":"","what":"","state":"done 或 watch 或 plan"}],                     // 3～6 個這家公司正在忙的事
 "coverage":[{"label":"公司登記資料|經營團隊|規模|近期新聞|對外評價|員工評價|營收與獲利|股權與集團|職缺內容|薪資行情","state":"有 或 部分 或 沒有"}],
 "facts":[{"fact":"已確認事實","infer":"我的推論","ask":"要問人資的"}],                  // 3～5 組
 "reputation":[{"date":"","src":"","obs":"","limit":"證據限制"}],                       // 對外評價與爭議（有來源才列）
 "platforms":[{"name":"GoodJob|面試趣|PTT|Dcard|104|1111|Google","status":"有資料 或 部分 或 被擋 或 查不到","note":"一句話"}],   // 員工評價平台逐一回報，不可默默跳過
 "employee":[{"date":"","role":"職務","place":"地點","tone":"正面|負面|中性","obs":"","limit":"","src":"平台"}],  // 員工／面試心得；區分門市現場與總部，匿名單一則不能代表全公司
 "employee_note":"總部／高階職有沒有直接評價，以及這些資料能怎麼用、不能怎麼用（一兩句）",
 "jobs":[{"title":"","date":"YYYYMMDD","area":"","salary":"","url":"","why":"為什麼這個職缺值得顧問看（一句）","level":"high 或 mid"}],   // 從 104 職缺清單挑 3～8 個「有價值」的：主管級、總部職、難補、稀缺、可能反覆刊登；門市基層大量缺不要列
 "jobs_summary":"一句話：這家公司目前的招募長相（共幾個職缺、多少是門市基層、值得看的在哪）",
 "topics":[{"t":"","why":"為什麼可以聊","how":"怎麼開口（一句話）"}],                      // 4～5 個，要有日期或事實依據
 "opening":"60 秒電話開場（顧問本人使用；沿用既定話術：很多公司會同時請兩三家顧問，多一個人才池子、多看一次機會；不提資料來源）",
 "questions":[{"q":"","verify":"要驗證什麼"}],                                          // 3 個最重要的需求訪談問題
 "unknowns":["查不到、請顧問自己補的"],"risks":["對我們開發的風險（簽約主體、付費能力、自行招募傾向…）"],
 "plan":{"today":[""],"week":[""],"month":[""]},
 "selfcheck":["三個讓顧問用自己的話回答的問題，確認他真的讀懂（例：這家現在最忙的三件事？這個缺為什麼現在出現？）"],
 "sources":["來源網址，每個重要結論都要有"]
}"""


try:  # 2026-10-23 F002：具名權杖優先（STEP1NE_API_TOKEN），沒有就照舊讀 RECRUIT_ADMIN_TOKEN
    import api_token as _api_token
except Exception:
    _api_token = None


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def _token():
    for f in ('~/.config/workflow-os/tokens.env', '~/.config/workflow-os/recruit.env'):
        try:
            for line in open(os.path.expanduser(f), encoding='utf-8'):
                if line.startswith('RECRUIT_ADMIN_TOKEN='):
                    return line.strip().split('=', 1)[1].strip().strip("'\"")
        except Exception:
            continue
    return None


if _api_token:
    _token = _api_token.wrap(_token)


def _read_jd(url):
    tok = _token()
    if not tok:
        return None
    try:
        req = urllib.request.Request(BASE + '/admin/bd/read-104', data=json.dumps({'urls': [url]}).encode(),
                                     headers={'authorization': 'Bearer ' + tok, 'content-type': 'application/json', 'user-agent': 'company_research/1.0'}, method='POST')
        d = json.loads(urllib.request.urlopen(req, timeout=90).read().decode())
        it = (d.get('items') or [{}])[0]
        if it.get('ok'):
            return {'title': it.get('title'), 'locations': it.get('locations'), 'pay': it.get('pay_text'),
                    'duties': str(it.get('main_duties') or '')[:1800], 'conditions': str(it.get('required_conditions') or '')[:1500]}
    except Exception:
        return None
    return None


def _core(name):
    n = re.sub(r'(股份)?有限公司|企業社|公司', '', str(name or '')).strip()
    n = n.split('_')[-1] if '_' in n else n
    return n or str(name)


def _claude(prompt):
    env = dict(os.environ)
    env.pop('CLAUDECODE', None)
    r = subprocess.run(
        [os.environ.get('CLAUDE_BIN', 'claude'), '-p', '--model', MODEL, '--allowedTools', 'WebSearch,WebFetch', '--disallowed-tools', _BAN,
         '--permission-mode', 'bypassPermissions', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '', '--output-format', 'text'],
        input=prompt, capture_output=True, text=True, env=env, timeout=TIMEOUT, cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError(f'claude exit={r.returncode}：{(r.stderr or r.stdout)[-200:]}')
    out = r.stdout.strip()
    i, j = out.find('{'), out.rfind('}')
    if i < 0 or j < 0:
        raise RuntimeError(f'回覆裡沒有 JSON：{out[:160]}')
    return json.loads(out[i:j + 1])


def _clean(d, today):
    need = ('verdict', 'findings', 'topics', 'opening', 'questions')
    miss = [k for k in need if not d.get(k)]
    if miss:
        raise RuntimeError('AI 回的資料缺：' + '、'.join(miss))
    d['version'] = 1
    d.setdefault('generated_at', today)
    d['by'] = 'AI 研究（未經顧問核對）'
    for k in ('charts', 'timeline', 'coverage', 'facts', 'reputation', 'platforms', 'employee', 'jobs', 'unknowns', 'risks', 'selfcheck', 'sources', 'kpis'):
        if not isinstance(d.get(k), list):
            d[k] = []
    d['kpis'] = d['kpis'][:4]
    d['findings'] = d['findings'][:5]
    d['jobs'] = [j for j in d['jobs'] if isinstance(j, dict) and j.get('title')][:8]
    for j in d['jobs']:
        if not str(j.get('url') or '').startswith('https://www.104.com.tw/'):
            j['url'] = ''
    d['charts'] = [c for c in d['charts'] if isinstance(c, dict) and len(c.get('items') or []) >= 2]
    return d


def run(payload):
    company = payload['company']
    D.query(f"UPDATE bd_company_profiles SET research_status='working', research_error=NULL WHERE company={q(company)}")
    try:
        import company_jobs_104 as CJ
        core = _core(company)
        match = core[:4] if len(core) > 4 else core
        jobs = CJ.jobs(core, match, roles=SHORT_ROLES)
        vol = [j for j in jobs if VOLUME.search(j['job'])]
        cand = [j for j in jobs if not VOLUME.search(j['job'])]
        cand.sort(key=lambda j: (0 if SENIOR.search(j['job']) else 1, -int(j['date'] or 0)))
        jds = []
        for j in cand[:3]:
            jd = _read_jd(j['url']) if j.get('url') else None
            if jd:
                jds.append({'url': j['url'], **jd})
        md = open(PROMPT_MD, encoding='utf-8').read() if os.path.exists(PROMPT_MD) else ''
        today = __import__('datetime').date.today().isoformat()
        joblines = '\n'.join(f"- {j['date']}｜{j['job']}｜{j['area']}｜{j['salary'] or '面議'}｜{j['url']}" for j in cand[:60]) or '（104 搜不到這家公司的職缺）'
        prompt = f"""{md}

====================
【這次任務：做成「認識公司」研究卡】今天是 {today}。
研究對象：{company}　官網：{payload.get('website') or '自己查，查不到寫未查證'}　產業：{payload.get('industry') or '自己查'}
顧問想開發的職缺：{payload.get('job_hint') or '不限特定職缺——請你從這家公司在 104 的職缺清單裡，挑出「有價值、值得顧問跟人資聊」的職缺'}
研究目的：企業開發 BD。我們是台灣獵頭公司 Step1ne（德仁管理顧問）。

【104 目前職缺（已用公司名搜出，共 {len(jobs)} 個；其中門市／計時等大量基層缺 {len(vol)} 個已先排除，下面是其餘 {len(cand)} 個）】
{joblines}

【最值得看的前幾個職缺的 JD 全文（104 讀取）】
{json.dumps(jds, ensure_ascii=False)[:9000] if jds else '（沒讀到 JD）'}

做法：
1. 用 WebSearch／WebFetch 查最新公開資料；每個重要結論附來源網址與日期；查不到寫「未查證」，絕不補猜。區分事實／合理推論／待訪談驗證。
2. 員工評價：GoodJob、面試趣（interview.tw）、PTT、Dcard、104／1111 公司頁、Google 地圖，每一個都要在 platforms 逐一回報結果（被擋就寫被擋，不繞過、不登入）。employee 要區分門市現場與總部／高階，匿名單一評論不代表整家公司。
3. 若是上市櫃公司，查近三年營收、營業利益、淨利、現金流並放進 charts／kpis；有限公司沒有財報就明講沒公布，不要畫沒有資料的圖。
4. 職缺（jobs）要從上面的 104 清單挑，url 必須原樣取自清單，不可自己編；why 要講出為什麼值得看。
5. 不得使用非公開個資；年齡、性別等不得寫進任何字句。
6. 全文繁體中文（台灣用語）、短句、給顧問一眼看懂，不要論文。

最後只輸出一個 JSON 物件（不要 Markdown、不要說明），欄位格式如下（// 後面是說明，輸出時不要帶）：
{SCHEMA}
"""
        d = _clean(_claude(prompt), today)
        D.query(f"UPDATE bd_company_profiles SET research_json={q(json.dumps(d, ensure_ascii=False))}, research_status='done', research_error=NULL, "
                f"research_at=datetime('now','+8 hours') WHERE company={q(company)}")
        try:
            import tg_route
            import recommendation_service as rs
            c, t = tg_route.route('client_candbd')
            rs._tg(f"🔍 認識公司研究好了｜{company}\n到後台「客戶 → 開發進度」打開這家的卡片 →「認識公司」看。\n（{payload.get('by') or '顧問'} 按的；AI 查的，打電話前請核對）", thread=t, chat=c)
        except Exception:
            pass
        return f'company_research {company}: ok（104 職缺 {len(jobs)}、研究卡完成）'
    except Exception as e:
        D.query(f"UPDATE bd_company_profiles SET research_status='error', research_error={q(str(e)[:300])} WHERE company={q(company)}")
        raise


if __name__ == '__main__':
    print(run({'company': sys.argv[1], 'website': sys.argv[2] if len(sys.argv) > 2 else ''}))
