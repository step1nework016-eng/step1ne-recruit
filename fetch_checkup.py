#!/usr/bin/env python3
"""一行拿到一筆履歷健檢的全部資料——阿福開口前讀的就是這支。

跟阿財的 fetch_application.py 是同一個角色，但讀的是 checkups 系列的表，
**完全不碰 applications**。

用法：
    python3 fetch_checkup.py <checkup_id>
    python3 fetch_checkup.py --latest        # 最新一筆（開發與驗證時用）
    python3 fetch_checkup.py <id> --text     # 只印履歷全文

輸出 JSON：
    checkup   基本欄位 + 三個關鍵數字（led_headcount / budget_scale /
              crowdfunding_raised，NULL 代表「還沒問到」——不准自己填）
    resume_text        履歷全文（由 checkup_parse.py 事先抽好）
    resume_readable    false 時**絕對不准說「您的履歷我看過了」**
    files / links / messages
"""
import importlib.util, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('pr', os.path.join(HERE, 'parse_resumes.py'))
PR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PR)
d1 = PR.d1


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--latest' in sys.argv:
        rows = d1('SELECT id FROM checkups ORDER BY created_at DESC LIMIT 1')
        if not rows:
            print('沒有任何健檢單', file=sys.stderr); sys.exit(1)
        cid = rows[0]['id']
    elif args:
        cid = args[0]
    else:
        print(__doc__); sys.exit(1)

    e = cid.replace("'", "''")
    rows = d1(f"SELECT * FROM checkups WHERE id='{e}'")
    if not rows:
        print(f'找不到健檢單 {cid}', file=sys.stderr); sys.exit(1)
    c = rows[0]

    if '--text' in sys.argv:
        print(c.get('resume_text') or '')
        return

    out = {
        'checkup': c,
        'resume_text': c.get('resume_text'),
        'resume_readable': bool(c.get('resume_readable')),
        'files': d1("SELECT cf.file_id, cf.kind, f.filename, f.mime, f.size FROM checkup_files cf "
                    f"LEFT JOIN files f ON f.id=cf.file_id WHERE cf.checkup_id='{e}'"),
        'links': d1(f"SELECT idx, url, label, fetched_at, fetch_note FROM checkup_links "
                    f"WHERE checkup_id='{e}' ORDER BY idx"),
        'messages': d1(f"SELECT role, content, created_at FROM checkup_messages "
                       f"WHERE checkup_id='{e}' ORDER BY created_at"),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
