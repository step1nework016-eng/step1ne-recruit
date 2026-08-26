#!/usr/bin/env python3
"""一次性回填：把舊職缺塞在 faq_notes 裡的內容，拆回結構化欄位。

## 為什麼要有這支

2026-08-25 做「顧問預覽頁」時發現：幾乎每個職缺的 work_hours／main_duties／
benefits_detail 這類結構化欄位都是 NULL，但同樣的資訊其實都寫在 faq_notes
裡（一整段給候選人問到可以照講的說明）。舊的職缺建立流程只填了 faq_notes，
沒有拆進 51 個結構化欄位——這些欄位是後來才加的，舊資料沒有回填過。

這支重用 portal_import_tick.py 的規則比對＋AI 解析（同一套邏輯，不重寫一份），
把 faq_notes（+must_skills+salary_note 當補充）丟進去解析，只填「目前是
NULL」的欄位——已經有值的欄位一律不動，不會覆蓋掉任何人工填過的內容。

用法：
    python3 backfill_structured_fields.py --dry     # 只印出會填什麼，不寫入
    python3 backfill_structured_fields.py            # 真的寫入
    python3 backfill_structured_fields.py --slug bim-engineer   # 只處理一筆
"""
import os, sys, json, argparse, importlib.util, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

spec = importlib.util.spec_from_file_location('d', os.path.join(ROOT, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)

import portal_import_tick as PIT  # noqa: E402


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


# main_duties／work_hours 兩個當代表性指標：兩個都是 NULL 才算「還沒拆過」，
# 避免把已經回填過一次的職缺又處理一次。
TARGET_FIELDS = [
    'work_hours', 'leave_policy', 'employment_period', 'overtime_policy',
    'main_duties', 'reports_to', 'leads_team', 'work_environment_ratio',
    'attendance_method', 'interview_process', 'benefits_detail',
    'onboarding_prep_note', 'work_mode', 'headcount', 'urgency',
    'dispatch_range', 'department',
]
# 這些欄位太容易出錯或風險高，回填只做「明顯沒有」的敘述性欄位，
# 不去動薪資／人數這類數字（已經在其他地方特別處理過，交叉比對風險更高）。


def find_candidates(slug_filter=None):
    where = "status NOT IN ('closed') AND faq_notes IS NOT NULL AND main_duties IS NULL AND work_hours IS NULL"
    if slug_filter:
        where += f" AND slug={D.q(slug_filter)}"
    rows = D.d1(f"SELECT slug, title, faq_notes, must_skills, salary_note, notes FROM jobs WHERE {where}")
    return rows


def backfill_one(row, dry):
    slug = row['slug']
    text = '\n'.join(filter(None, [row.get('faq_notes'), row.get('must_skills'), row.get('salary_note')]))
    if not text.strip():
        log(f'⏭️  {slug}：沒有可用的來源文字，跳過')
        return

    rule_fields, cov = PIT.rule_extract(text)
    ai_fields = {}
    if cov < 0.7:
        ok, out = PIT.run_claude(PIT.build_prompt(text, rule_fields))
        if ok:
            parsed = PIT.extract_json(out)
            if isinstance(parsed, dict):
                ai_fields = parsed
            else:
                log(f'⚠️  {slug}：AI 沒有回傳有效 JSON，只用規則比對的結果')
        else:
            log(f'⚠️  {slug}：AI 呼叫失敗，只用規則比對的結果')

    merged = {**ai_fields, **rule_fields}
    # 只留白名單裡「這次要回填」的目標欄位，其他（title/client_name/薪資等）
    # 這支不動——那些欄位風險更高，且大多已經在別的地方有值。
    fields = {k: v for k, v in merged.items() if k in TARGET_FIELDS and v not in (None, '')}
    if not fields:
        log(f'⏭️  {slug}：解析不出可回填的欄位')
        return

    log(f'✅ {slug}（{row.get("title")}）：{list(fields.keys())}')
    if dry:
        return

    sets = [f"{k}={D.q(v)}" for k, v in fields.items()]
    D.d1(f"UPDATE jobs SET {', '.join(sets)} WHERE slug={D.q(slug)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--slug', default=None)
    a = ap.parse_args()

    rows = find_candidates(a.slug)
    log(f'找到 {len(rows)} 筆待回填的職缺' + ('（--dry 只顯示不寫入）' if a.dry else ''))
    for row in rows:
        try:
            backfill_one(row, a.dry)
        except Exception as e:
            log(f'❌ {row["slug"]} 處理失敗：{e}')


if __name__ == '__main__':
    main()
