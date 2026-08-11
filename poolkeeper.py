#!/usr/bin/env python3
"""履歷池管理員：定期回頭撈池子裡的人，配對現在開著的職缺。

為什麼要有這支：沒有它的話，履歷池就是墳場——人丟進去就沒了，誰也不會回頭看。
一個人當初被判低分，往往不是他不好，是「那個時候只有那個缺」。
三個月後開了新缺，他可能正好合適，但沒有人會想起他。

做三件事：
1. **反向匹配**：拿池子裡的人去對現在開著的職缺，找出「當初不合、現在合」的
2. **標記沉睡**：太久沒動的人選標出來，讓顧問決定要重新聯繫還是放掉
3. **只報告值得看的**：沒有配對成功就安靜，不要每週推一則「本週無事」

⚠️ 這支**不會**主動聯繫任何候選人。它只告訴顧問「這個人可以再看一次」，
聯繫是人的事——而且隔了三個月再聯絡，講什麼、怎麼講，本來就該人來判斷。

用法：
    python3 poolkeeper.py            # 正常跑，配到才推 Telegram
    python3 poolkeeper.py --dry      # 只印不推（調規則時用）
"""
import os, sys, json, re, subprocess, argparse, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('d', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)

MODEL = 'claude-sonnet-5'        # 這是「值不值得再看一眼」的粗篩，不是最終判斷，用不到 opus
THREAD_WINDOW = 2855    # 顧問窗口
THREAD_POOL = 304       # 履歷池
MATCH_MIN = 70          # 反向配對要到這個分數才值得驚動顧問
SLEEP_DAYS = 90         # 超過這麼久沒動作就算沉睡

SCHEMA = """{
  "matches": [
    {"job_slug":"", "score":0, "why":"一句話：為什麼這個人配這個缺，要具體",
     "gap":"最主要的落差，沒有就填空字串"}
  ]
}"""


def pool_people():
    """池子裡的人＝初篩低分、或從來沒被送件出去過的。

    ⚠️ 已經送件（placements 有紀錄）的人不要撈——那些正在跑，
    再推一次只會讓顧問以為是新的。
    """
    return D.d1(f"""
        SELECT a.id, a.name, a.job_slug, a.job_title, a.created_at,
               a.expected_salary, a.available_date, a.location_ok,
               s.score, s.summary, s.strengths, s.risks,
               CAST(julianday('now','+8 hours') - julianday(a.created_at) AS INTEGER) AS age_days
          FROM applications a
          LEFT JOIN screenings s ON s.application_id = a.id
         WHERE NOT EXISTS (SELECT 1 FROM placements p WHERE p.application_id = a.id)
           AND (s.score IS NULL OR s.score < {MATCH_MIN})
         ORDER BY a.created_at DESC LIMIT 60""")


def open_jobs():
    return D.d1("SELECT slug,title,must_skills,years_min,salary_min,salary_max,locations,"
                "employment,client_name FROM jobs WHERE status IN ('open','active')")


def match(person, jobs):
    lines = []
    for j in jobs:
        if j['slug'] == person.get('job_slug'):
            continue          # 當初應徵的那個缺不用再配一次
        lines.append(f"- {j['slug']}｜{j['title']}｜必備：{(j.get('must_skills') or '未填')[:80]}"
                     f"｜年資≥{j.get('years_min') if j.get('years_min') is not None else '?'}"
                     f"｜{j.get('salary_min') or '?'}-{j.get('salary_max') or '?'}"
                     f"｜{(j.get('locations') or '未填')[:20]}")
    if not lines:
        return []

    prompt = f"""你是資深獵頭。下面這個人當初應徵的職缺不合（或還沒被送出去），
現在手上有一批開著的缺。判斷**有沒有哪一個明顯更適合他**。

## 這個人
姓名代號：{person['name']}
當初應徵：{person.get('job_title') or person.get('job_slug')}
初篩分數：{person.get('score') if person.get('score') is not None else '（沒跑過初篩）'}
初篩結論：{person.get('summary') or '（無）'}
強項：{person.get('strengths') or '[]'}
落差：{person.get('risks') or '[]'}
期望薪資：{person.get('expected_salary') or '未填'}
可到職：{person.get('available_date') or '未填'}
地點：{person.get('location_ok') or '未填'}

## 現在開著的職缺
{chr(10).join(lines)}

## 規則
- **寧缺勿濫。** 沒有明顯更適合的就回空陣列，不要硬湊。
  這份結果會直接推到顧問眼前，湊出來的配對會讓他以後不再看這個通知。
- 分數 0–100，只給你真的認為值得再看一眼的（{MATCH_MIN} 分以上才會被採用）。
- 薪資、地點、可到職這種硬性落差要寫進 gap，不要當作沒看到。
- 最多回 2 個，挑最像的。

只輸出 JSON，不要其他文字：
{SCHEMA}
"""
    r = subprocess.run(['claude', '-p', D.sanitize(prompt), '--model', MODEL,
                        *D.NO_TOOLS, '--output-format', 'text'],
                       capture_output=True, text=True, env=D.env_with_cf(), timeout=240)
    out = (r.stdout or '').strip()
    m = re.search(r'\{.*\}', out, re.S)
    if not m:
        return []
    try:
        got = json.loads(m.group(0)).get('matches') or []
    except Exception:
        return []
    return [x for x in got if int(x.get('score') or 0) >= MATCH_MIN]


def push(text, thread):
    import urllib.request, urllib.parse
    e = dict(l.strip().split('=', 1)
             for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
             if '=' in l and not l.startswith('#'))
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
        data=urllib.parse.urlencode(
            {'chat_id': e['TG_CHAT_ID'], 'message_thread_id': thread, 'text': text}).encode(),
        timeout=20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true', help='只印不推')
    a = ap.parse_args()

    people, jobs = pool_people(), open_jobs()
    print(f'池子裡 {len(people)} 人｜開著的職缺 {len(jobs)} 個')
    if not people or not jobs:
        return

    hits, sleeping = [], []
    for p in people:
        if (p.get('age_days') or 0) > SLEEP_DAYS:
            sleeping.append(p)
        for m in match(p, jobs):
            j = next((x for x in jobs if x['slug'] == m.get('job_slug')), None)
            if not j:
                continue
            hits.append((p, j, m))
            print(f"  ⭐ {p['name']} → {j['title'][:24]}　{m.get('score')}分")

    if hits:
        lines = ['♻️ 履歷池撈到可以再看的人', '']
        for p, j, m in hits[:8]:
            lines.append(f"・{p['name']}　→　{j['title'][:26]}　{m.get('score')}分")
            lines.append(f"　 {m.get('why', '')[:90]}")
            if m.get('gap'):
                lines.append(f"　 ⚠️ {m['gap'][:60]}")
        lines += ['', '要不要聯繫由你決定——隔了一段時間，講什麼怎麼講是人的事。']
        txt = '\n'.join(lines)
        print(f'\n→ 推顧問窗口（{len(hits)} 筆）')
        if not a.dry:
            push(txt, THREAD_WINDOW)
    else:
        print('  沒有值得驚動顧問的配對（這是正常的，寧缺勿濫）')

    if sleeping:
        txt = (f'😴 沉睡超過 {SLEEP_DAYS} 天：{len(sleeping)} 位\n\n'
               + '\n'.join(f"・{x['name']}　{(x.get('job_title') or '')[:20]}　"
                           f"{x['age_days']} 天" for x in sleeping[:10])
               + '\n\n這些人可能已經就業了。要重新聯繫還是放掉，你決定。')
        print(f'→ 推履歷池（沉睡 {len(sleeping)} 位）')
        if not a.dry:
            push(txt, THREAD_POOL)

    # 這支一週只跑一次，每次都值得記——「本週沒配到」也是有意義的資訊
    if not a.dry:
        D.runlog('step1ne-poolkeeper', 'success',
                 f'履歷池 {len(people)} 人 × {len(jobs)} 個開缺：'
                 + (f'配到 {len(hits)} 組' if hits else '沒有值得驚動顧問的配對')
                 + (f'；沉睡 {len(sleeping)} 位' if sleeping else ''),
                 {'pool': len(people), 'jobs': len(jobs),
                  'matched': len(hits), 'sleeping': len(sleeping)})


if __name__ == '__main__':
    main()
