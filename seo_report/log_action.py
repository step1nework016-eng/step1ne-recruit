#!/usr/bin/env python3
"""週報人工補充（Mac 或 WSL2 都可以用；寫進 repo 檔案，記得 commit＋push，WSL2 週一 git pull 後就會用到）。

  log_action.py done "新文章" "〈試用期不合格可以資遣嗎？〉上線"     → 記一件「這週做了什麼」（actions.jsonl）
       分類建議：新文章／補內容／改標題／信任／收錄／修正
  log_action.py followup 2026-11-20 "某某詞是否進前 10"               → 加一個回收日（followups.json）
  log_action.py followup "等 Jacky" "某件事"                          → 沒有日期的待辦（標籤當日期欄）
  log_action.py close f3                                              → 把回收項目標成做完（用 id 或一段文字）
  log_action.py list                                                  → 列出還沒做完的回收項目
  log_action.py set geo_note "…" ／ set extra_leads "…"               → 覆寫 AI 實測說明／額外商機那一行
  log_action.py geo "「台灣獵頭公司推薦」（ChatGPT）" "✓ 第 5 家" up  → 新一輪 AI 實測結果（同一天第一筆會換掉舊的整組）
"""
import json, os, re, sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ACT, FU, OV = (os.path.join(HERE, f) for f in ('actions.jsonl', 'followups.json', 'overrides.json'))


def _load(p, d):
    try:
        return json.load(open(p, encoding='utf-8'))
    except (OSError, ValueError):
        return d


def _save(p, v):
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(v, f, ensure_ascii=False, indent=2); f.write('\n')


def main(a):
    if not a:
        print(__doc__); return 1
    cmd, today = a[0], date.today().isoformat()
    if cmd == 'done' and len(a) == 3:
        with open(ACT, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'date': today, 'tag': a[1], 'text': a[2]}, ensure_ascii=False) + '\n')
        print('已記：', a[1], a[2])
    elif cmd == 'followup' and len(a) == 3:
        fu = _load(FU, [])
        due = a[1] if re.match(r'\d{4}-\d{2}-\d{2}$', a[1]) else None
        label = f'{int(due[5:7])}/{due[8:10]}' if due else a[1]
        nid = 'f%d' % (max([int(x['id'][1:]) for x in fu if re.match(r'f\d+$', x.get('id', ''))] or [0]) + 1)
        fu.append({'id': nid, 'due': due, 'label': label, 'what': a[2], 'done': False, 'added': today})
        _save(FU, fu); print('已加回收項目', nid, label, a[2])
    elif cmd == 'close' and len(a) == 2:
        fu = _load(FU, []); hit = [x for x in fu if not x.get('done') and (x['id'] == a[1] or a[1] in x['what'])]
        if len(hit) != 1:
            print('找到', len(hit), '筆，要剛好一筆才會關：', [x['id'] + ' ' + x['what'][:20] for x in hit]); return 1
        hit[0]['done'] = True; hit[0]['closed'] = today; _save(FU, fu); print('已完成：', hit[0]['what'])
    elif cmd == 'list':
        for x in _load(FU, []):
            if not x.get('done'):
                print(x['id'], x.get('due') or '-', x['label'], x['what'])
    elif cmd == 'set' and len(a) == 3 and a[1] in ('geo_note', 'extra_leads'):
        ov = _load(OV, {}); ov[a[1]] = {'updated': today, 'text': a[2]}; _save(OV, ov); print('已更新', a[1])
    elif cmd == 'geo' and len(a) == 4 and a[3] in ('up', 'down', 'same'):
        ov = _load(OV, {}); g = ov.get('geo') or {}
        if g.get('updated') != today:
            g = {'updated': today, 'rows': []}
        g['rows'].append([a[1], a[2], a[3]]); ov['geo'] = g; _save(OV, ov); print('已記 AI 實測：', a[1], a[2])
    else:
        print(__doc__); return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
