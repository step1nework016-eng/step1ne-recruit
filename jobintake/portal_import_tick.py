#!/usr/bin/env python3
"""一鍵匯入：把企業客戶上傳的檔案／連結／文字解析成用人需求表的 51 個欄位。

## 為什麼要有這支

Worker（Cloudflare）沒有能力可靠解析任意格式的 JD 文件——2026-07-30 已經在
履歷解析踩過這個坑：瀏覽器端用 pdf.js 抽中文 PDF，抽出來夾了 1756 個空
位元組、內容殘缺，本機 pdftotext 抽同一份卻是乾淨的。所以跟 job_intake
（顧問自助新增職缺）同一套哲學：Worker 只負責收件跟存檔，真正的解析
交給本機排程呼叫 Claude Code CLI 做。

## 流程

    企業客戶在 portal 頁「一鍵匯入」→ Worker 存進 portal_imports（status=pending）
    → ★ 這一支 ★ 每分鐘掃一次，抽文字＋交給 Claude 對應成 51 個欄位
      → 寫回 parsed_json（status=parsed）
    → 客戶在 portal 頁看到反白的「匯入建議」，確認/改過後按「套用」
      → Worker 的 /portal/:token/imports/:id/apply 才真的寫進 jobs 表

⚠️ 這一支不會自動把資料寫進 jobs 表——那一步要客戶自己按確認，
   避免匯錯欄位沒人發現就直接進了審核。

用法：
    python3 portal_import_tick.py           # 處理所有 pending 的匯入（單次）
    python3 portal_import_tick.py --loop     # 常駐輪詢（launchd 用 tick 模式即可，這個是備用）
"""
import os, sys, json, uuid, subprocess, tempfile, base64, time, re, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOCK = '/tmp/step1ne-portal-import.lock'
MODEL = 'claude-opus-5'
TIMEOUT_SEC = 240

sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import importlib.util

_spec = importlib.util.spec_from_file_location('d', os.path.join(ROOT, 'interview_daemon.py'))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

import parse_resumes as PR  # noqa: E402  extract()/guard_url()/http_get()/html_to_text()/direct_download_url()


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


# 跟 portal/index.html 的 LABELS 常數同一份清單（欄位新增/改名要兩邊一起改，
# 不然匯入解析跟前端顯示會對不起來）。
FIELD_LABELS = {
    'client_name': '公司名稱', 'client_screen_conditions': '用人條件補充說明（內部，不對外）',
    'faq_notes': '常見問題／FAQ說明', 'salary_note': '薪資補充說明',
    'title': '職缺名稱', 'client_intro': '公司簡介', 'hiring_manager': '用人主管',
    'years_min': '最低年資要求（純數字，年）', 'must_skills': '必要技能',
    'salary_min': '薪資下限（純數字）', 'salary_max': '薪資上限（純數字）', 'salary_unit': '薪資單位（MONTH/YEAR）',
    'locations': '工作地點', 'employment': '聘僱型態', 'onboard_by': '希望到職日',
    'team_size': '團隊人數與組成', 'interview_rounds': '面試次數', 'interview_who': '面試官',
    'has_test': '是否有測驗',
    'client_contact_name': '聯絡窗口', 'client_contact_phone': '聯絡電話',
    'headcount': '需求人數', 'work_mode': '辦公型態',
    'work_hours': '上班時間', 'leave_policy': '休假方式', 'employment_period': '工作期間',
    'overtime_policy': '加班情形',
    'hiring_reason': '招募原因', 'urgency': '急迫程度',
    'main_duties': '主要工作職責', 'reports_to': '直接匯報對象', 'leads_team': '是否帶團隊',
    'education_level': '學歷要求', 'required_conditions': '必備條件', 'language_requirement': '語言能力',
    'nice_to_have_skills': '加分技能／經驗', 'preferred_background': '偏好背景',
    'personality_traits': '期望人格特質',
    'benefits_detail': '福利與津貼', 'dispatch_to_permanent_policy': '派遣／轉正條件',
    'off_limits_note': '同業限制',
    'dispatch_client': '實際要派單位／進駐專案', 'department': '所屬部門／團隊',
    'work_environment_ratio': '工作環境現場比重', 'attendance_method': '出勤紀錄方式',
    'interview_process': '面試流程與關卡', 'dispatch_range': '專案調派範圍',
    'contract_terms_note': '保密／競業／最低服務年限',
    'onboarding_prep_note': '錄取後前置作業',
}
# salary_tier_table／overtime_detail 是給進階使用者的 JSON 欄位，AI 匯入不猜這兩個——
# 猜錯格式不是合法 JSON，反而讓客戶看到的表單壞掉，寧可留給客戶自己手動填。


def load_pending():
    return D.d1("SELECT id, job_slug, company_id, source_type, source_ref FROM portal_imports "
                "WHERE status='pending' ORDER BY created_at LIMIT 5")


def mark(import_id, status, parsed_json=None, error_note=None):
    # 本機系統時區已經是台北時間，直接用 now() 就好，不用另外算 +8。
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sets = [f"status={D.q(status)}", f"updated_at={D.q(now)}"]
    if parsed_json is not None:
        sets.append(f"parsed_json={D.q(json.dumps(parsed_json, ensure_ascii=False))}")
    if error_note is not None:
        sets.append(f"error_note={D.q(error_note[:500])}")
    D.d1(f"UPDATE portal_imports SET {', '.join(sets)} WHERE id={D.q(import_id)}")


def gather_text_from_files(import_id):
    """把這筆匯入關聯的所有檔案抓回本機、逐個抽文字、串起來。"""
    files = D.d1(f"""SELECT f.id, f.filename, f.mime, f.chunks FROM portal_import_files pf
                       JOIN files f ON f.id = pf.file_id WHERE pf.import_id={D.q(import_id)}""")
    if not files:
        return None, '這筆匯入沒有關聯到任何檔案'
    parts = []
    for f in files:
        rows = D.d1(f"SELECT b64 FROM file_chunks WHERE file_id={D.q(f['id'])} ORDER BY idx ASC")
        b64 = ''.join(r['b64'] for r in rows)
        if not b64:
            continue
        raw = base64.b64decode(b64)
        text, note = PR.extract(raw, f.get('filename') or '', f.get('mime') or '')
        if text:
            parts.append(f"【檔案：{f.get('filename')}】\n{text}")
        else:
            log(f'⚠️ {f.get("filename")} 抽不到文字：{note}')
    if not parts:
        return None, '所有檔案都抽不到文字內容（可能是掃描影像或格式不支援）'
    return '\n\n'.join(parts)[:60000], None


def gather_text_from_url(url):
    """雲端連結／網頁連結 → 文字。Google Sheets 額外處理（parse_resumes 原本只認 Docs／Drive 檔案）。"""
    m = re.search(r'/spreadsheets/d/([\w-]{20,})', url)
    if m:
        export_url = f'https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=xlsx'
        err = PR.guard_url(export_url)
        if err:
            return None, err
        try:
            raw, _ct = PR.http_get(export_url)
        except Exception as e:
            return None, f'Google Sheet 下載失敗：{str(e)[:150]}（確認分享設定是否為「知道連結的人可檢視」）'
        text, note = PR.extract(raw, 'sheet.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        return (text, None) if text else (None, note or 'Google Sheet 抽不到內容')

    dl_url, dl_err = PR.direct_download_url(url)
    if dl_err:
        return None, dl_err
    if dl_url:
        err = PR.guard_url(dl_url)
        if err:
            return None, err
        try:
            raw, ct = PR.http_get(dl_url)
        except Exception as e:
            return None, f'下載失敗：{str(e)[:150]}'
        ext_guess = '.pdf' if 'pdf' in ct else ''
        text, note = PR.extract(raw, 'download' + ext_guess, ct)
        return (text, None) if text else (None, note or '下載的檔案抽不到文字內容')

    # 不是已知的檔案主機——當一般網頁抓，剝標籤取文字（跟 /assessment/fetch-url 同精神：
    # 抓不到就明說，不編內容）。
    err = PR.guard_url(url)
    if err:
        return None, err
    try:
        raw, ct = PR.http_get(url)
    except Exception as e:
        return None, f'網頁抓取失敗：{str(e)[:150]}'
    if 'text/html' not in ct and 'text/plain' not in ct:
        return None, f'這個連結不是網頁內容（{ct}）'
    text = PR.html_to_text(raw.decode('utf-8', 'replace'))
    if len(text) < 200:
        return None, '這個頁面內容太少（可能是前端動態載入），請改用「貼上文字」或上傳檔案'
    return text[:60000], None


def build_prompt(raw_text):
    field_lines = '\n'.join(f'- {k}：{v}' for k, v in FIELD_LABELS.items())
    return f'''你是在幫台灣一家獵頭公司（Step1ne）把企業客戶提供的職缺需求文件，
對應進一份固定格式的「用人需求表」。以下是原始文件內容（可能是 JD、Email、Excel 表格、
或客戶隨手打的文字，格式不一定工整）：

---
{raw_text}
---

請把上面的內容對應到下面這些欄位。只輸出你有把握、原文有明確依據的欄位，
原文沒提到的欄位**不要輸出、不要瞎猜、不要用「不拘」「面議」這類詞硬填**——
留白比錯誤的資訊更安全，顧問跟客戶會自己補。

欄位清單（key：說明）：
{field_lines}

⚠️ 硬性規則：
- salary_min／salary_max／years_min：只能是純數字（不含「萬」「元」「起」等字），
  「月薪4萬起」要拆成 salary_min=40000。看不出精確數字就不要輸出這個欄位。
- 不要輸出年齡、性別、婚姻、國籍、身心障礙、宗教等就業服務法第5條禁止的條件，
  就算原文有寫，也不要放進任何欄位——那種內容只能記在顧問內部系統，不能出現在
  這份會給企業客戶自己看到的結構化欄位裡。
- 只輸出一個 JSON 物件，不要有任何說明文字、不要用 ```json 包起來。
'''


def run_claude(prompt):
    env = dict(os.environ)
    env.pop('CLAUDECODE', None)
    env.pop('CLAUDE_CODE_ENTRYPOINT', None)
    cmd = ['claude', '-p', '--model', MODEL, '--output-format', 'text',
           '--permission-mode', 'bypassPermissions',
           '--setting-sources', '',
           '--session-id', str(uuid.uuid4())]
    r = subprocess.run(cmd + [prompt], capture_output=True, text=True,
                       timeout=TIMEOUT_SEC, env=env)
    return r.returncode == 0, (r.stdout or r.stderr or '')


def extract_json(text):
    s = (text or '').strip()
    if s.startswith('```'):
        s = s.split('\n', 1)[-1]
        if s.rstrip().endswith('```'):
            s = s.rstrip()[:-3]
    i, j = s.find('{'), s.rfind('}')
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(s[i:j + 1])
    except Exception:
        return None


def process_one(row):
    import_id = row['id']
    log(f'處理匯入 {import_id}（{row["source_type"]}）…')
    D.d1(f"UPDATE portal_imports SET status='parsing' WHERE id={D.q(import_id)}")

    if row['source_type'] == 'file':
        text, err = gather_text_from_files(import_id)
    elif row['source_type'] == 'url':
        text, err = gather_text_from_url(row['source_ref'])
    else:  # text
        text, err = (row['source_ref'] or '').strip() or None, None
        if not text:
            err = '沒有文字內容'

    if not text:
        log(f'❌ {import_id} 抽不到內容：{err}')
        mark(import_id, 'failed', error_note=err or '抽不到內容')
        return

    ok, out = run_claude(build_prompt(text))
    if not ok:
        log(f'❌ {import_id} Claude 呼叫失敗：{out[:200]}')
        mark(import_id, 'failed', error_note='AI 解析失敗，請改手動填寫或重新匯入')
        return

    fields = extract_json(out)
    if not isinstance(fields, dict):
        log(f'❌ {import_id} 解析不出 JSON：{out[:200]}')
        mark(import_id, 'failed', error_note='AI 沒有回傳有效格式，請改手動填寫或重新匯入')
        return

    # 只留白名單內、且值非空的欄位——就算 AI 不聽話多吐了不該有的欄位也擋住。
    fields = {k: v for k, v in fields.items() if k in FIELD_LABELS and v not in (None, '')}
    if not fields:
        mark(import_id, 'failed', error_note='文件裡沒有找到看得懂的職缺資訊，請改手動填寫')
        return

    log(f'✅ {import_id} 解析出 {len(fields)} 個欄位')
    mark(import_id, 'parsed', parsed_json=fields)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--loop', action='store_true')
    a = ap.parse_args()

    if os.path.exists(LOCK):
        try:
            pid = int(open(LOCK).read().strip())
            os.kill(pid, 0)
            log('已有一份在跑，跳過這次')
            return
        except Exception:
            pass
    with open(LOCK, 'w') as f:
        f.write(str(os.getpid()))
    try:
        while True:
            rows = load_pending()
            for row in rows:
                try:
                    process_one(row)
                except Exception as e:
                    log(f'❌ {row["id"]} 處理時噴例外：{e}')
                    try:
                        mark(row['id'], 'failed', error_note=f'處理時發生錯誤：{str(e)[:200]}')
                    except Exception:
                        pass
            if not a.loop:
                break
            time.sleep(60)
    finally:
        try:
            os.unlink(LOCK)
        except Exception:
            pass


if __name__ == '__main__':
    main()
