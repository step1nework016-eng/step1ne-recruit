#!/usr/bin/env python3
"""檢查文字裡「10/9（四）」這種日期＋星期有沒有對上。

2026-10-08 事故：寄給杜偉銘的面試通知寫「10/9（四）前回覆」，10/9 其實是星期五。
是寫信時用心算星期算錯。寄信程式寄出前都要先過這一關。

用法：
    from weekday_check import weekday_errors
    errs = weekday_errors(text)   # [] 代表沒問題
    python3 weekday_check.py "10/9（四）前回覆"
"""
import datetime
import re
import sys

WD = '一二三四五六日'
PAT = re.compile(r'(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})\s*[（(]\s*(?:週|周|星期|禮拜)?\s*([一二三四五六日天])\s*[)）]')


def _guess_date(m, d, today):
    # 沒寫年份：取離今天最近、而且不超過半年前的那一年
    best = None
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            c = datetime.date(y, m, d)
        except ValueError:
            continue
        if (c - today).days < -183:
            continue
        if best is None or abs((c - today).days) < abs((best - today).days):
            best = c
    return best


def weekday_errors(text, today=None):
    today = today or datetime.date.today()
    errs = []
    for mm in PAT.finditer(text or ''):
        m, d, w = int(mm.group(1)), int(mm.group(2)), mm.group(3).replace('天', '日')
        dt = _guess_date(m, d, today)
        if dt is None:
            errs.append(f'「{mm.group(0)}」這個日期不存在')
            continue
        real = WD[dt.weekday()]
        if real != w:
            errs.append(f'「{mm.group(0)}」寫錯了：{dt:%Y/%m/%d} 是星期{real}')
    return errs


if __name__ == '__main__':
    e = weekday_errors(' '.join(sys.argv[1:]) or sys.stdin.read())
    print('\n'.join(e) or '星期都對')
    sys.exit(1 if e else 0)
