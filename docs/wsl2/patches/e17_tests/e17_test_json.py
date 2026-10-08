"""E17-3：把「改後」報告（含新段落）真的丟進 report_to_json，看 not_covered 有沒有被正確搬進結構化 JSON，
其他欄位（career_directions 等）有沒有受影響，並且顧問版有、客戶版沒有。不寫任何資料（token 記帳與 D1 寫入都攔掉）。"""
import os, sys, json, glob
os.environ.setdefault('INTERVIEW_HOST', 'test')
sys.path.insert(0, os.getcwd())
import interview_daemon as D
import deliver
D.HERE = glob.glob(os.path.expanduser('~/claude-projects/*/step1ne-recruit'))[0]
D.log = lambda *a, **k: None
REAL = D.d1
D.d1 = lambda sql, *a, **k: [] if sql.lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE', 'REPLACE')) else REAL(sql, *a, **k)
D.log_token_usage = lambda *a, **k: None
OUT = glob.glob('/mnt/c/Users/haoli/AppData/Local/Temp/claude/*/ba608703-7b36-4d2a-bfd8-d5af8c2c7d76/scratchpad/e17_out')[0]
ok = True
def check(name, cond, detail=''):
    global ok; ok &= bool(cond); print(('  ✅ ' if cond else '  ❌ ') + name + (f'  ｜{detail}' if detail else ''))
for name in sys.argv[1:]:
    a = D.d1(f"SELECT id, name, job_slug FROM applications WHERE name={D.q(name)} AND interview_state IN ('done','paused') ORDER BY interview_started_at DESC LIMIT 1")[0]
    ctx = D.context_for(a['id'])
    md = open(f'{OUT}/replay_v2/{name}_after.md', encoding='utf-8').read()
    print(f'\n[{name}] 報告 {len(md)} 字 → report_to_json …')
    blob = D.report_to_json(md, ctx, a['name'], app_id=a['id'])
    check('有轉出結構化 JSON', bool(blob))
    if not blob: continue
    data = json.loads(blob)
    nc = data.get('not_covered')
    check('not_covered 是陣列且有內容', isinstance(nc, list) and len(nc) > 0, f'{len(nc or [])} 筆；kind 分布 {sorted({x["kind"] for x in nc or []})}')
    check('每筆都有 item', all(x.get('item') for x in nc or []))
    cd = data.get('career_directions') or []
    check('career_directions 沒有被擠掉', len(cd) >= 1, f'{len(cd)} 筆')
    check('consultant_followups／verdict／job_match 都還在', 'consultant_followups' in data and data.get('verdict') and data.get('job_match'),
          f"verdict={data.get('verdict')}")
    meta = D._delivery_meta(a['id'], a['name'], a['job_slug'], False)
    cons = deliver.build_consultant_html(data, meta)
    check('顧問版 HTML 有新段落', '這次沒問到的地方' in cons and (nc[0]['item'][:6] in cons if nc else False))
    # 洩漏檢查用「差異法」：同一份資料，有 not_covered 與拿掉 not_covered，客戶版 HTML 必須完全相同。
    # （用「條目文字有沒有出現在客戶版」會誤報——職缺必要條件這類字眼本來就會出現在客戶版的硬條件裡）
    data_wo = {k: v for k, v in data.items() if k != 'not_covered'}
    leak = []
    for label, fn in (('舊客戶版', deliver.build_client_html), ('客戶版 v2', deliver.build_client_html_v2)):
        if fn(data, meta) != fn(data_wo, meta): leak.append(label)
    check('兩種客戶版：有沒有 not_covered 輸出完全相同（沒有洩漏）', not leak, leak or '相同')
print('\n全部通過' if ok else '\n有失敗'); sys.exit(0 if ok else 1)
