#!/usr/bin/env python3
"""禁刊過濾器：把不能公開的內容從職缺原文裡揪出來。

為什麼要獨立成一支、而且用規則不用 LLM：
  這一關是**法遵**，不是文筆。就業服務法第 5 條的年齡／性別／婚育／國籍條件
  刊出去是會被裁罰的，而 LLM 每次的判斷不保證一致——同一份 JD 跑兩次
  可能一次擋掉一次放過。規則寫死才能「每次都擋」，而且可以單獨測試。

  LLM 負責的是把 JD 改寫得好看（那是它擅長的），
  這支負責的是「有沒有漏掉不該刊的東西」（那需要每次都一樣）。

規則來源：~/工作流程技能包/recruiting-workflow/step1ne-job-posting/SKILL.md Phase 2

用法：
    from publishing_filters import scan, format_report
    result = scan(原文, client_name='某某科技', client_code='C07')
    print(format_report(result))

⚠️ 這支只**標記**，不自動刪改。刪改是擬 JD 那一步的事，
   因為「限 35 歲以下」正確的處理不是刪掉整句，而是改寫成合法的說法。
"""
import re


# ── 規則表 ──
# 每條：(類型, 正規表達式, 為什麼不能刊, 建議怎麼改)
#
# ⚠️ 加規則時請一併加測試（見本檔最下面的 __main__）。
#    沒有測試的規則，下次有人改 regex 時會靜默失效。
RULES = [
    # 就業服務法第 5 條：年齡
    ('年齡歧視', r'(限|需|須|僅限|只收|徵)?\s*\d{2}\s*歲\s*(以下|以內|(以)?上)',
     '違反就業服務法第 5 條（年齡歧視），刊出會被裁罰',
     '拿掉年齡門檻。若客戶真正在意的是體力或職涯階段，改寫成「歡迎職涯初期人才」'
     '或如實描述工作型態（需輪班／需久站），讓求職者自己判斷'),
    ('年齡歧視', r'年齡\s*[:：]?\s*\d{2}\s*[-–~至]\s*\d{2}',
     '違反就業服務法第 5 條（年齡歧視）',
     '整段移除。年齡條件請填進 jobs.client_screen_conditions（內部欄位），由顧問評估'),
    ('年齡歧視', r'(未滿|不超過|不得超過)\s*\d{2}\s*歲',
     '違反就業服務法第 5 條（年齡歧視）',
     '整段移除，改填內部欄位'),
    ('年齡歧視', r'(年輕|年紀輕|六年級|七年級|八年級|九年級)(生|後)?(佳|尤佳|優先)?',
     '間接年齡指涉，實務上同樣認定為年齡歧視',
     '改成具體的能力或經驗描述'),

    # 就業服務法第 5 條：性別
    ('性別歧視', r'(限|僅限|只收|徵|需|以)\s*(男|女)(性|生|士)?(為主|佳|尤佳|優先)?',
     '違反就業服務法第 5 條（性別歧視）',
     '拿掉性別。若是體力需求，如實寫「需搬運 20 公斤以上物品」'),
    ('性別歧視', r'(男|女)(性|生)\s*(佳|尤佳|優先|為佳)',
     '違反就業服務法第 5 條（性別歧視）',
     '拿掉性別敘述'),

    # 就業服務法第 5 條：婚育
    ('婚育歧視', r'(已婚|未婚|單身|需已婚|限未婚)',
     '違反就業服務法第 5 條（婚姻歧視）',
     '整段移除'),
    ('婚育歧視', r'(無|不得有|近期無)\s*(懷孕|生育|婚育|生子)\s*(計畫|規劃|打算)?',
     '違反就業服務法第 5 條（懷孕歧視），這條裁罰最重',
     '整段移除，且不要用任何形式暗示'),

    # 就業服務法第 5 條：國籍／種族
    # 「需具本國籍」「限有中華民國籍」——動詞後面常夾一個「具／有」，不能漏
    ('國籍歧視', r'(限|僅限|只收|需|須)\s*(具備|具|有)?\s*(本國|台灣|中華民國|國)籍',
     '違反就業服務法第 5 條（國籍歧視）',
     '移除。若確實有工作權法規限制（如需具備我國工作許可），如實寫「須具合法工作權」'),
    ('國籍歧視', r'(不收|不用|排除|不考慮)\s*(外籍|外勞|移工|陸籍|外國人)',
     '違反就業服務法第 5 條（國籍歧視）',
     '移除'),

    # 就業服務法第 5 條：容貌／身心障礙
    ('容貌歧視', r'(五官端正|形象佳|外型佳|外貌佳|身高\s*\d{3}|體重\s*\d{2,3}|口齒清晰且外型)',
     '違反就業服務法第 5 條（容貌歧視）',
     '移除。接待類職務可寫「需面對客戶，注重服裝儀容」'),
    ('身心障礙歧視', r'(不收|不適合|排除)\s*(身心障礙|殘障|身障)',
     '違反就業服務法第 5 條（身心障礙歧視）',
     '移除'),

    # 客戶保密
    ('內部獵頭策略', r'(建議鎖定|建議搜尋|搜尋職稱|目標公司|挖角|對標公司|競品名單|人選來源建議)',
     '等於把挖角名單公開給同業看',
     '整段移除，只留在內部筆記'),
    ('內部溝通備註', r'(客戶(最初|原本|一開始)說|建議二次確認|與確認的.{0,10}有落差|內部備註|待客戶回覆)',
     '這是我們跟客戶之間的往來，不是給求職者看的',
     '整段移除'),
    ('未確認欄位', r'(待確認|待補|未確認|\?\?|待客戶確認|TBD|tbd)',
     '不刊未驗證資訊——刊錯的工作條件會害候選人白跑一趟',
     '列進「待你確認」清單請顧問補齊，補齊前該欄位不出現在頁面上'),

    # 內部作業口吻（不違法，但不該出現在對外頁面）
    ('內部作業口吻', r'(由\s*Step1ne\s*投保|由我方投保|我司投保|派遣公司為本公司)',
     '這是內部作業說明，寫在頁面上會讓求職者覺得自己是貨物',
     '改成勞工視角：「勞健保與各項權益依勞基法辦理，與一般受僱者相同」，並放 FAQ 不放規格表'),
]


def scan(text, client_name=None, client_code=None, extra_secrets=None):
    """掃一段文字，回傳所有命中。

    client_name / client_code 是動態規則——每份職缺的客戶都不一樣，
    不可能寫死在 RULES 裡。這兩個一定要傳，漏傳就等於這一關沒跑。
    """
    text = text or ''
    hits = []

    for kind, pattern, why, fix in RULES:
        for m in re.finditer(pattern, text):
            if kind == '未確認欄位' and _is_disclosure(text, m.start(), m.end()):
                continue
            hits.append({
                'kind': kind,
                'matched': m.group(0).strip(),
                'context': _context(text, m.start(), m.end()),
                'why': why,
                'fix': fix,
            })

    # 客戶名稱：整串比對，另外也比對去掉「股份有限公司／有限公司／公司」的主體
    for name in _name_variants(client_name):
        for m in re.finditer(re.escape(name), text):
            hits.append({
                'kind': '委任客戶名稱',
                'matched': m.group(0),
                'context': _context(text, m.start(), m.end()),
                'why': '客戶保密義務。頁面／schema／網址／職缺卡都不可出現',
                'fix': '改用產業描述（例：「國際半導體大廠」「大型電商平台」）。'
                       '用哪個描述牽涉客戶敏感度，請顧問決定，不要自己挑',
            })

    if client_code:
        for m in re.finditer(re.escape(client_code), text):
            hits.append({
                'kind': '客戶代號外洩',
                'matched': m.group(0),
                'context': _context(text, m.start(), m.end()),
                'why': 'client_code 是內部代號，任何對外輸出都不得出現',
                'fix': '移除。區隔同名職缺請改用班別／薪資帶／僱傭型態／外語加給／地點',
            })

    for s in (extra_secrets or []):
        if not s:
            continue
        for m in re.finditer(re.escape(s), text):
            hits.append({
                'kind': '自訂禁字',
                'matched': m.group(0),
                'context': _context(text, m.start(), m.end()),
                'why': '顧問指定不可外流的字串',
                'fix': '移除',
            })

    return _dedupe(hits)


def scan_output(html_or_text, client_name=None, client_code=None, extra_secrets=None):
    """發布前掃「產出的頁面」——跟 scan() 同一套規則，但語氣是「這是最後一道」。

    為什麼要掃兩次：scan() 掃的是顧問給的原文（預期會有一堆命中，那很正常）；
    這支掃的是擬完 JD 之後要上線的成品，**任何一條命中都代表不可以發布**。
    """
    return scan(html_or_text, client_name, client_code, extra_secrets)


def _name_variants(client_name):
    if not client_name:
        return []
    name = client_name.strip()
    if not name:
        return []
    out = {name}
    stem = re.sub(r'(股份)?有限公司$|公司$|集團$|企業社$', '', name).strip()
    if len(stem) >= 2:
        out.add(stem)
    return sorted(out, key=len, reverse=True)


# 「未確認欄位」這條規則要擋的是：把還沒跟客戶確認的工作條件，當成事實刊出去。
# 但它的正則會連下面這種句子一起擋掉：
#
#   「稅前或稅後、有無匯率保障機制，業主尚未確認，顧問會問清楚後告知。」
#
# 那句話正好是這條規則想要的結果——沒有冒充事實，而且明白告訴候選人
# 這一項還沒定、誰會去問。把它擋掉會逼顧問改成「乾脆不寫」，
# 候選人反而是到面試才發現條件不同，正是這條規則本來要防的事。
#
# 2026-08-11 主管特助（台日兩地）那張單就是被這樣誤擋成「不可以發布」。
# 判準：命中的位置附近有沒有「誰會去確認、什麼時候告訴你」。
# 有 → 這是坦白揭露，放行；沒有 → 那就是留白的佔位符（例：「薪資：未確認」），照擋。
_DISCLOSURE = re.compile(
    r'(顧問|我們|業主|客戶).{0,12}(會|將).{0,8}(告知|說明|問|確認|回覆)'
    r'|確認後.{0,10}(告知|說明|通知|回覆)'
    r'|(取得|問到).{0,6}答案'
    r'|會(主動)?(告知|通知)您')


def _is_disclosure(text, start, end, pad=70):
    seg = text[max(0, start - pad):min(len(text), end + pad)]
    return bool(_DISCLOSURE.search(seg))


def _context(text, start, end, pad=24):
    a = max(0, start - pad)
    b = min(len(text), end + pad)
    return ('…' if a > 0 else '') + text[a:b].replace('\n', ' ') + ('…' if b < len(text) else '')


def _dedupe(hits):
    seen, out = set(), []
    for h in hits:
        key = (h['kind'], h['matched'], h['context'])
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def pending_fields(spec, required=None):
    """哪些求職者一定會問、但這份規格裡還沒有答案的欄位。

    SKILL.md Phase 7 的「待你確認」清單就是這個。
    寧可頁面上不寫，也不要用合理猜測填滿——猜錯的工作條件比空白更糟。
    """
    required = required or [
        ('locations', '工作地點'),
        ('salary_min', '薪資下限'),
        ('salary_max', '薪資上限'),
        ('employment', '僱傭型態'),
        ('intro', '職缺導言'),
        ('duties', '工作內容'),
        ('must', '應徵條件（必要）'),
    ]
    missing = []
    for key, label in required:
        v = spec.get(key)
        if v is None or v == '' or v == [] or v == {}:
            missing.append(label)
    return missing


def format_report(hits, missing=None, title='禁刊過濾器'):
    """人看的報告。送審訊息與終端機都用這一份，不要各寫一套格式。"""
    lines = []
    if not hits:
        lines.append(f'✅ {title}：沒有命中任何禁刊規則')
    else:
        lines.append(f'🚫 {title}：擋下 {len(hits)} 處')
        by_kind = {}
        for h in hits:
            by_kind.setdefault(h['kind'], []).append(h)
        for kind, group in by_kind.items():
            lines.append(f'\n【{kind}】{group[0]["why"]}')
            for h in group:
                lines.append(f'  ・原文：「{h["matched"]}」')
                lines.append(f'    上下文：{h["context"]}')
            lines.append(f'  → 處理方式：{group[0]["fix"]}')
    if missing:
        lines.append('\n❓ 待你確認（來源沒給，頁面上暫時不會出現）：')
        for m in missing:
            lines.append(f'  ・{m}')
    return '\n'.join(lines)


if __name__ == '__main__':
    # 規則的自我測試。改 regex 之後請先跑這個：python3 publishing_filters.py
    CASES = [
        ('限 35 歲以下，個性積極', '年齡歧視'),
        ('年齡：25-35', '年齡歧視'),
        ('未滿 40 歲尤佳', '年齡歧視'),
        ('限男性，需輪班', '性別歧視'),
        ('女性佳', '性別歧視'),
        ('限未婚', '婚育歧視'),
        ('近期無生育計畫', '婚育歧視'),
        ('限本國籍', '國籍歧視'),
        ('需具本國籍', '國籍歧視'),
        ('不收外籍', '國籍歧視'),
        ('五官端正', '容貌歧視'),
        ('建議鎖定台積電、聯電的製程工程師', '內部獵頭策略'),
        ('薪資待確認', '未確認欄位'),
        ('由 Step1ne 投保勞健保', '內部作業口吻'),
    ]
    bad = 0
    for text, want in CASES:
        kinds = {h['kind'] for h in scan(text)}
        ok = want in kinds
        print(('✅' if ok else '❌'), f'{text!r} → {sorted(kinds) or "（沒命中）"}')
        if not ok:
            bad += 1
    # 客戶名稱是動態的，另外測
    k = {h['kind'] for h in scan('本職缺由巨匠科技股份有限公司委託', client_name='巨匠科技股份有限公司')}
    print(('✅' if '委任客戶名稱' in k else '❌'), '客戶名稱 →', sorted(k))
    if '委任客戶名稱' not in k:
        bad += 1
    # 不該誤殺的正常句子
    for clean in ['需具備三年以上專案經驗', '每月薪資 40,000 至 55,000 元', '工作地點：台北市內湖區']:
        k = scan(clean)
        print(('✅' if not k else '❌'), f'（不該命中）{clean!r} → {[h["kind"] for h in k]}')
        if k:
            bad += 1
    print('\n全部通過' if not bad else f'\n{bad} 條沒過')
    raise SystemExit(1 if bad else 0)
