#!/usr/bin/env python3
"""履歷 × JD 初篩：算匹配分數、標風險、生面試追問。

流程圖上「收履歷」與「AI 面談」中間那個紅框。在此之前那一段是空的——
履歷抽完文字就直接進面談，沒有任何人（或東西）先看過它適不適合。

判準不是我編的，是 Phoebe 寫在 Prompy 主題裡的那份《獵頭顧問助理-履歷分析SOP》：
必要條件逐條比對（✅符合／⚠️存疑／❌不符）、匹配分數 0–100、硬性門檻獨立警示、
針對缺口生面試追問。原本是「人貼給 Claude」的手動流程，這裡把它變成自動的。

分流（門檻沿用舊系統 telegram-groups.json 的設定，不要另外發明一套）：
    ≥80  高    → 推「面試通知確認」，值得優先看
    70-79 中   → 只寫進資料庫，不吵人
    <70  低    → 推「#4 履歷池」，當作以後可能用得上的人

用法：
    python3 screen_resumes.py            # 掃所有還沒初篩的
    python3 screen_resumes.py 范博翔      # 指定一位（會重跑，用來校準判準）
"""
import os, sys, json, re, subprocess, argparse, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('d', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)

MODEL = 'claude-sonnet-5'          # 這個分數會決定要不要花顧問時間，寫壞比慢貴
RUBRIC_VER = 'phoebe-v4-multiclient'  # 判準版本。校準之後要改這個，才分得出新舊評分

# 2026-08-05 從 90 調成 80。舊系統 telegram-groups.json 訂的是 90，但實測跑下來
# 「必要條件全符合＋有加分」大約落在 85（范博翔），90 幾乎沒人會被推到顧問面前，
# 等於這條通知路徑形同虛設。門檻要對得起實際分數分布，不是照抄舊設定。
HIGH, MID = 80, 70
THREAD_WINDOW = 2855    # 面試通知確認（顧問窗口）
THREAD_POOL = 304       # #4 履歷池

SCHEMA = """{
  "fit_score": 0,
  "fit_reason": "這個分數怎麼來的：哪幾條符合、哪幾條不符，各佔多少",
  "verdict": "strong | yes | hold | no",
  "summary": "一句話：這個人值不值得花時間，為什麼",
  "must_have": [{"item":"必要條件原文","actual":"人選實際狀況","result":"符合|存疑|不符"}],
  "nice_to_have": [{"item":"","actual":"","result":"符合|存疑|不符"}],
  "strengths": ["最強命中點，具體，帶數字"],
  "risks": ["面談要釐清的疑慮。⚠️ 這些不影響 fit_score"],
  "questions": ["針對上面每個風險，設計一題可以直接唸出來的面試追問"],
  "hard_fail": "有硬性條件不符就寫是哪一條與影響；沒有就填空字串",
  "suggest_client": "如果這個職缺有多家用人單位（看 scoring_notes 有沒有寫分軌），建議送哪一家；只有一家或判斷不出來就填空字串",
  "blocker": "missing_info | not_fit | none —— 分數上不去的原因是哪一種：\n  missing_info＝資料不足，問一下就知道（表單沒填、履歷沒寫）\n  not_fit＝條件真的不符\n  none＝沒有卡點",
  "one_call": "如果 blocker 是 missing_info，寫出「打這通電話要問哪幾件事」，一句話；否則空字串"
}"""

def build_prompt(app, job, resume_text):
    return f"""你是有 15 年經驗的資深招募經理。針對下面的職缺與履歷做初篩，
判準照 Step1ne 現行的《獵頭顧問助理 SOP》。

## 判準

**分數只算「這個 JD 寫的條件」符合到什麼程度，不要算別的。**
這是 2026-08-05 改的：第一版讓模型自由給分，結果它拿「英文能力」「職級是否往回走」
「職涯目標沒提到這個職種」去扣分——**那三件事這份 JD 一個字都沒提**。
每個職缺看重的東西不同，模型不該自己發明標準。

1. **必要條件逐條比對** —— 每一條都要有「人選實際狀況」，結果只能是 符合／存疑／不符。
   履歷沒寫就是「存疑」，不是「不符」。
2. **加分條件逐條比對** —— 同上。
3. **fit_score 0–100，只根據上面兩張表算**：
   - 必要條件全部符合 → 底分 80 起跳
   - 必要條件有「存疑」→ 每條扣 5
   - 必要條件有「不符」→ 上限壓到 60 以下
   - 加分條件符合的往上加，最多加到 100
   - **年資、學歷這種 JD 沒要求的（例如 years_min=0、學歷不拘），不要拿來扣分。**
     JD 說歡迎無經驗，那有經驗就是加分，不是理所當然。
   - 在 `fit_reason` 寫清楚這個分數怎麼算出來的，讓人可以吵。
4. **risks 是「面談要釐清的」，不是扣分項** —— 動機、穩定度、職級落差這類寫進 risks
   與 questions，**不要因此降低 fit_score**。那些要問過才知道，不是履歷上的事實。
5. **分數上不去時要分清楚是哪一種**（寫進 blocker）：
   - 「表單沒填、履歷沒寫」→ `missing_info`，這是問一下就知道的事，不是這個人不行
   - 「條件真的不符」→ `not_fit`
   顧問的實際做法是：資料不足就打一通電話問清楚再決定。所以這兩種要分開，
   混在一起的話，「該打電話的」會跟「真的不合的」被當成同一件事。
6. **硬性門檻** —— 客戶開的硬性條件、可到職時間、地點、證照這種**明確寫在 JD 裡**的
   不符合，寫進 hard_fail。JD 沒寫的不算硬性條件。

## 鐵則
- **不要美化，也不要苛刻。** 履歷沒寫的就是沒寫，不要腦補成有。
- 分數要能跟別人比。同一個職缺、同樣條件的人，這次給 85 下次不能給 60。
- 判斷依據只能來自履歷與 JD，不要引用外部對這家公司或這個人的印象。

## 職缺
{job.get('title') or app.get('job_title') or ''}
客戶：{job.get('client_name') or '（未綁）'}
必備技能：{job.get('must_skills') or '（未填）'}
年資門檻：{job.get('years_min') if job.get('years_min') is not None else '（未填）'}
薪資：{job.get('salary_min') or '?'} – {job.get('salary_max') or '?'}
地點：{job.get('locations') or '（未填）'}
僱用型態：{job.get('employment') or '（未填）'}
客戶硬性條件：{job.get('client_screen_conditions') or '（無）'}
顧問特別交代（這個職缺最看重什麼）：{job.get('scoring_notes') or '（未填，就照 JD 條件判斷）'}

## 候選人表單填的
期望薪資：{app.get('expected_salary') or '（未填）'}
可到職：{app.get('available_date') or '（未填）'}
地點可否：{app.get('location_ok') or '（未填）'}

## 履歷原文
{(resume_text or '（無履歷文字）')[:14000]}

## 輸出
只輸出一個 JSON 物件，不要有其他文字、不要包在程式碼區塊裡：
{SCHEMA}
"""


def screen_one(app):
    job = (D.d1(f"SELECT title,client_name,must_skills,years_min,salary_min,salary_max,scoring_notes,"
                f"locations,employment,client_screen_conditions FROM jobs "
                f"WHERE slug={D.q(app['job_slug'])}") or [{}])[0]

    txt = app.get('resume_url_text') or ''
    if app.get('resume_file_id'):
        f = D.d1(f"SELECT text_content FROM files WHERE id={D.q(app['resume_file_id'])}")
        if f and f[0].get('text_content'):
            txt = f[0]['text_content']
    if not txt:
        print(f"  ⏭ {app['name']}：沒有履歷文字，跳過")
        return None

    r = subprocess.run(['claude', '-p', D.sanitize(build_prompt(app, job, txt)),
                        '--model', MODEL, *D.NO_TOOLS, '--output-format', 'text'],
                       capture_output=True, text=True, env=D.env_with_cf(), timeout=300)
    out = (r.stdout or '').strip()
    if out.startswith('```'):
        out = out.split('\n', 1)[-1]
        if out.rstrip().endswith('```'):
            out = out.rstrip()[:-3]
    m = re.search(r'\{.*\}', out, re.S)
    if not m:
        print(f"  ❌ {app['name']}：模型沒回 JSON\n"
              f"     returncode={r.returncode}　stderr={(r.stderr or '')[-200:]}")
        return None
    c = json.loads(m.group(0))

    D.d1(
        "INSERT INTO screenings (application_id, job_slug, score, verdict, summary, "
        "strengths, risks, questions, hard_fail, blocker, one_call, suggest_client, model, rubric_ver) VALUES ("
        f"{D.q(app['id'])}, {D.q(app['job_slug'])}, {int(c.get('fit_score') or 0)}, "
        f"{D.q(c.get('verdict'))}, {D.q(c.get('summary'))}, "
        f"{D.q(json.dumps(c.get('strengths') or [], ensure_ascii=False))}, "
        f"{D.q(json.dumps(c.get('risks') or [], ensure_ascii=False))}, "
        f"{D.q(json.dumps(c.get('questions') or [], ensure_ascii=False))}, "
        f"{D.q(c.get('hard_fail') or '')}, {D.q(c.get('blocker') or '')}, "
        f"{D.q(c.get('one_call') or '')}, {D.q(c.get('suggest_client') or '')}, "
        f"{D.q(MODEL)}, {D.q(RUBRIC_VER)})")
    return c


def notify(app, c):
    """高分推顧問窗口、低分推履歷池、中分不吵人。

    ⚠️ 已經送件給客戶的人不推——2026-08-05 踩到：張博州、呂皓宇早就被顧問送出去了，
    初篩卻把他們推進「履歷池」，看起來像是被打回票。已經在跑的案子不該出現在池子裡。

    ⚠️ 中分不通知是刻意的——70–89 這一段是「可以但不急」，
    每一筆都推的話，真正該看的高分會被埋掉。要看中分的去儀表板。
    """
    if D.d1(f"SELECT 1 FROM placements WHERE application_id={D.q(app['id'])} LIMIT 1"):
        return '已送件→不通知'
    score = int(c.get('fit_score') or 0)
    hard = (c.get('hard_fail') or '').strip()
    head = f"{app['name']}　{(app.get('job_title') or app['job_slug'])[:24]}"
    risks = '\n'.join(f'・{x}' for x in (c.get('risks') or [])[:3])
    qs = '\n'.join(f'{i+1}. {x}' for i, x in enumerate((c.get('questions') or [])[:3]))

    if score >= HIGH and not hard:
        sug = f"　→ 建議送 {c['suggest_client']}" if c.get('suggest_client') else ''
        text = (f"⭐ 初篩高分 {score}／100{sug}\n{head}\n\n"
                f"{c.get('summary') or ''}\n\n"
                f"強項\n" + '\n'.join(f'・{x}' for x in (c.get('strengths') or [])[:3]) +
                (f"\n\n要問的\n{qs}" if qs else ''))
        D.tg(text)   # tg() 預設就是顧問窗口
        return '高分→顧問窗口'
    # 卡在「資料不足」的，不能靜靜放著——顧問的做法是打一通電話問清楚再決定，
    # 但沒人通知他的話，他根本不知道該打。2026-08-05 呂皓宇就是這種：
    # 70 分、四項門檻過了兩項、另外兩項只是表單沒填。
    if score >= MID and (c.get('blocker') == 'missing_info'):
        text = (f"🔔 打一通電話就能決定　{score}／100\n{head}\n\n"
                f"{c.get('summary') or ''}\n\n"
                f"要問的：{c.get('one_call') or '確認缺的那幾項'}")
        D.tg(text)
        return '待確認→顧問窗口'
    if score < MID or hard:
        text = (f"📥 進履歷池　{score}／100\n{head}\n\n"
                f"{c.get('summary') or ''}"
                + (f"\n\n🚨 硬性不符：{hard}" if hard else '')
                + (f"\n\n缺口\n{risks}" if risks else ''))
        _tg_thread(text, THREAD_POOL)
        return '低分→履歷池'
    return '中分→只寫資料庫'


def _tg_thread(text, thread):
    import urllib.request, urllib.parse
    e = dict(l.strip().split('=', 1)
             for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
             if '=' in l and not l.startswith('#'))
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
            data=urllib.parse.urlencode(
                {'chat_id': e['TG_CHAT_ID'], 'message_thread_id': thread, 'text': text}).encode(),
            timeout=20)
    except Exception as ex:
        print(f'  ⚠️ 推播失敗：{ex}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('who', nargs='?', help='候選人姓名或 id；不給就掃所有還沒初篩的')
    ap.add_argument('--no-notify', action='store_true', help='只寫資料庫，不推 Telegram（校準用）')
    a = ap.parse_args()

    if a.who:
        apps = D.d1(f"SELECT id,name,job_slug,job_title,resume_file_id,resume_url_text,"
                    f"expected_salary,available_date,location_ok FROM applications "
                    f"WHERE id={D.q(a.who)} OR name={D.q(a.who)} LIMIT 1")
    else:
        apps = D.d1("SELECT id,name,job_slug,job_title,resume_file_id,resume_url_text,"
                    "expected_salary,available_date,location_ok FROM applications a "
                    "WHERE NOT EXISTS (SELECT 1 FROM screenings s WHERE s.application_id=a.id) "
                    "ORDER BY created_at DESC LIMIT 10")
    if not apps:
        print('沒有需要初篩的候選人')
        return
    print(f'要初篩 {len(apps)} 位')
    done, failed, hi = [], 0, 0
    for app in apps:
        print(f"── {app['name']}")
        c = screen_one(app)
        if not c:
            failed += 1
            continue
        where = '（未通知）' if a.no_notify else notify(app, c)
        print(f"   {c.get('fit_score')}／100　{c.get('verdict')}　{where}")
        if c.get('hard_fail'):
            print(f"   🚨 {c['hard_fail'][:70]}")
        done.append((app['name'], c.get('fit_score')))
        if (c.get('fit_score') or 0) >= HIGH:
            hi += 1
    # ⚠️ 沒事就不記——這支每 20 分鐘跑一次，每次都記的話一天 72 筆「今天沒事」。
    # ⚠️ 而且**跳過不算失敗**：沒有履歷文字就跳過是正常流程，
    #    2026-08-06 第一版把測試資料的跳過記成「1 位失敗」，那是假警報。
    #    只有真的篩了人才記。
    if done:
        D.runlog('step1ne-screening', 'success' if not failed else 'partial',
                 '初篩 ' + '、'.join(f'{n} {sc}分' for n, sc in done[:5])
                 + (f'（另 {len(done)-5} 位）' if len(done) > 5 else '')
                 + (f'；{failed} 位失敗' if failed else ''),
                 {'screened': len(done), 'high': hi, 'failed': failed})


if __name__ == '__main__':
    main()
