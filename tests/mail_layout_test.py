"""mail_layout.py（Python 這邊的候選人信件新版設計）測試：開關、字不變、條列與署名。
  python3 tests/mail_layout_test.py
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import mail_layout as ML  # noqa: E402

fail = 0


def check(c, m):
    global fail
    print(('PASS ' if c else 'FAIL ') + m)
    fail += 0 if c else 1


def q(v):
    return lambda sql: ([{'value': v}] if v is not None else [])


check(ML.mode(q(None)) == 'off', '沒設＝off')
check(ML.mode(q('TEST ')) == 'test', 'test（大小寫空白不影響）')
check(ML.mode(lambda sql: 1 / 0) == 'off', '查不到就 off')
check(ML.use_v2('王測試', q('test')) and not ML.use_v2('陳真人', q('test')), 'test 只給姓名含「測試」')
check(ML.use_v2('陳真人', q('on')) and not ML.use_v2('王測試', q('off')), 'on 全部、off 都不用')

text = '\n'.join(['王測試 您好，', '', '感謝您今天撥空參加「業務專員」的線上面談。', '',
                  '另外，下面幾個職缺方向也接近：', '1. 客戶成功專員｜月薪 4 萬｜https://step1ne.com/jobs/a/', '有興趣的話回覆這封信。', '',
                  'Step1ne 德仁管理顧問', 'Email：official@step1ne.com', '就業服務許可證：北市就服字第 0363 號'])
h = ML.render_text('感謝您參加「業務專員」線上面談', text, avatar=True)
for w in ['王測試 您好，', '感謝您今天撥空參加「業務專員」的線上面談。', '客戶成功專員｜月薪 4 萬｜', 'https://step1ne.com/jobs/a/', '有興趣的話回覆這封信。']:
    check(w in h, f'保留：{w[:18]}')
check('<ol' in h and 'href="https://step1ne.com/jobs/a/"' in h, '編號條列＋網址可點')
check(h.count('北市就服字第 0363 號') == 1, '署名有許可證時頁尾不重複')
check('max-width:600px' in h and '#F4F6F8' in h and 'acai-avatar-192.png' in h and 'height="28"' in h, '新版樣式')
check('f4f1ea' not in h.lower(), '沒有舊米色')
print(f'\n{fail} 項失敗' if fail else '\n全部通過')
sys.exit(1 if fail else 0)
