#!/usr/bin/env python3
"""職缺專業題庫產生器 —— 讓阿財變成「這個職缺該領域的專家」。

為什麼要有這支：
    阿財原本問的是通用面談題（動機、經歷、穩定度），該領域的專業問題完全沒問。
    結果就是顧問拿到報告後還要自己補一輪——孙悦那場的「需要你協助的事」裡，
    三件有兩件是「阿財應該當場問完的專業問題」（英文情境測試、日文口說實測）。
    用人單位主管想知道的是「他到底會不會做這份工作」，那必須在面談當下問。

作法：
    每個職缺產生一次專業題庫，存進 D1，之後每一場該職缺的面談都直接載入。
    不在面談當下即時查——候選人在等，查資料要幾十秒，那個延遲會毀掉對話。

資料來源三路（缺一不可）：
    ① JD 本文：這家公司這個職位實際要做什麼
    ② 網路查該職務的專業知識：這個領域真正的技術重點、常見坑、實務判準
    ③ 網路查該職務的常見面試題：面試趣、Glassdoor、產業論壇等別人分享的真實考題

⚠️ 這支跟面談／報告那兩支不同，**刻意開放 WebSearch 與 WebFetch**——
   不查資料就只能產出泛泛而談的題目，那等於沒做。
   但一樣禁掉 Edit／Write／Bash：查資料不需要動這台電腦上的任何檔案。

用法：
    python3 build_expertise.py <job_slug>       # 產一個職缺
    python3 build_expertise.py --all            # 補齊所有還沒有題庫的職缺
    python3 build_expertise.py <job_slug> --force   # 已經有了也重產
"""
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = 'step1ne-recruit'
MODEL = 'claude-sonnet-5'
TIMEOUT = 900   # 要上網查資料，比產報告久得多

# 可以查資料，但不准碰這台電腦。跟 interview_daemon 的 NO_TOOLS 是同一個道理，
# 只是這裡刻意把 WebSearch／WebFetch 留下來。
_BAN = ('Task,Bash,Glob,Grep,Read,Edit,Write,NotebookEdit,AskUserQuestion,TodoWrite,'
        'BashOutput,KillShell,Skill,Agent,Artifact,Monitor,'
        'CronCreate,CronDelete,CronList')
# ⚠️ headless 模式下 WebSearch 會卡在權限提示（實測：模型回一句「需要您授權」
# 就結束了）。要配 bypassPermissions 才跑得動——危險的工具已經在上面那份
# 黑名單裡擋掉了，所以這個組合是「可以查資料，但動不了這台電腦」。
RESEARCH_TOOLS = ['--disallowed-tools', _BAN, '--setting-sources', '',
                  '--permission-mode', 'bypassPermissions']


def log(m):
    print(f'[{datetime.datetime.now():%H:%M:%S}] {m}', flush=True)


def env_with_cf():
    env = dict(os.environ)
    p = os.path.expanduser('~/.config/workflow-os/cf.env')
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            if '=' in line and not line.startswith('#'):
                k, v = line.strip().split('=', 1)
                env[k] = v.strip().strip("'\"")
    return env


def d1(sql):
    r = subprocess.run(['npx', 'wrangler', 'd1', 'execute', DB, '--remote', '--json',
                        f'--command={sql}'],
                       cwd=HERE, capture_output=True, text=True, env=env_with_cf(), timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f'd1 失敗：{(r.stderr or r.stdout)[-400:]}')
    out = r.stdout[r.stdout.index('['):]
    data = json.loads(out)
    return (data[0] if data else {}).get('results', [])


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"



# ── 花費記錄 ──
# 直接借用 interview_daemon 那一套（同一個 claude session 檔案格式、同一張表），
# 不重寫一份——兩邊的算法不一致的話，花費頁上的數字就沒辦法互相比較。
def _daemon():
    import importlib.util
    spec = importlib.util.spec_from_file_location('_idm', os.path.join(HERE, 'interview_daemon.py'))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


def _snapshot():
    try:
        return _daemon()._snapshot_session_files()
    except Exception:
        return set()


def _log_tokens(key, call_type, prompt, before):
    try:
        _daemon().log_token_usage(key, call_type, prompt, before)
    except Exception as e:
        log(f'（花費沒記到，不影響題庫：{e}）')


def _extract_json(text):
    """從模型回覆裡挖出最外層 JSON。跟 interview_daemon 同一套邏輯。"""
    if not text:
        return None
    s = text.strip()
    if s.startswith('```'):
        s = s.split('\n', 1)[-1]
        if s.rstrip().endswith('```'):
            s = s.rstrip()[:-3]
    i, j = s.find('{'), s.rfind('}')
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


PROMPT = '''你要為一個職缺建立「專業面談題庫」，讓 AI 面談助理能像這個領域的行家一樣提問。

【這個職缺】
職稱：{title}
公司／產業背景：{client_hint}
工作地點：{locations}
工作內容與條件（用人單位原文）：
{jd}

【你要做的事，三步都要做，不可以跳過】

第一步 — 查這個職務的專業內涵
用 WebSearch 查這個職務在台灣（或本案工作地）實際的工作內容、常用工具與方法、
這一行的人怎麼判斷一個人是不是真的做過。不要只查職務名稱的字面定義。

第二步 — 查別人怎麼面試這個職務
用 WebSearch 查這個職務的實際面試考題與面試心得，來源例如「面試趣」「104 面試心得」
「Glassdoor」「PTT／Dcard 的面試分享」或該產業的專業社群。
把「這一行的人真的會被問什麼」找出來，不要自己想像。

第三步 — 產出題庫

【硬性要求】
1. 題目要**能分辨真做過與只是聽過**。不要問「你熟不熟悉 X」——那種問題誰都會說熟。
   要問「你上一次處理 X 的時候，做法是什麼、遇到什麼狀況」。
2. 每一題都要附 `good_signs` 與 `red_flags`：真的做過的人會提到什麼、
   沒做過的人會用什麼方式含糊帶過。這是給 AI 判斷「要不要追問」用的，
   **不是用來判定對錯**——AI 不是這個領域的專家，它的工作是問對問題、
   記錄原話、標出可疑處，讓顧問與用人主管自己判斷。
3. 如果這個職缺需要外語，要出**情境題**（例如「請用英文說一段：向業主報告本月出租率下滑的原因與對策」），
   不是問「你英文如何」。
4. 題目數量 8–14 題，依這個職務的複雜度決定。寧可少而準。
5. 不要出違反就業服務法第 5 條的題目（年齡、性別、婚育、國籍、宗教、政黨…）。
6. 全部用繁體中文寫（情境題裡要候選人講外語的部分除外）。

【只輸出這個 JSON，不要有其他文字】
{{
  "domain": "這個職務所屬的專業領域，10 字內",
  "topics": [
    {{"name": "考點名稱", "why": "用人主管為什麼在意這件事，一句話"}}
  ],
  "questions": [
    {{
      "q": "要問候選人的問題（口語，像人在講話）",
      "topic": "對應上面哪個考點",
      "why": "這題想確認什麼",
      "good_signs": ["真的做過的人會提到…"],
      "red_flags": ["沒做過的人會…"],
      "followup": "如果他答得含糊，追問什麼",
      "kind": "經驗|情境|技術|外語"
    }}
  ],
  "sources": ["你實際查過並採用的網址"]
}}'''


def build(job, force=False):
    slug = job['slug']
    if not force:
        # ⚠️ 2026-09-20 修：原本只看「有沒有這一列」，不看列裡面有沒有題目。
        # 產題失敗時列還是會寫進去（questions_json 是空陣列），這個守門員就
        # 永遠判定「已經有了」，那個職缺再也不會重產——實測 28 個在辦職缺裡
        # 有 8 個是這種空殼，阿財面談時拿到 0 題專業題。
        exist = d1(f"SELECT questions_json FROM job_expertise WHERE job_slug = {q(slug)}")
        if exist:
            try:
                n = len(json.loads(exist[0].get('questions_json') or '[]'))
            except Exception:
                n = 0
            if n:
                log(f'{slug}：已經有 {n} 題了，跳過（要重產加 --force）')
                return None
            log(f'{slug}：有紀錄但題庫是空的（前一次產題失敗），重產')

    jd_parts = []
    for k, label in (('description', '工作內容'), ('requirements', '資格條件'),
                     ('must_skills', '必備技能'), ('nice_skills', '加分技能'),
                     ('salary_note', '待遇說明')):
        v = job.get(k)
        if v:
            jd_parts.append(f'■ {label}\n{v}')
    prompt = PROMPT.format(
        title=job.get('title') or slug,
        client_hint=job.get('industry') or job.get('client_name') or '（未提供，請依職務內容判斷）',
        locations=job.get('locations') or '未提供',
        jd='\n\n'.join(jd_parts) or '（JD 內容不足，請依職稱與產業推導）')

    log(f'{slug}：開始查資料並產題庫（會上網，需要幾分鐘）…')
    # 花費照樣要記（Jacky 指定）。這支沒有 application_id，所以用 job:<slug> 當 key，
    # 花費頁上就看得出「這筆錢是花在替哪個職缺建題庫」，不會跟面談的花費混在一起。
    before = _snapshot()
    r = subprocess.run(['claude', '-p', prompt, '--model', MODEL,
                        *RESEARCH_TOOLS, '--output-format', 'text'],
                       cwd=HERE, capture_output=True, text=True,
                       env=env_with_cf(), timeout=TIMEOUT)
    _log_tokens(f'job:{slug}', 'expertise', prompt, before)
    obj = _extract_json(r.stdout)
    if not obj or not obj.get('questions'):
        log(f'❌ {slug}：沒有產出可用題庫。模型回覆開頭：{(r.stdout or r.stderr or "")[:200]}')
        return None

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    d1(f"INSERT INTO job_expertise (job_slug, domain, topics_json, questions_json, "
       f"sources_json, built_at) VALUES ({q(slug)}, {q(obj.get('domain'))}, "
       f"{q(json.dumps(obj.get('topics') or [], ensure_ascii=False))}, "
       f"{q(json.dumps(obj['questions'], ensure_ascii=False))}, "
       f"{q(json.dumps(obj.get('sources') or [], ensure_ascii=False))}, {q(now)}) "
       f"ON CONFLICT(job_slug) DO UPDATE SET domain=excluded.domain, "
       f"topics_json=excluded.topics_json, questions_json=excluded.questions_json, "
       f"sources_json=excluded.sources_json, built_at=excluded.built_at")
    log(f'✅ {slug}：{obj.get("domain")} — {len(obj["questions"])} 題，'
        f'查了 {len(obj.get("sources") or [])} 個來源')
    return obj


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    force = '--force' in sys.argv
    if '--all' in sys.argv:
        # 空殼題庫（questions_json 是空陣列）也要撈出來重產，理由同 build() 裡
        # 那段註解——只看「有沒有這一列」會讓產題失敗的職缺永遠卡住。
        jobs = d1("SELECT j.* FROM jobs j LEFT JOIN job_expertise e ON e.job_slug = j.slug "
                  "WHERE COALESCE(j.status,'open') != 'closed' "
                  "  AND (e.job_slug IS NULL "
                  "       OR COALESCE(e.questions_json,'[]') IN ('[]','','null'))")
        log(f'還沒有題庫（或題庫是空的）的職缺：{len(jobs)} 個')
        for job in jobs:
            try:
                build(job, force)
            except Exception as e:
                log(f'❌ {job["slug"]}：{e}')
        return
    if not args:
        print(__doc__)
        return
    rows = d1(f"SELECT * FROM jobs WHERE slug = {q(args[0])}")
    if not rows:
        log(f'找不到職缺 {args[0]}')
        return
    build(rows[0], force)


if __name__ == '__main__':
    main()
