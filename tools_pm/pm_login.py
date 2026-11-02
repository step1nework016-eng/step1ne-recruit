#!/usr/bin/env python3
"""發財基地（pm.aijob.com.tw）一次性登入：Jacky 自己在終端機跑，輸入帳號密碼。
只把「換新用的鑰匙」（refreshToken）存到 ~/.config/workflow-os/pm-session.env（chmod 600），
密碼不存、不印、不經過 Claude。之後 pm_calendar.py 會自己用這把鑰匙換新。"""
import getpass, json, os, urllib.request
ENV = os.path.expanduser('~/.config/workflow-os/pm-session.env')
name = input('發財基地帳號（名稱）：').strip()
pw = getpass.getpass('密碼（不會顯示）：')
r = urllib.request.Request('https://pm.aijob.com.tw/api/v1/auth/login', method='POST',
    data=json.dumps({'name': name, 'password': pw}).encode(), headers={'Content-Type': 'application/json', 'User-Agent': 'step1ne-ops'})
try:
    d = json.load(urllib.request.urlopen(r, timeout=20))
except urllib.error.HTTPError as e:
    raise SystemExit(f'登入失敗（{e.code}）：帳號或密碼不對')
rt = d.get('refreshToken')
if not rt:
    raise SystemExit('登入成功但沒拿到換新鑰匙，請跟 Claude 說')
fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write(f'PM_REFRESH_TOKEN={rt}\n')
print('✅ 已存好，之後行事曆可以自動登記了。')
