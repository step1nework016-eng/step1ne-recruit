"""外語人選模式（2026-10-21）單元測試：不連 D1、不碰真人選。
用法：python3 tests/foreign_mode_test.py            （只測新版行為）
      python3 tests/foreign_mode_test.py . <舊版目錄>   （另外逐字比對中文人選 prompt 新舊一致）"""
import importlib.util, sys, json, difflib, datetime
import os
NEW = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = sys.argv[2] if len(sys.argv) > 2 else None
sys.path.insert(0, NEW)

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path + '/interview_daemon.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    m.d1 = lambda sql: []          # 不連資料庫
    m.d1_raw = lambda sql: {'meta': {}}
    return m

new = load(NEW, 'daemon_new')
old = load(OLD, 'daemon_old') if OLD else None
SKILL = '（作業規範全文，測試用佔位）'
JOB = {'slug': 'bim-engineer-houli', 'title': 'BIM 工程師（儲備培訓）', 'company_id': None,
       'required_conditions': '1. 會用 Revit\n2. 可配合后里上班', 'client_screen_conditions': ''}

def ctx(note_extra='', utm=None, conv=None):
    note = '【快速應徵（測試版）】\n' + (note_extra + '\n' if note_extra else '') + '相關經驗：無\n會用的軟體：Revit\n期望工作城市：台中市'
    return {'application': {'id': 'TEST-0000', 'name': '測試人選', 'job_title': JOB['title'], 'note': note,
                            'utm_medium': utm, 'chat_token': None, 'created_at': '2026-10-21 10:00:00'},
            'job': JOB, 'conversation': conv or [], 'resume_readable': False, 'resume_note': '未提供'}

fail = 0
def check(cond, msg):
    global fail
    print(('PASS ' if cond else 'FAIL ') + msg); fail += (not cond)

# 1) 中文人選：新舊 prompt 必須逐字相同（含有/沒有 utm、對話進行中）
for label, c in [('中文・開場', ctx()), ('中文・utm=job_page', ctx(utm='job_page')),
                 ('中文・對話中', ctx(conv=[{'role': 'candidate', 'content': '（候選人已進入面談室）'},
                                          {'role': 'assistant', 'content': '您好'}, {'role': 'candidate', 'content': '你好'}]))]:
    b = new.build_prompt(json.loads(json.dumps(c)), SKILL)
    check('外語人選模式' not in b and '中文能力題' not in b and '工作許可身分' not in b, f'{label} prompt 沒有外語模式內容')
    if old:
        a = old.build_prompt(json.loads(json.dumps(c)), SKILL)
        d = list(difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm=''))
        check(not d, f'{label} prompt 新舊一致（{len(a)} 字）' + ('' if not d else '\n' + '\n'.join(d[:20])))
    check(new._foreign_opening(c, ['中文開場']) == ['中文開場'], f'{label} 開場不加外語歡迎')
    check(new._foreign_report_block(c) == '' and new._foreign_json_block(c) == '', f'{label} 報告規則不變')

# 2) 印尼文／越南文：開場與 prompt
for fl, c in [('id', ctx('應徵頁語言：印尼文（id）')), ('vi', ctx('應徵頁語言：越南文（vi）')),
              ('id', ctx(utm='job_page_id')), ('vi', ctx(utm='job_page_vi'))]:
    check(new.foreign_lang(c['application']) == fl, f'認得 {fl}（note={"應徵頁語言" in c["application"]["note"]}, utm={c["application"]["utm_medium"]}）')
    op = new._foreign_opening(c, ['（阿財的中文開場）'])
    check(len(op) == 2 and op[0] == new.FOREIGN_GREETING[fl], f'{fl} 開場第一則是固定外語歡迎')
    check(new._clean_out(op[0]) == op[0], f'{fl} 歡迎詞經過清洗後不變（繁體/標點轉換不會弄壞外語）')
    check(not new.law5_hits(op[0]), f'{fl} 歡迎詞沒有保護特徵字眼')
    p = new.build_prompt(c, SKILL)
    check('外語人選模式' in p and '工作許可身分（台灣籍配偶' in p and '中文能力題①' in p, f'{fl} prompt 有外語模式段落與必問項')
    conv2 = [{'role': 'assistant', 'content': op[0]}]
    check(new._foreign_opening(dict(c, conversation=conv2), ['x']) == ['x'], f'{fl} 第二輪之後不再送歡迎')

# 3) 報告：中文不足 → 結論固定改硬條件不符
base = {'verdict': '值得轉給顧問', 'one_liner': 'Revit 有基礎', 'top_risk': '', 'job_match': {'items': []}}
for lvl, want in [('不足', '硬條件不符'), ('勉強', '硬條件不符'), ('可日常溝通', '值得轉給顧問'), ('流利', '值得轉給顧問')]:
    o = new._normalize_report_json(dict(base, chinese_ability={'level': lvl, 'evidence': ['「我以前做室內設計」'], 'work_permit': '我是台灣籍配偶'}), 't', JOB)
    ok = o['verdict'] == want and (('中文不足、不建議推薦' in o['one_liner']) == (lvl in ('不足', '勉強')))
    check(ok and any('中文' in i['requirement'] for i in o['job_match']['items']), f'程度「{lvl}」→ 結論「{o["verdict"]}」｜{o["one_liner"]}')
o = new._normalize_report_json(dict(base), 't', JOB)
check('chinese_ability' not in o and o['verdict'] == '值得轉給顧問', '中文人選報告沒有中文能力欄、結論不被改')

print('\n===== 開場範例 =====')
for fl in ('id', 'vi'):
    print(f'[{new.FOREIGN_LANG_NAME[fl]}] {new.FOREIGN_GREETING[fl]}')
print(f'[中文意思] {new.FOREIGN_GREETING_ZH}')
print('\n全部通過' if not fail else f'\n{fail} 項失敗'); sys.exit(1 if fail else 0)
