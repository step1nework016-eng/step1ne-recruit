"""E23：量一場面談「送出應徵 → 預熱完成 → 進房 → 第一句」的秒數。唯讀。
用法：python3 e23_measure.py <application_id 前綴或姓名>
輸出：每個時間點與相鄰秒數；進房→第一句 ≤ 3 秒才算「秒開場」。"""
import datetime, json, os, subprocess, sys
pipe = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'nightly_pipeline')
def q(sql):
    return json.loads(subprocess.run(['python3', os.path.join(pipe, 'd1q.py'), 'q', sql], capture_output=True, text=True).stdout)
key = sys.argv[1].replace("'", "")
a = q(f"SELECT id, name, status, created_at, ready_notified_at, prewarmed_at, interview_started_at, interview_host_at, interview_plan_at "
      f"FROM applications WHERE id LIKE '{key}%' OR name LIKE '%{key}%' ORDER BY created_at DESC LIMIT 1")
if not a:
    sys.exit('找不到')
a = a[0]
ass = q(f"SELECT MIN(created_at) AS t FROM assessments WHERE application_id='{a['id']}'")[0]['t']
first = q(f"SELECT MIN(created_at) AS t FROM messages WHERE application_id='{a['id']}' AND role='assistant'")[0]['t']
P = lambda s: datetime.datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S') if s else None
pts = [('送出應徵（/apply 建立）', a['created_at']), ('交卷', ass), ('status 變 ready', a['ready_notified_at']),
       ('預熱開場白完成', a['prewarmed_at']), ('進房', a['interview_started_at']), ('daemon 開始處理', a['interview_host_at']),
       ('第一句出現', first), ('題目計畫完成', a['interview_plan_at'])]
print(a['name'], a['id'][:8], '狀態', a['status'])
t0 = P(a['created_at'])
for n, t in pts:
    print('  %-22s %s  (+%s 秒)' % (n, t or '—', int((P(t) - t0).total_seconds()) if t else '—'))
def d(x, y):
    return int((P(y) - P(x)).total_seconds()) if x and y else None
print('預熱完成 比 進房 早幾秒：', d(a['prewarmed_at'], a['interview_started_at']), '（正數＝來得及）')
print('進房 → 第一句：', d(a['interview_started_at'], first), '秒（目標 ≤ 3）')
print('預熱是否在交卷前完成：', (P(a['prewarmed_at']) < P(ass)) if a['prewarmed_at'] and ass else '—')
