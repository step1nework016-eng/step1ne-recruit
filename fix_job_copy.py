#!/usr/bin/env python3
"""職缺文案修正 —— 稽核抓到問題之後，真的把它改掉。

為什麼要有這支（2026-08-21 Jacky 交辦）：
    audit_job_copy.py 會抓出問題，但抓完就停在那裡。8/19 跑完有六個職缺
    被判「不可上架」，兩天過去**一個字都沒改、六個還全部開著**——
    因為「標記問題」跟「解決問題」是兩件事，中間沒有人接。
    Jacky 說得直接：不要判不可上架，要判「要修文案」然後叫 agent 去修。

⚠️ 這支最重要的設計：**分清楚哪些能自己改、哪些不能。**

    能自己改（agent 直接產出修正版）：
      · 薪資揭露不足 —— 資料庫有數字，補上去就好
      · 內部備註漏進對外文案 —— 刪掉
      · 就服法字眼 —— 改寫成合法說法
      · 敏感條件寫得太直白 —— 換順序、把配套跟限制寫在一起
      · 吸引力問題 —— 重寫那幾句

    不能自己改（一定要問人）：
      · **兩個地方講的事實互相矛盾**（例如轉正時間「3 個月至 1 年」vs「做滿 1–2 年」）
        —— agent 不知道哪一個才是真的。猜一個改下去，就是把錯的那個變成唯一版本，
        而且之後沒有人看得出來曾經有矛盾。這種只能列出來問業主或顧問。
      · **客戶要不要具名** —— 那是商業判斷，資料庫設定跟頁面不一致可能是後來改了口徑。

    這條界線是這支能不能被信任的關鍵：一個會自己猜事實的修文案 agent，
    比不修還危險。

輸出：修正後的文案（DB 欄位 + 頁面段落），推到 Telegram 等人看過再套用。
    ⚠️ 不自動改上線的頁面——公開文案改錯的代價由候選人承擔。

用法：
    python3 fix_job_copy.py <job_slug>
    python3 fix_job_copy.py --all        # 所有判定「要修文案」的
"""
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = 'claude-sonnet-5'
TIMEOUT = 420
SITE = 'https://step1ne.com'

_BAN = ('Task,Bash,Glob,Grep,Read,Edit,Write,NotebookEdit,WebFetch,WebSearch,'
        'AskUserQuestion,TodoWrite,BashOutput,KillShell,SlashCommand,Skill,'
        'Agent,Artifact,Monitor,CronCreate,CronDelete,CronList')
NO_TOOLS = ['--disallowed-tools', _BAN, '--strict-mcp-config',
            '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '']


def log(m):
    print(f'[{datetime.datetime.now():%H:%M:%S}] {m}', flush=True)


def _daemon():
    import importlib.util
    spec = importlib.util.spec_from_file_location('_idm', os.path.join(HERE, 'interview_daemon.py'))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


D = _daemon()
d1, q = D.d1, D.q


SITE_REPO = os.path.expanduser('~/下載項目/step1ne-stopgap-site')


def page_source(slug):
    """讀**本機原始碼**，不是抓線上的純文字。

    ⚠️ 2026-08-21 第一版踩到的坑：原本餵給模型的是「去掉 HTML 標籤的純文字」，
       但又要求它產出「跟原始碼一字不差」的比對字串——那永遠對不上，
       因為原文中間夾著 <span style="...">。實測第一次就全部套用失敗。
       改成直接給原始碼（去掉 style/script 區塊，那些跟文案無關又佔掉一半篇幅）。
    """
    path = os.path.join(SITE_REPO, 'jobs', slug, 'index.html')
    if not os.path.exists(path):
        return '', f'（本機找不到 {path}）'
    src = open(path, encoding='utf-8').read()
    src = re.sub(r'<style.*?</style>', '<style>（樣式已省略，不要改這裡）</style>', src, flags=re.S)
    src = re.sub(r'<script.*?</script>', '<script>（程式已省略，不要改這裡）</script>', src, flags=re.S)
    return src[:22000], ''


def _extract_json(text):
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


PROMPT = '''稽核抓出這個職缺的文案有問題。你要**把它改掉**，不是再講一次哪裡有問題。

【職缺資料庫欄位】
{db}

【職缺頁原始碼（去掉樣式與程式）】
{page}

【稽核抓到的問題】
{blockers}

【稽核給的吸引力與寫法建議】
{appeal}

━━━━━━━━━━━━━━━━━━━━

⚠️ **最重要的一條：分清楚哪些你能改、哪些你不能。**

■ 你**可以**直接改的：
  · 薪資揭露不足 → 資料庫有數字就補上去（就服法：未達 4 萬必須揭示範圍）
  · 內部備註漏進對外文案 → 刪掉
  · 就服法第 5 條字眼（性別、年齡、婚育、國籍…）→ 改寫成合法說法
  · 敏感條件寫得太直白 → 換順序（好處先講）、把配套跟限制寫在一起。
    **委婉不是隱瞞**：不可以刪掉不利條件、不可以講得讓人誤會
  · 吸引力問題 → 重寫那幾句

■ 你**不可以**改的，一律列進 `need_human`：
  · **兩個地方講的事實互相矛盾**——例如轉正時間一處寫「約 3 個月至 1 年」、
    另一處寫「做滿 1–2 年」。你**不知道哪一個才是真的**。
    猜一個改下去，就是把錯的那個變成唯一版本，而且之後沒人看得出來曾經有矛盾。
  · **客戶要不要具名**——那是商業判斷，資料庫設定跟頁面不一致可能是後來改了口徑。
  · 任何需要跟業主確認才知道答案的事實。

  ⚠️ 一個會自己猜事實的修文案 agent，比不修還危險。不確定就放進 need_human。

■ 改寫的硬規則
  0. `before` 一定要**一字不差照抄原始碼（含 HTML 標籤）**——這段會被程式拿去
     做完全比對後直接取代，憑印象重打或只抄看得見的文字都會套用失敗。
     ⚠️ 不要動 <style> 與 <script> 區塊，那些跟文案無關。
     ⚠️ 不要為了排版好看而搬動整個區塊——搬動大段 HTML 很容易把標籤配對弄壞。
     這一輪只改**文字內容**，版面順序的問題請寫進 need_human 讓人決定。
  1. **只用手上有的事實**，不准編造沒提供的資訊。
  2. 不准出現佔位符（待補、待確認、［…］、TBD）。資料不足的段落整段不要寫。
  3. 對候選人的文字不准用「客戶」，用「業主」或「用人單位」。
  4. 不要拿「顧問／我們」當句子主詞，站在候選人（您）的視角寫。
  5. 繁體中文（台灣用語）。

【只輸出這個 JSON】
{{
  "fixed": [
    {{"target": "要改的地方，只能是這兩種寫法之一：\"db:<欄位名>\"（例如 db:salary_note）或 \"page\"",
      "problem": "原本的問題是什麼",
      "before": "**原封不動照抄原始碼**。target 是 page 時，這段會被拿去跟上面那份原始碼做完全比對後直接取代——**要連 HTML 標籤一起照抄**（例如 <dd>月薪…<span style=\"…\">…</span></dd>），多一個字少一個空格都會套用失敗。抄短一點、抄準一點，好過抄一大段。",
      "after": "改好的完整文字"}}
  ],
  "need_human": [
    {{"issue": "什麼事實互相矛盾或需要確認",
      "options": "看到的兩種（或多種）說法分別是什麼",
      "ask": "要問業主或顧問的那一句話"}}
  ],
  "summary": "一句話：改了什麼、還有什麼要人決定"
}}'''


def fix(slug, do_apply=True):
    job = d1(f"SELECT * FROM jobs WHERE slug={q(slug)}")
    if not job:
        log(f'{slug}：找不到這個職缺')
        return None
    job = job[0]
    audit = d1(f"SELECT blockers_json, appeal_json, rewrite_json FROM job_audits "
               f"WHERE job_slug={q(slug)} ORDER BY audited_at DESC LIMIT 1")
    if not audit:
        log(f'{slug}：還沒稽核過，先跑 audit_job_copy.py')
        return None
    audit = audit[0]
    blockers = json.loads(audit.get('blockers_json') or '[]')
    appeal = json.loads(audit.get('appeal_json') or '[]')
    if not blockers and not appeal:
        log(f'{slug}：稽核沒抓到要改的東西')
        return None

    page, note = page_source(slug)
    fields = []
    for k in ('title', 'client_name', 'client_named', 'confidential_client', 'locations',
              'employment', 'service_line', 'seniority', 'salary_min', 'salary_max',
              'salary_note', 'must_skills', 'years_min', 'talking_points', 'faq_notes',
              'onboard_by', 'interview_language', 'status'):
        v = job.get(k)
        if v not in (None, ''):
            fields.append(f'{k}: {str(v)[:800]}')

    prompt = PROMPT.format(
        db='\n'.join(fields), page=page or '（沒有公開頁面）' + note,
        blockers='\n'.join(f"· [{b.get('type')}] {b.get('detail')}\n  依據：{b.get('evidence')}\n  稽核建議：{b.get('fix')}"
                           for b in blockers) or '（沒有）',
        appeal='\n'.join(f"· {a.get('issue')}｜{a.get('suggest')}" for a in appeal) or '（沒有）')

    log(f'{slug}：修文案中…')
    before = D._snapshot_session_files()
    r = subprocess.run(['claude', '-p', D.sanitize(prompt), '--model', MODEL,
                        *NO_TOOLS, '--output-format', 'text'],
                       cwd=HERE, capture_output=True, text=True,
                       env=D.env_with_cf(), timeout=TIMEOUT)
    D.log_token_usage(f'fix_copy:{slug}', 'fix_copy', prompt, before)
    obj = _extract_json(r.stdout)
    if not obj:
        log(f'{slug}：沒有產出。回覆開頭：{(r.stdout or r.stderr or "")[:160]}')
        return None

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    d1(f"INSERT INTO job_audits (job_slug, audited_at, verdict, blockers_json, fixes_json, "
       f"appeal_json, rewrite_json, raw_len) VALUES ("
       f"{q(slug)}, {q(now)}, '已產修正版', "
       f"{q(json.dumps(obj.get('need_human') or [], ensure_ascii=False))}, "
       f"{q(json.dumps({'summary': obj.get('summary')}, ensure_ascii=False))}, '[]', "
       f"{q(json.dumps(obj.get('fixed') or [], ensure_ascii=False))}, {len(page)})")

    n_fix, n_ask = len(obj.get('fixed') or []), len(obj.get('need_human') or [])
    log(f'✅ {slug}：產出 {n_fix} 處修正、{n_ask} 件要人決定')
    if do_apply:
        applied, failed = apply_fixes(slug, obj)
        obj['_applied'], obj['_failed'] = applied, failed
        log(f'   套用：成功 {len(applied)} 處' + (f'、失敗 {len(failed)} 處' if failed else ''))
        for t, why in failed:
            log(f'     ✗ {t}：{why}')
    notify(slug, job.get('title') or slug, obj)
    return obj



# ── 套用 ──


def apply_fixes(slug, obj, push=True):
    """把修正版真的改下去。

    ⚠️ 頁面的改法是**完全比對後取代**，不是讓模型自由編輯 HTML。
       比對不到就跳過並回報，不做模糊比對——模糊比對會改到不該改的地方，
       而公開頁面改錯的代價是候選人看到錯的資訊，那個錯誤還會被搜尋引擎收走。

    回傳 (成功幾處, 失敗清單)。
    """
    ok, failed = [], []
    page_path = os.path.join(SITE_REPO, 'jobs', slug, 'index.html')
    page_src = open(page_path, encoding='utf-8').read() if os.path.exists(page_path) else None
    page_dirty = False

    for f in obj.get('fixed') or []:
        target = (f.get('target') or '').strip()
        before, after = f.get('before') or '', f.get('after') or ''
        if not before or not after:
            failed.append((target, '沒有給原文或新文字'))
            continue

        if target.startswith('db:'):
            col = target[3:].strip()
            # 只允許改對外文案相關的欄位。不開放整張表，避免一個判斷失誤
            # 就改掉 status 或 client_name 這種會連動別處的欄位。
            ALLOW = {'salary_note', 'faq_notes', 'must_skills', 'talking_points',
                     'client_intro', 'social_angle', 'onboarding_prep_note',
                     'interview_rounds', 'locations', 'title'}
            if col not in ALLOW:
                failed.append((target, f'這個欄位不開放自動改（只開放 {len(ALLOW)} 個對外文案欄位）'))
                continue
            cur = d1(f"SELECT {col} v FROM jobs WHERE slug={q(slug)}")
            cur = (cur[0]['v'] if cur else '') or ''
            if before not in cur:
                failed.append((target, '資料庫裡找不到那段原文，可能已經被改過了'))
                continue
            d1(f"UPDATE jobs SET {col}={q(cur.replace(before, after, 1))}, "
               f"updated_at=datetime('now','+8 hours') WHERE slug={q(slug)}")
            ok.append(target)

        elif target == 'page':
            if page_src is None:
                failed.append((target, f'本機找不到頁面檔 {page_path}'))
                continue
            if before not in page_src:
                failed.append((target, '頁面原始碼裡找不到那段原文（可能中間夾著 HTML 標籤）'))
                continue
            page_src = page_src.replace(before, after, 1)
            page_dirty = True
            ok.append(target)
        else:
            failed.append((target, '認不得這個 target'))

    if page_dirty:
        open(page_path, 'w', encoding='utf-8').write(page_src)
        if push:
            msg = (f'職缺文案自動修正：{slug}\n\n'
                   + '\n'.join(f"- {f.get('problem')}" for f in (obj.get('fixed') or [])[:8])
                   + '\n\n由 fix_job_copy.py 依 audit_job_copy.py 的稽核結果產出並套用。\n'
                   + '事實互相矛盾的部分沒有自動改——那種只能問業主。\n\n'
                   + 'Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>')
            subprocess.run(['git', 'add', '-A', f'jobs/{slug}'], cwd=SITE_REPO, capture_output=True)
            subprocess.run(['git', 'commit', '-q', '-m', msg], cwd=SITE_REPO, capture_output=True)
            r = subprocess.run(['git', 'push', 'deploy', 'HEAD:main'], cwd=SITE_REPO,
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                failed.append(('page', f'推上線失敗：{(r.stderr or "")[-120:]}'))
    return ok, failed


def notify(slug, title, obj):
    """推 Telegram。**只講重點。**

    ⚠️ 2026-08-21 Jacky：「回報不要那麼多文字，我們有 ADHD 的人真的很難懂。」
       第一版把每一處修改的前後全文都貼上去，一則訊息幾十行——
       那種訊息的下場是被滑過去，等於沒回報。
       現在只留三件事：改了幾處、要你決定的是什麼、去哪裡看細節。
       前後對照留在資料庫（job_audits.rewrite_json），要看再查。
    """
    applied = obj.get('_applied')
    failed = obj.get('_failed') or []
    n_fix = len(applied) if applied is not None else len(obj.get('fixed') or [])
    asks = obj.get('need_human') or []

    head = f'✏️ {title}'
    if applied is not None:
        head += f'　已改 {n_fix} 處並上線' if n_fix else '　沒有改動'
    else:
        head += f'　產出 {n_fix} 處修正（未套用）'

    lines = [head]
    if failed:
        lines.append(f'⚠️ {len(failed)} 處沒改成功')
    if asks:
        lines.append('')
        lines.append(f'🙋 {len(asks)} 件要你決定：')
        for h in asks[:3]:
            lines.append(f"・{(h.get('ask') or h.get('issue') or '')[:70]}")
        if len(asks) > 3:
            lines.append(f'・…還有 {len(asks) - 3} 件')
    lines.append('')
    lines.append(f'{SITE}/jobs/{slug}/')

    text = '\n'.join(lines)[:1200]
    try:
        e = dict(l.strip().split('=', 1) for l in
                 open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
                 if '=' in l and not l.startswith('#'))
        import urllib.parse
        # 2026-08-21 Jacky：文案修正版回報放「系統回報」主題（1360），
        # 不要放「面試通知確認」——那個主題是給「顧問要動手的事」用的。
        data = urllib.parse.urlencode({
            'chat_id': e['TG_CHAT_ID'].strip('"\''), 'text': text,
            'message_thread_id': 1360}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{e['TG_BOT_TOKEN'].strip(chr(34) + chr(39))}/sendMessage",
            data, timeout=20)
    except Exception as ex:
        log(f'（TG 發送失敗，修正版已存進資料庫）：{ex}')
    print(text)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--all' in sys.argv:
        rows = d1("""SELECT job_slug FROM job_audits a
                     WHERE a.verdict='要修文案'
                       AND NOT EXISTS (SELECT 1 FROM job_audits b
                                        WHERE b.job_slug=a.job_slug AND b.verdict='已產修正版'
                                          AND b.audited_at > a.audited_at)
                     GROUP BY job_slug""")
        log(f'要修的職缺：{len(rows)} 個')
        for r in rows:
            try:
                fix(r['job_slug'], do_apply='--dry' not in sys.argv)
            except Exception as e:
                log(f"❌ {r['job_slug']}：{e}")
        return
    if not args:
        print(__doc__)
        return
    fix(args[0], do_apply='--dry' not in sys.argv)


if __name__ == '__main__':
    main()
