#!/usr/bin/env python3
"""special_check.py — 特殊職缺檢查卡（2026-10-23 總指揮交辦，第一個套用：築樂 主管／家庭專屬司機）

只對「被標成特殊職缺」的職缺產生：site_settings key='special_check_jobs'（JSON 陣列，職缺 slug）。
jobs 表已滿 100 欄，所以不加欄位；其他職缺完全不受影響（不在清單裡就連查都不查）。

流程：
  ai_worker.tick() → scan()：清單內職缺的人選，有履歷文字、而且（還沒有卡／顧問記了出席或按重算／阿財面談剛結束）
    → 先搶 special_check_cards 那一列（原子 INSERT OR IGNORE／帶條件 UPDATE，兩台機器不會重複排）
    → 排 ai_jobs(kind='special_check', payload={application_id})
  ai_worker.process() → run()：讀履歷＋職缺＋出席紀錄（＋阿財面談報告），請 AI 產
    ① 履歷疑點（每條寫具體年月）② 三個分數（願意面談／成功錄取／穩定性，0～100＋一句理由）
    → 寫回 special_check_cards.result_json

⚠️ 硬規則
  - 分數只能依：換工作頻率／平均任期、相關經驗、居住地到台北市距離、期望薪資 vs 職缺月薪、104 履歷更新時間、
    求職意向是否相符、放鳥紀錄。**絕不能用年齡、性別、婚育、外貌、國籍、身心障礙**（就業服務法第 5 條）。
    兩道防線：送進 AI 前先把履歷裡的出生／性別／婚姻／國籍等行拿掉；AI 回來的文字再掃一次禁用詞，有就重產，
    還有就遮蔽。
  - 司法院裁判書：該系統有機器人防護（2026-10-23 實測結果清單頁被 bot-defense 擋），**這支不自動查**，
    卡片上只給顧問自己去查的入口（後台 API 組連結）。
  - **不推任何 TG 通知**（Jacky：只有真的跟阿財面談完才推 TG）。

手動：
  python3 special_check.py scan            # 掃一輪（只排隊）
  python3 special_check.py run <app_id>    # 直接產這位人選的卡（不經佇列，測試用）
"""
import datetime
import json
import os
import re
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402

SETTING_KEY = 'special_check_jobs'
SCAN_EVERY_SEC = 60
_last_scan = 0.0

FLAG_TYPES = ('畢業與工作時間對不上', '工作空白超過6個月', '工作時間重疊', '最早經歷之前沒寫', '短時間頻繁換工作', '自傳與經歷不一致')

# 送進 AI 前先拿掉的履歷行（AI 根本看不到，就不可能拿來評分）
_STRIP_LINE = re.compile(r'(出生|生日|年齡|年紀|\d{2}\s*歲|性別|婚姻|已婚|未婚|懷孕|身高|體重|血型|國籍|宗教|身心障礙|殘障|身障)')
# AI 回來的文字不准出現的字眼（出現就重產；仍出現就遮蔽）
_BANNED = re.compile(r'(年齡|年紀|\d{2}\s*歲|年輕|年長|高齡|性別|男性|女性|男生|女生|已婚|未婚|婚姻|婚育|有小孩|有子女|照顧小孩|照顧子女|育兒|懷孕|生育|外貌|長相|身高|體重|國籍|外籍|原住民|身心障礙|殘障|身障)')


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] [special_check] {m}', flush=True)


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def rows(sql):
    return d1_http.query(sql).get('results') or []


def now_tpe():
    return (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')


def special_jobs():
    r = rows(f"SELECT value FROM site_settings WHERE key={q(SETTING_KEY)}")
    if not r:
        return []
    try:
        v = json.loads(r[0].get('value') or '[]')
    except Exception:
        return []
    return [s for s in v if isinstance(s, str) and re.fullmatch(r'[A-Za-z0-9_-]{2,120}', s)]


# ── 掃描：誰要產／重產 ──────────────────────────────────────────

def scan(force=False):
    global _last_scan
    if not force and time.time() - _last_scan < SCAN_EVERY_SEC:
        return 0
    _last_scan = time.time()
    slugs = special_jobs()
    if not slugs:
        return 0
    ins = ','.join(q(s) for s in slugs)
    cand = rows(
        "SELECT a.id, a.job_slug, c.application_id AS has_card FROM applications a "
        "LEFT JOIN files f ON f.id = a.resume_file_id "
        "LEFT JOIN special_check_cards c ON c.application_id = a.id "
        f"WHERE a.job_slug IN ({ins}) AND a.superseded_by IS NULL "
        "  AND (COALESCE(f.text_content,'') <> '' OR COALESCE(a.resume_url_text,'') <> '') "
        "  AND (c.application_id IS NULL OR c.needs_refresh = 1 "
        "       OR (a.interview_ended_at IS NOT NULL AND a.interview_ended_at > COALESCE(c.updated_at,''))) "
        "  AND NOT EXISTS (SELECT 1 FROM ai_jobs j WHERE j.kind='special_check' AND j.status IN ('pending','running') "
        "                  AND j.payload_json LIKE '%' || a.id || '%') "
        "LIMIT 20")
    n = 0
    now = now_tpe()
    for a in cand:
        aid = a['id']
        if a.get('has_card'):
            claim = d1_http.query(
                f"UPDATE special_check_cards SET status='queued', needs_refresh=0, updated_at={q(now)} "
                f"WHERE application_id={q(aid)} AND (needs_refresh=1 OR EXISTS (SELECT 1 FROM applications x "
                f"WHERE x.id={q(aid)} AND x.interview_ended_at IS NOT NULL AND x.interview_ended_at > COALESCE(special_check_cards.updated_at,'')))")
        else:
            claim = d1_http.query(
                "INSERT OR IGNORE INTO special_check_cards (application_id, job_slug, status, needs_refresh, updated_at) "
                f"VALUES ({q(aid)}, {q(a['job_slug'])}, 'queued', 0, {q(now)})")
        if not (claim.get('meta') or {}).get('changes'):
            continue   # 別台機器剛搶走
        d1_http.query(
            "INSERT INTO ai_jobs (id, kind, payload_json, status, created_at) VALUES ("
            f"{q(str(uuid.uuid4()))}, 'special_check', {q(json.dumps({'application_id': aid}))}, 'pending', {q(now)})")
        n += 1
    if n:
        log(f'排了 {n} 張特殊職缺檢查卡')
    return n


# ── 產卡 ──────────────────────────────────────────────────────

def _clean_resume(t):
    t = ''.join(c for c in str(t or '') if c in '\n\t' or ord(c) >= 32)
    return '\n'.join(line for line in t.splitlines() if not _STRIP_LINE.search(line))


def attendance_summary(aid):
    att = rows(f"SELECT result, appointment_id FROM special_check_attendance WHERE application_id={q(aid)}")
    appts = rows(f"SELECT id FROM interview_appointments WHERE application_id={q(aid)} AND COALESCE(status,'') != 'superseded'")
    attended = sum(1 for x in att if x['result'] == 'attended')
    no_show = sum(1 for x in att if x['result'] == 'no_show')
    booked = len(appts) + sum(1 for x in att if not x.get('appointment_id'))
    return {'booked': max(booked, len(att)), 'attended': attended, 'no_show': no_show}


def _load(aid, job_slug_override=None):
    a = rows("SELECT a.id, a.name, a.job_slug, a.expected_salary, a.available_date, a.location_ok, a.note, a.resume_104_url, "
             "a.resume_url_text, a.resume_extra_files, a.resume_source, a.interview_ended_at, f.text_content "
             f"FROM applications a LEFT JOIN files f ON f.id = a.resume_file_id WHERE a.id={q(aid)}")
    if not a:
        raise RuntimeError(f'找不到人選 {aid}')
    a = a[0]
    texts = [a.get('text_content') or '', a.get('resume_url_text') or '']
    try:
        extra = json.loads(a.get('resume_extra_files') or '[]') or []
    except Exception:
        extra = []
    ids = [x.get('file_id') for x in extra if isinstance(x, dict) and x.get('file_id')][:5]
    if ids:
        for r in rows(f"SELECT text_content FROM files WHERE id IN ({','.join(q(i) for i in ids)})"):
            texts.append(r.get('text_content') or '')
    resume = _clean_resume('\n\n────（下一份）────\n\n'.join(t for t in texts if t.strip()))[:30000]
    job = rows("SELECT slug, title, salary_min, salary_max, salary_unit, locations, required_conditions, must_skills, "
               "nice_to_have_skills, preferred_background, main_duties, work_hours "
               f"FROM jobs WHERE slug={q(job_slug_override or a['job_slug'])}")
    job = job[0] if job else {'slug': a['job_slug']}
    rep = rows(f"SELECT content_md FROM reports WHERE application_id={q(aid)} ORDER BY created_at DESC LIMIT 1")
    report = _clean_resume((rep[0].get('content_md') if rep else '') or '')[:6000]
    return a, job, resume, report


def build_prompt(a, job, resume, report, att, today):
    sal = f"{job.get('salary_min') or ''}～{job.get('salary_max') or ''} {job.get('salary_unit') or ''}".strip('～ ')
    return f"""你是台灣獵頭公司 Step1ne 的資深顧問助理。這是一個「特殊職缺」的人選檢查，只給內部顧問看。
請讀人選履歷，產出「履歷疑點」與「三個分數」。今天日期：{today}

【職缺】{job.get('title') or job.get('slug')}
- 月薪：{sal}（4 萬起）
- 地點：{job.get('locations') or ''}
- 必要條件：{job.get('required_conditions') or job.get('must_skills') or ''}
- 加分條件：{job.get('nice_to_have_skills') or ''}
- 偏好背景：{job.get('preferred_background') or ''}
- 主要工作：{job.get('main_duties') or ''}
- 班別：{job.get('work_hours') or ''}

【應徵單資訊】
- 期望薪資：{a.get('expected_salary') or '（沒填）'}
- 可上班日：{a.get('available_date') or '（沒填）'}
- 地點可配合：{a.get('location_ok') or '（沒填）'}
- 履歷來源：{a.get('resume_source') or ''}{'（有 104 履歷連結）' if a.get('resume_104_url') else ''}

【面談出席紀錄】約了 {att['booked']} 次、到了 {att['attended']} 次、放鳥 {att['no_show']} 次

【阿財面談摘要】
{report or '（還沒有面談）'}

【履歷全文】
{resume or '（沒有履歷文字）'}

────────────
只輸出一個 JSON（不要任何其他文字）：
{{
 "resume_flags": [{{"type": "{'｜'.join(FLAG_TYPES)} 其中之一", "detail": "具體寫出哪兩段、幾年幾月到幾年幾月"}}],
 "facts": {{
   "avg_tenure_months": 平均每份工作幾個月（數字，算不出來填 null）,
   "jobs_last_5y": 最近 5 年做過幾份工作（數字或 null）,
   "relevant_experience": "主管司機／家庭司機／VIP 接送／大型 MPV（LM、Alphard 等）相關經驗，一句話；沒有就寫「履歷沒寫到」",
   "residence": "履歷寫的居住地（寫到區即可），沒寫就「沒寫」",
   "commute": "到台北市的通勤判斷，一句話",
   "salary_fit": "期望薪資 vs 月薪 4 萬起，一句話",
   "resume_updated": "104 履歷更新時間（履歷有寫才填），沒寫就「看不出來」",
   "intent_fit": "求職意向（希望職類／地點）跟這個職缺合不合，一句話"
 }},
 "scores": {{
   "interview_willingness": {{"score": 0到100整數, "reason": "一句話理由"}},
   "offer_success": {{"score": 0到100整數, "reason": "一句話理由"}},
   "stability": {{"score": 0到100整數, "reason": "一句話理由"}}
 }}
}}

【履歷疑點規則】
- 只列真的看得到的疑點，每條 detail 一定要寫具體年月（例：「2019/03 離開 A 公司，2020/05 才到 B 公司，中間 14 個月沒寫」）。
- 六種類型：
  1. 畢業與工作時間對不上：畢業年月之前就有全職工作；**在學期間（含二技、進修部）同時做全職工作、而且工作內容寫需要長時間駐點／常駐海外／24 小時待命**，兩邊時間怎麼兼顧要問清楚；畢業後很久才開始寫工作（扣掉兵役）。
  2. 工作空白超過6個月：兩份工作之間超過 6 個月沒寫（兵役、進修有寫出來的不算）。也看最後一份工作離職到今天。
  3. 工作時間重疊：兩份全職工作時間重疊（兼職／接案有寫明的不算）。
  4. 最早經歷之前沒寫：最早一份工作之前有一大段時間完全沒交代；**學歷銜接有缺（例如只寫二技／大學，沒寫入學前的高中職或專科）**；**第一份寫出來的工作就是主管職，之前沒有任何基層經歷**。
  5. 短時間頻繁換工作：最近 5 年有 3 份以上做不到 1 年的工作。
  6. 自傳與經歷不一致：自傳裡講的年資、公司、職務跟經歷表對不上。
- 履歷沒寫年月就沒辦法判斷，不要硬掰；沒有疑點就回空陣列。
- 應徵單填的期望薪資跟履歷寫的希望待遇不一樣，也算「自傳與經歷不一致」的一種，寫出兩邊各寫什麼。

【分數規則】
- 依據**只能**是：換工作頻率／平均任期、相關經驗、居住地到台北市距離、期望薪資 vs 月薪 4 萬起、104 履歷更新時間、求職意向是否相符、放鳥紀錄。
- **絕對不能**用或提到年齡、性別、婚姻／生育、外貌、國籍、身心障礙（就業服務法第 5 條）。理由裡也不能出現這些字眼。
- interview_willingness（願意來面談的機率）：意向相符、履歷近期有更新、通勤近、薪資期望在範圍內 → 高；放鳥過 → 明顯降低（放鳥 2 次以上不超過 30）。
- offer_success（被客戶錄取的機率）：相關經驗（主管／家庭／VIP 司機、MPV）最重要，其次是穩定性與薪資是否對得上。
- stability（到職後做得久的機率）：平均任期越長越高；短期頻繁換工作、空白期多 → 低。
- 資料不足時給 50 附近，理由寫「資料不足：缺什麼」。
- reason 一句話，30 字內，講依據不講形容詞。
"""


def _validate(d):
    if not isinstance(d, dict):
        raise ValueError('不是 JSON 物件')
    flags = d.get('resume_flags')
    if not isinstance(flags, list):
        raise ValueError('resume_flags 不是陣列')
    sc = d.get('scores') or {}
    for k in ('interview_willingness', 'offer_success', 'stability'):
        s = sc.get(k)
        if not isinstance(s, dict) or not isinstance(s.get('score'), (int, float)) or not str(s.get('reason') or '').strip():
            raise ValueError(f'scores.{k} 缺分數或理由')
        s['score'] = max(0, min(100, int(round(s['score']))))
    clean = []
    for f in flags:
        if isinstance(f, dict) and str(f.get('detail') or '').strip():
            t = str(f.get('type') or '').strip()
            clean.append({'type': t if t in FLAG_TYPES else (t or '其他'), 'detail': str(f['detail']).strip()})
    d['resume_flags'] = clean
    if not isinstance(d.get('facts'), dict):
        d['facts'] = {}
    return d


def _banned_hits(d):
    hits = []
    for k, s in (d.get('scores') or {}).items():
        m = _BANNED.search(str(s.get('reason') or ''))
        if m:
            hits.append(f'scores.{k}：{m.group(0)}')
    for i, f in enumerate(d.get('resume_flags') or []):
        m = _BANNED.search(str(f.get('detail') or ''))
        if m:
            hits.append(f'resume_flags[{i}]：{m.group(0)}')
    for k, v in (d.get('facts') or {}).items():
        m = _BANNED.search(str(v or ''))
        if m:
            hits.append(f'facts.{k}：{m.group(0)}')
    return hits


def _mask(d):
    for s in (d.get('scores') or {}).values():
        if _BANNED.search(str(s.get('reason') or '')):
            s['reason'] = '（理由用到不得使用的條件，已遮蔽；請按「重算」）'
    d['resume_flags'] = [f for f in d.get('resume_flags') or [] if not _BANNED.search(str(f.get('detail') or ''))]
    for k, v in list((d.get('facts') or {}).items()):
        if _BANNED.search(str(v or '')):
            d['facts'][k] = '（已遮蔽）'
    return d


def generate(aid, run_claude, att_override=None, job_slug_override=None):
    """att_override／job_slug_override 只給「唯讀試算」用（例：用別的職缺的人選履歷試跑，不寫任何資料）。"""
    a, job, resume, report = _load(aid, job_slug_override)
    att = att_override or attendance_summary(aid)
    today = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=8)).strftime('%Y-%m-%d')
    prompt = build_prompt(a, job, resume, report, att, today)
    d = _validate(json.loads(run_claude(prompt, want_json=True)))
    hits = _banned_hits(d)
    if hits:
        log(f'{aid[:8]} 回覆含禁用字眼（{"；".join(hits)[:120]}），重產一次')
        d = _validate(json.loads(run_claude(prompt + f'\n\n⚠️ 上一次輸出用到不得使用的字眼：{"；".join(hits)}。請完全不提這些，重新輸出整份 JSON。', want_json=True)))
        if _banned_hits(d):
            d = _mask(d)
    d['attendance_at_generation'] = att
    d['note'] = '初期估計，累積 20～30 位實際結果後再校正'
    d['had_resume'] = bool(resume.strip())
    d['had_report'] = bool(report.strip())
    return d


def run(payload, run_claude):
    aid = str((payload or {}).get('application_id') or '')
    if not aid:
        raise RuntimeError('payload 少 application_id')
    d = generate(aid, run_claude)
    now = now_tpe()
    wid = os.environ.get('STEP1NE_WORKER_NAME') or __import__('socket').gethostname()
    d1_http.query(
        "INSERT INTO special_check_cards (application_id, status, result_json, generated_at, updated_at, error, worker_id) "
        f"VALUES ({q(aid)}, 'ready', {q(json.dumps(d, ensure_ascii=False))}, {q(now)}, {q(now)}, NULL, {q(wid)}) "
        "ON CONFLICT(application_id) DO UPDATE SET status='ready', result_json=excluded.result_json, "
        "generated_at=excluded.generated_at, error=NULL, worker_id=excluded.worker_id")
    s = d['scores']
    return json.dumps({'application_id': aid, 'flags': len(d['resume_flags']),
                       'scores': {k: v['score'] for k, v in s.items()}}, ensure_ascii=False)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['scan', 'run'])
    ap.add_argument('id', nargs='?')
    x = ap.parse_args()
    if x.cmd == 'scan':
        print(scan(force=True))
    else:
        from ai_worker import run_claude
        print(run({'application_id': x.id}, run_claude=run_claude))


if __name__ == '__main__':
    main()
