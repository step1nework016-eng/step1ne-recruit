#!/usr/bin/env python3
"""拿人選去敲門（2026-09-29 Jacky 拍板並授權直接寄）。

跟一般開發信的差別：開頭不是「我們能幫你找人」，而是「剛好手上有一位做過＿＿的人選」。
其餘照 references/開發信框架.md（自我介紹、A 級評鑑、做法、低門檻邀請、謝謝與祝福、署名）。

規則：
- 人選一律匿名：不寫姓名、年齡、性別、現職／前雇主名稱；評分、薪資落差這些內部資訊不外流。
- 人選本人必須已同意匿名推薦（Phoebe 電話確認）才能寄。
- 寄之前走後台 /admin/bd/:id/decide（客戶名單守門、收信網域檢查、自動排追信、TG 通知都在那裡）。

用法：
    python3 candidate_knock.py targets.json --dry-run   # 只印信件
    python3 candidate_knock.py targets.json             # 建檔＋寄出
targets.json：[{"candidate":"finance_lead","company":"…","contact_email":"…","contact_name":"王經理"|null,
                "job_title":"主辦會計","job_url":"https://…","industry":"…"}]
"""
import json
import os
import sys
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import d1_http as D
import bd_sig

API = 'https://step1ne-backoffice-worker.aiagentg888.workers.dev'

CANDIDATES = {
    'finance_lead': {
        'what': '財會主管',
        'bullets': [
            '約 10 年財會資歷，四大會計師事務所審計出身',
            '在日系集團多年，後任集團子公司主辦會計，帶過約 4 人團隊',
            '重視內控與帳務合規，可以很快到職',
        ],
    },
    'safety_site': {
        'what': '工地／職安',
        'bullets': [
            '約 10 年營造現場管理經驗，目前在高科技廠房新建工程擔任現場工程師',
            '持有營造業甲種勞工安全衛生業務主管證照，想往職安專業發展',
            '住雲林，雲嘉南一帶案場通勤沒問題，10 月可到職',
        ],
    },
}

SUBJECTS = [
    '關於貴公司{job}職缺，剛好有一位人選想跟您提',
    '關於貴公司{job}職務的招募',
    '想跟您請教近期{job}的招募需求',
    '關於{job}職缺，想提供一個招募方向',
    '想交流一下{job}人才招募',
]


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def letter(t):
    c = CANDIDATES[t['candidate']]
    hi = f"{t['contact_name']}您好，" if t.get('contact_name') else '您好，'
    # 只查得到公司共用信箱（service@、sales@）時，請對方轉給人資，不然容易被當成推銷信丟掉
    if t.get('generic_inbox'):
        hi += '\n這封寄到公司的公開信箱，想麻煩您協助轉給人資或招募同仁，謝謝。'
    bullets = '\n'.join(f'・{b}' for b in c['bullets'])
    body = f"""{hi}
我是 Step1ne（德仁管理顧問）的 Jacky，我們用自己開發的 AI 面談系統搭配顧問篩選，協助企業做正職招募、人力派遣與高階獵才。臺北市政府勞動局 114 年度評鑑 A 級，就業服務許可北市就服字第 0363 號。

有留意到貴公司目前正在招募{t['job_title']}。剛好我們手上有一位人選，背景跟這個職務滿接近，所以想先跟您提一下：
{bullets}

這位已經完成我們的 AI 初步面談，也由顧問複核過，本人同意在不具名的前提下先跟貴公司介紹。我們是成功到職才收費，沒到職原則上不收。

不一定要馬上談合作。如果您覺得方向對，回信跟我說一聲，我再提供完整的去識別資料；也可以直接把這個職缺的 JD 給我，我先幫您對一下條件。

謝謝您撥冗看完這封信，期待有機會為貴公司協助。
敬祝　商祺"""
    return bd_sig.ensure(body)


def admin_token():
    for f in ('recruit.env', 'tokens.env'):
        p = os.path.expanduser(f'~/.config/workflow-os/{f}')
        if os.path.exists(p):
            for ln in open(p, encoding='utf-8'):
                k, _, v = ln.strip().partition('=')
                if k in ('ADMIN_TOKEN', 'RECRUIT_ADMIN_TOKEN') and v:
                    return v.strip('"\'')
    raise SystemExit('找不到後台 token')


def decide_send(bid):
    req = urllib.request.Request(f'{API}/admin/bd/{bid}/decide',
                                 data=json.dumps({'action': 'send', 'decided_by': 'Jacky（口頭授權：拿人選敲門）'}).encode(),
                                 headers={'content-type': 'application/json', 'authorization': 'Bearer ' + admin_token(), 'user-agent': 'step1ne-candidate-knock/1.0'})
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or '{}') or {'ok': False, 'error': f'HTTP {e.code}'}


def main():
    targets = json.load(open(sys.argv[1], encoding='utf-8'))
    dry = '--dry-run' in sys.argv
    for t in targets:
        if not t.get('contact_email'):
            print(f"⏭ {t['company']}：沒有信箱，跳過（改電話）")
            continue
        body = letter(t)
        subj = SUBJECTS[0].format(job=t['job_title'])
        if dry:
            print(f"===== {t['company']} <{t['contact_email']}>\n主旨：{subj}\n{body}\n（正文約 {len(body.split(chr(10)+chr(10)+'Jacky Chen')[0])} 字）\n")
            continue
        rid = str(uuid.uuid4())
        D.query(f"""INSERT INTO bd_outreach
          (id, created_at, updated_at, company, why_company, contact_email, contact_name, status,
           subject, subject_alts, body, job_title, job_source, job_url, channel, difficulty_type, industry, source_url)
          VALUES ({q(rid)}, datetime('now'), datetime('now'), {q(t['company'])},
           {q('拿人選敲門：' + CANDIDATES[t['candidate']]['what'])}, {q(t['contact_email'])}, {q(t.get('contact_name'))}, 'pending',
           {q(subj)}, {q(chr(10).join(s.format(job=t['job_title']) for s in SUBJECTS))}, {q(body)},
           {q(t['job_title'])}, {q(t.get('job_source') or '公開職缺')}, {q(t.get('job_url'))}, 'email', 'candidate_knock',
           {q(t.get('industry'))}, {q(t.get('job_url'))})""")
        r = decide_send(rid)
        print(('📤 已寄 ' if r.get('ok') else '⛔ 沒寄 ') + f"{t['company']} <{t['contact_email']}>" + ('' if r.get('ok') else f"：{r.get('error')}"))


if __name__ == '__main__':
    main()
