#!/usr/bin/env python3
"""sec_update.py — 資安紅藍隊專用：只能改 sec_findings 的狀態，並順手記一筆 sec_events。

（2026-10-22 Mac 補上；E18 回報說 d1q.py 只能新增、不能改狀態，藍隊修好的東西記不回去）

  python3 sec_update.py status <finding_id> <open|fixing|verified|wontfix> "說明（修了什麼／怎麼驗）" [--team red|blue]
  python3 sec_update.py list                       # 列出還沒結案的 finding

規則（寫死在程式裡，不靠提示詞自律）：
- 只能 UPDATE sec_findings 的 status／fix_note／fixed_at／verified_at／updated_at，其他欄位、其他表一律不能改。
- 只能 INSERT sec_events 一筆（team／kind／finding_id／text）。
- 改成 verified 必須是紅隊（--team red）：藍隊修、紅隊複測，同一隊不能自己驗收自己。
- 說明不能空白，最多 500 字。
"""
import datetime
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'nightly_pipeline'))
from d1q import _post  # noqa: E402

STATUSES = ('open', 'fixing', 'verified', 'wontfix')
KIND = {'fixing': 'fixed', 'verified': 'verified', 'wontfix': 'wontfix', 'open': 'reopened'}


def now():
    return (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')


def cmd_list():
    r = _post("SELECT id, severity, status, title FROM sec_findings WHERE status IN ('open','fixing') ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'unknown' THEN 2 ELSE 3 END, id")
    print(json.dumps(r.get('results') or [], ensure_ascii=False, indent=1))


def cmd_status(fid, status, note, team):
    if status not in STATUSES:
        raise SystemExit(f'❌ 狀態只能是 {STATUSES}')
    note = (note or '').strip()
    if not note:
        raise SystemExit('❌ 要寫說明（修了什麼／怎麼驗的）')
    if len(note) > 500:
        raise SystemExit('❌ 說明最多 500 字')
    if team not in ('red', 'blue'):
        raise SystemExit('❌ --team 只能是 red 或 blue')
    if status == 'verified' and team != 'red':
        raise SystemExit('❌ 驗收（verified）要由紅隊複測後標，藍隊不能自己驗收')
    cur = (_post('SELECT id, status FROM sec_findings WHERE id=?', [fid]).get('results') or [])
    if not cur:
        raise SystemExit(f'❌ 找不到 finding {fid}')
    t = now()
    sets = ['status=?', 'fix_note=?', 'updated_at=?']
    vals = [status, note, t]
    if status == 'fixing':
        sets.append('fixed_at=?'); vals.append(t)
    if status == 'verified':
        sets.append('verified_at=?'); vals.append(t)
    _post(f"UPDATE sec_findings SET {', '.join(sets)} WHERE id=?", vals + [fid])
    _post('INSERT INTO sec_events (id, team, kind, finding_id, text, created_at) VALUES (?,?,?,?,?,?)',
          [str(uuid.uuid4()), team, KIND[status], fid, note, t])
    print(f"✅ {fid}：{cur[0]['status']} → {status}")


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['list']:
        cmd_list()
    elif a[:1] == ['status'] and len(a) >= 4:
        team = 'blue'
        if '--team' in a:
            i = a.index('--team'); team = a[i + 1] if i + 1 < len(a) else ''; a = a[:i] + a[i + 2:]
        cmd_status(a[1], a[2], a[3], team)
    else:
        print(__doc__)
        sys.exit(1)
