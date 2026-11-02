#!/usr/bin/env python3
"""每週一在發財基地新增「M/D本週工作事項」（2026-11-02 從 Mac Claude 排程搬到 WSL2，改走 API，不開 Chrome、不用 Claude）。
鑰匙：~/.config/workflow-os/pm-session.env（這台自己跑一次 pm_login.py 產生；不能跟 Mac 共用，refresh token 用一次就換新）。
規則照原排程：標題「M/D本週工作事項」、截止本週五、優先「中」(MID)、公司「獵人頭公司」、共享；同標題已存在就跳過。
2026-11-02 Mac 實測 POST /api/v1/tasks 成功（priority 只收 HIGH／MID／LOW）。
用法：python3 pm_weekly_task.py [--dry-run]"""
import datetime, os, sys
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pm_calendar as pc

BRANCH_HEADHUNT = '00000000-0000-0000-0000-0000000000a1'   # 獵人頭公司
today = datetime.datetime.now(ZoneInfo('Asia/Taipei')).date()
mon = today - datetime.timedelta(days=today.weekday())
fri = mon + datetime.timedelta(days=4)
title = f'{mon.month}/{mon.day}本週工作事項'
tk = pc.token()
if any((t.get('title') or '').startswith(title) for t in pc._req('GET', '/tasks?limit=500', token=tk) or []):
    print(f'已存在，跳過：{title}'); sys.exit(0)
if '--dry-run' in sys.argv:
    print(f'[dry-run] 會建立：{title}，截止 {fri}'); sys.exit(0)
d = pc._req('POST', '/tasks', {'title': title, 'priority': 'MID', 'dueDate': fri.isoformat(),
                               'branchId': BRANCH_HEADHUNT, 'shared': True}, token=tk)
print(f"建立成功：{d.get('title')}｜截止 {d.get('dueDate')}｜{d.get('branchName')}｜共享 {d.get('shared')}")
