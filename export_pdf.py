#!/usr/bin/env python3
"""把一場面談的初篩報告＋逐字稿印成 PDF。

用途：給老闆或客戶看實際運作狀況。後台可以下載 Markdown，
但要拿給人看的時候 PDF 比較不會有「格式在對方電腦跑掉」的問題。

⚠️ 逐字稿裡會出現委任客戶的名稱（阿財依規範可以對候選人揭露）。
   這份 PDF 是內部用的，**不要直接轉給第三方**。

用法：
    python3 export_pdf.py 張博州
    python3 export_pdf.py <application_id> --out ~/Desktop/x.pdf
"""
import os, sys, json, argparse, importlib.util, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('d', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
F = 'STSong-Light'
W, H = A4
L, R = 20 * mm, W - 20 * mm
GOLD = (0.65, 0.49, 0.24)
INK = (0.14, 0.15, 0.17)
GREY = (0.45, 0.47, 0.5)


# 中文 CID 字型沒有 emoji 字符，直接畫出來是空白——
# 而硬條件那一段整段靠 ✅⚠️❌ 標示，變空白等於資訊全失。
# 換成文字標記，列印與轉寄都不會掉。
GLYPH = {
    '✅': '［符合］', '⚠️': '［注意］', '⚠': '［注意］', '❌': '［不符］',
    '❓': '［未確認］', '✕': '［無］', '🎯': '［重點］', '🆘': '［回報］',
    '→': '→',
    '·': '｜',   # U+00B7 半形間隔號 STSong-Light 沒有，會變 □；換全形直線
    '😞': '(1分)', '😕': '(2分)', '😐': '(3分)', '🙂': '(4分)', '😄': '(5分)',
    '🙏': '', '🙌': '', '😊': '', '📥': '', '👤': '', '🗑': '', '⏰': '', '▶': '',
}


def clean(t):
    t = str(t)
    for k, v in GLYPH.items():
        t = t.replace(k, v)
    # 剩下抓不到的符號一律拿掉，不要留空白讓版面看起來破
    return ''.join(c for c in t if ord(c) < 0x2100 or 0x3000 <= ord(c) <= 0xFFFF)


class Doc:
    """很小的排版器。reportlab 的 Platypus 對中文換行處理得不好，
    自己算換行反而可控——尤其中文沒有空白可以斷。"""

    def __init__(self, path, title):
        self.c = canvas.Canvas(path, pagesize=A4)
        self.c.setTitle(title)
        self.y = H - 24 * mm
        self.page = 1

    def wrap(self, text, size, width):
        out, line = [], ''
        for ch in clean(text):
            if ch == '\n':
                out.append(line); line = ''; continue
            if pdfmetrics.stringWidth(line + ch, F, size) > width:
                out.append(line); line = ch
            else:
                line += ch
        out.append(line)
        return out

    def space(self, mm_):
        self.y -= mm_ * mm

    def newpage(self):
        self._footer()
        self.c.showPage(); self.page += 1
        self.y = H - 24 * mm

    def _footer(self):
        self.c.setFillColorRGB(0.7, 0.72, 0.75); self.c.setFont(F, 8)
        self.c.drawCentredString(W / 2, 12 * mm, f'Step1ne 德仁管理顧問　｜　第 {self.page} 頁')

    def text(self, s, size=10, color=INK, indent=0, lead=1.55, gap=0):
        for line in self.wrap(str(s), size, R - L - indent):
            if self.y < 24 * mm:
                self.newpage()
            self.c.setFillColorRGB(*color); self.c.setFont(F, size)
            self.c.drawString(L + indent, self.y, line)
            self.y -= size * lead
        self.y -= gap * mm

    def h1(self, s):
        if self.y < 40 * mm:
            self.newpage()
        self.space(2)
        self.c.setFillColorRGB(*GOLD); self.c.setFont(F, 13)
        self.c.drawString(L, self.y, s)
        self.y -= 4 * mm
        self.c.setStrokeColorRGB(0.9, 0.87, 0.82); self.c.setLineWidth(0.7)
        self.c.line(L, self.y, R, self.y)
        self.y -= 7 * mm

    def save(self):
        self._footer(); self.c.save()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('who')
    ap.add_argument('--out')
    a = ap.parse_args()

    rows = D.d1(f"SELECT a.*, j.title AS job_full FROM applications a "
                f"LEFT JOIN jobs j ON j.slug = a.job_slug "
                f"WHERE a.id = {D.q(a.who)} OR a.name = {D.q(a.who)}")
    if not rows:
        sys.exit(f'找不到「{a.who}」')
    app = rows[0]

    reps = D.d1(f"SELECT content_md, created_at FROM reports "
                f"WHERE application_id = {D.q(app['id'])} ORDER BY created_at DESC LIMIT 1")
    msgs = D.d1(f"SELECT role, content, created_at FROM messages "
                f"WHERE application_id = {D.q(app['id'])} ORDER BY id ASC")
    msgs = [m for m in msgs if m['content'] != '（候選人已進入面談室）']

    out = a.out or os.path.expanduser(
        f"~/Desktop/AI初篩報告_{app['name']}_{(app['interview_ended_at'] or '')[:10] or 'undated'}.pdf")
    d = Doc(out, f"AI 初篩報告 — {app['name']}")

    # 抬頭
    d.c.setFillColorRGB(*GOLD); d.c.setFont(F, 9)
    d.c.drawString(L, d.y, 'STEP1NE　德仁管理顧問')
    d.y -= 9 * mm
    d.text('AI 初步面談紀錄', 19, INK, gap=1)
    d.text(f"{app['name']}　·　{app['job_full'] or app['job_slug']}", 11, GREY, gap=1)
    meta = [
        f"面談時間：{app.get('interview_started_at') or app['created_at']}"
        f" – {app.get('interview_ended_at') or '未結束'}",
        f"對話則數：{len(msgs)}　·　來源：{'顧問主動接觸' if app.get('utm_source') == 'consultant' else '官網表單'}",
    ]
    for m in meta:
        d.text(m, 9, GREY)
    d.space(3)

    # 報告
    d.h1('初篩報告（由 AI 面談助理「阿財」產出）')
    if reps:
        for line in reps[0]['content_md'].split('\n'):
            s = line.rstrip()
            if not s:
                d.space(2); continue
            if s.startswith('【') or s.startswith('⚠️ 面談未完成'):
                d.text(s, 11, INK, gap=1)
            elif not s.startswith(' ') and not s.startswith('　'):
                d.text(s, 10.5, GOLD, gap=0.5)     # 區塊標題
            else:
                d.text(s.strip(), 9.5, INK, indent=5 * mm)
    else:
        d.text('（這場面談還沒有報告）', 10, GREY)

    # 逐字稿
    d.newpage()
    d.h1('完整逐字稿')
    d.text('這是候選人與 AI 面談助理的原始對話，未經編輯。', 9, GREY, gap=3)
    for m in msgs:
        who = '候選人' if m['role'] == 'candidate' else '阿財（AI）'
        col = (0.23, 0.37, 0.54) if m['role'] == 'candidate' else GOLD
        if d.y < 34 * mm:
            d.newpage()
        d.text(f"{who}　{m['created_at'][11:16]}", 9, col)
        d.text(m['content'], 10, INK, indent=4 * mm, gap=2.5)

    d.save()
    print(f'  ✅ {out}')
    print(f'     {len(msgs)} 則對話　{d.page} 頁')


if __name__ == '__main__':
    main()
