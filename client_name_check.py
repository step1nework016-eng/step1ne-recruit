"""客戶公司名稱守門員：對外文字裡出現「已知寫錯的客戶名」就擋下。

2026-10-12 事故：美德向邦被寫成「美德相邦」，從 8/25 建檔一路錯到推薦履歷檔名，
被客戶人資糾正。同一天全面查核又抓到「全躍→全曜」「台銀人壽→臺銀人壽」。

資料來源：D1 client_companies
  legal_name / tax_id / name_verified_at / name_verify_source  ← 查核過的正式名稱
  wrong_names                                                  ← 已知錯字，一行一個

用法：
    from client_name_check import client_name_errors, unverified_clients
    errs = client_name_errors(text)   # [] 代表沒問題；有東西就不准寄
    python3 client_name_check.py "要檢查的文字或檔名"
    python3 client_name_check.py --list      # 列出每家客戶查核狀態
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '_tools'))
CACHE = os.path.expanduser('~/.cache/step1ne_client_names.json')

# D1 連不上時的最低保險（一定要擋的已知錯字）
FALLBACK = [
    {'wrong': '美德相邦', 'right': '美德向邦股份有限公司'},
    {'wrong': '全躍財經', 'right': '全曜財經資訊股份有限公司'},
    {'wrong': '台銀人壽保險股份有限公司', 'right': '臺銀人壽保險股份有限公司'},
]


def _rows():
    try:
        if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 600:
            return json.load(open(CACHE))
    except Exception:
        pass
    try:
        import d1_rw
        rows = d1_rw.q("SELECT id, display_name, aliases, legal_name, tax_id, wrong_names "
                       "FROM client_companies")
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(rows, open(CACHE, 'w'), ensure_ascii=False)
        return rows
    except Exception:
        return None


def client_name_errors(text):
    text = text or ''
    rows = _rows()
    pairs = list(FALLBACK)
    for r in rows or []:
        right = r.get('legal_name') or r.get('display_name')
        for w in (r.get('wrong_names') or '').splitlines():
            if w.strip():
                pairs.append({'wrong': w.strip(), 'right': right})
    errs = []
    for p in pairs:
        if p['wrong'] in text:
            errs.append(f"客戶名寫錯：「{p['wrong']}」→ 正確是「{p['right']}」")
    return sorted(set(errs))


def unverified_clients(text):
    """文字裡提到、但還沒查核過正式名稱的客戶（提醒用，不擋）。"""
    out = []
    for r in _rows() or []:
        if r.get('legal_name') or r.get('id') == 'co_step1ne':
            continue
        names = [r.get('display_name') or ''] + (r.get('aliases') or '').splitlines()
        if any(n.strip() and len(n.strip()) >= 2 and n.strip() in (text or '') for n in names):
            out.append(r.get('display_name'))
    return out


if __name__ == '__main__':
    if sys.argv[1:] == ['--list']:
        for r in sorted(_rows() or [], key=lambda x: (x.get('legal_name') is None, x.get('display_name') or '')):
            mark = '✅' if r.get('legal_name') else '⚠️ 未查核'
            print(f"{mark}  {r.get('display_name')}  ｜正式名稱：{r.get('legal_name') or '-'}  統編：{r.get('tax_id') or '-'}")
        sys.exit(0)
    t = ' '.join(sys.argv[1:]) or sys.stdin.read()
    e = client_name_errors(t)
    u = unverified_clients(t)
    for x in e:
        print('⛔', x)
    for x in u:
        print('⚠️ 這家客戶的正式名稱還沒查核：', x)
    sys.exit(1 if e else 0)
