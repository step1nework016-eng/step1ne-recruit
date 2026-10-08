"""E17-3 測試（不呼叫 claude）：
 1. 正規化白名單：not_covered 會被保留、壞資料會被清掉
 2. 顧問版 HTML 有這一段；客戶版（舊版與 v2）一個字都沒有
 3. 用真實逐字稿＋真實計畫組「改前／改後」的報告 prompt，證明：改前＝現行 prompt（逐字不變），改後只多出該有的段落
唯讀：D1 只做 SELECT。"""
import os, sys, json, re
os.environ.setdefault('INTERVIEW_HOST', 'test')
sys.path.insert(0, os.getcwd())
import glob
import interview_daemon as D
import deliver
# worktree 沒有正式環境那些未追蹤的設定檔（wrangler 憑證等），抓人選靜態資料的子程序要在正式 repo 目錄跑（唯讀）
D.HERE = glob.glob(os.path.expanduser('~/claude-projects/*/step1ne-recruit'))[0]

ok_all = True
def check(name, cond, detail=''):
    global ok_all
    ok_all &= bool(cond)
    print(('  ✅ ' if cond else '  ❌ ') + name + (f'  ｜{detail}' if detail else ''))

MARK = 'E17測試標記_不可出現在客戶版'
print('[1] 正規化白名單')
raw = json.loads(json.dumps({
    'not_covered': [
        {'kind': '沒問到', 'item': '英文口說驗證', 'note': MARK},
        {'kind': '含糊沒追到', 'item': '跨國團隊規模', 'note': '他說「很多人」，缺人數'},
        {'kind': '亂寫的類型', 'item': '離職預告期', 'note': ''},
        {'kind': '沒問到', 'item': '', 'note': '沒有 item 的要丟掉'},
        'not a dict',
    ]}))
out = D._normalize_report_json(raw, 'T', {})
nc = out.get('not_covered')
check('保留 3 筆有 item 的、丟掉空的與非物件', isinstance(nc, list) and len(nc) == 3, nc)
check('不認得的 kind 被歸到「沒問到」', nc[2]['kind'] == '沒問到')
check('沒有 not_covered 時給空陣列（不會 KeyError）', D._normalize_report_json({}, 'T', {}).get('not_covered') == [])
check('JSON 規格與規則都有寫到', 'not_covered' in D.REPORT_JSON_SPEC and '19. `not_covered`' in D.REPORT_JSON_RULES)

print('\n[2] 顧問版有、客戶版沒有')
rows = D.d1("SELECT a.id, a.name, a.job_slug, r.content_json FROM applications a JOIN reports r ON r.application_id=a.id "
            "WHERE r.content_json IS NOT NULL AND a.name='楊政翰' ORDER BY r.created_at DESC LIMIT 1")
r0 = rows[0]
data = json.loads(r0['content_json'])
meta = D._delivery_meta(r0['id'], r0['name'], r0['job_slug'], False)
data['not_covered'] = nc
cons = deliver.build_consultant_html(data, meta)
check('顧問版 HTML 有「這次沒問到的地方」且三條都在', '這次沒問到的地方' in cons and '英文口說驗證' in cons and '跨國團隊規模' in cons and MARK in cons)
data_empty = dict(data); data_empty['not_covered'] = []
check('空陣列時整段不顯示', '這次沒問到的地方' not in deliver.build_consultant_html(data_empty, meta))
for label, fn in (('客戶版（舊）', deliver.build_client_html), ('客戶版 v2', deliver.build_client_html_v2)):
    try:
        html = fn(data, meta)
        leaked = [k for k in (MARK, '英文口說驗證', '跨國團隊規模', '這次沒問到', 'not_covered') if k in html]
        check(f'{label}：沒有任何洩漏', not leaked, leaked or f'{len(html)} 字元')
    except Exception as e:
        check(f'{label}：可組版', False, f'{type(e).__name__}: {str(e)[:120]}')

print('\n[2b] 差異法：對真實 content_json 加上／拿掉 not_covered，兩種客戶版輸出必須逐字相同')
real = D.d1("SELECT a.id, a.name, a.job_slug, r.content_json FROM applications a JOIN reports r ON r.application_id=a.id "
            "WHERE r.content_json IS NOT NULL AND a.name IN ('楊政翰','張景勛','葉筱屏','林博祥','江逸泓','周亦宣','王美日') "
            "GROUP BY a.name ORDER BY r.created_at DESC")
for r in real:
    dj = json.loads(r['content_json']); mt = D._delivery_meta(r['id'], r['name'], r['job_slug'], False)
    d_with = dict(dj, not_covered=nc); d_wo = {k: v for k, v in dj.items() if k != 'not_covered'}
    same = all(fn(d_with, mt) == fn(d_wo, mt) for fn in (deliver.build_client_html, deliver.build_client_html_v2))
    check(f"{r['name']}：客戶版（舊＋v2）有無 not_covered 輸出逐字相同", same)

print('\n[3] 用真實面談組報告 prompt：改前＝現行，改後只多出該有的')
apps = D.d1("SELECT a.id, a.name, a.job_slug FROM applications a WHERE a.name IN ('楊政翰','張景勛','葉筱屏','林博祥','江逸泓','沈蓓芬','陳南宏') "
            "AND a.interview_state IN ('done','paused') ORDER BY a.interview_started_at DESC")
seen = set(); picks = []
for a in apps:
    if a['name'] in seen: continue
    seen.add(a['name']); picks.append(a)
print('  取樣面談：', [p['name'] for p in picks])
for a in picks:
    ctx = D.context_for(a['id'])
    before = D.build_report_prompt(a['id'], ctx, a['job_slug'], False, with_not_covered=False)
    after = D.build_report_prompt(a['id'], ctx, a['job_slug'], False, with_not_covered=True)
    has_plan = bool((ctx.get('plan') or {}).get('questions'))
    added = after.replace(before, '', 1) if after.startswith(before[:200]) else None
    # 「改後」應該等於「改前」在最後那句輸出指示之前插入一段
    tail = '\n\n只輸出報告本文（Markdown），不要有其他說明。'
    ok = before.endswith(tail) and after.endswith(tail) and after[:-len(tail)].startswith(before[:-len(tail)])
    insert = after[len(before) - len(tail): len(after) - len(tail)]
    check(f"{a['name']}：改後 = 改前 + 一段插入（{'有' if has_plan else '無'}題目計畫，插入 {len(insert)} 字）", ok and 'NOT_COVERED' not in insert and '這次沒問到的地方' in insert,
          '含計畫題目' if has_plan and '【這場事先擬好的題目計畫' in insert else ('用職缺必要條件' if not has_plan else '??'))
    # 逐字稿：不含過渡語（P1）且內容與現行一致
    check(f"{a['name']}：逐字稿區塊在改前改後完全相同", before.split('【逐字稿】')[1].split('\n\n')[0] == after.split('【逐字稿】')[1].split('\n\n')[0])

print('\n全部通過' if ok_all else '\n有失敗')
sys.exit(0 if ok_all else 1)
