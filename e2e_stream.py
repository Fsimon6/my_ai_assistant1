# -*- coding: utf-8 -*-
"""真实浏览器 E2E 验收（HTTP 层，复刻前端 Chat 的 fetch 流式请求，命中真实 FastAPI:8000）。

等价于：Vue → HTTP(fetch ndjson) → FastAPI → RAG service → Structured Table Access → Streaming
浏览器最终显示文本 = 流中 complete 帧的 content（与 useStreamingChat 累计值一致）。
"""
import sys, json, urllib.request, re, asyncio
sys.path.insert(0, r'C:\Users\Administrator\Desktop\my_ai_assistant')
import os
os.environ.setdefault('PYTHONPATH', r'C:\Users\Administrator\Desktop\my_ai_assistant')
from backend.services.rag_service import get_rag_service, RagService
from backend.services.table_representation import load_representation

BASE = 'http://127.0.0.1:8000'
USER, PW = 'FSimon', 'Test123456'
D5 = 'c3294aa7e9fa4dc19f3a4738ae2c53c8'   # 直邮5店 7.10号订单.xlsx (448 rows)
D1 = 'ff92b19423d14e4f8dba79dafecbc1f4'   # 直邮一店 8.20号订单.xlsx (19 rows)


def login():
    req = urllib.request.Request(BASE + '/api/v1/auth/login',
        data=json.dumps({'username': USER, 'password': PW}).encode(),
        headers={'Content-Type': 'application/json'})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return r['data']['access_token']


def stream_query(token, query, document_id=None):
    """复刻前端 Chat.vue useStreamingChat 的 fetch 流式请求，返回 (full_text, chunk_count, complete_text)。"""
    body = {'query': query, 'history': [], 'stream': True, 'context_count': 3,
            'character_id': None}
    if document_id:
        body['document_id'] = document_id
    req = urllib.request.Request(BASE + '/api/v1/rag/query-with-history',
        data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json',
                 'Accept': 'application/x-ndjson',
                 'Authorization': f'Bearer {token}'})
    resp = urllib.request.urlopen(req, timeout=180)
    buf = ''
    full = ''
    chunks = 0
    while True:
        c = resp.read(4096)
        if not c:
            break
        buf += c.decode('utf-8', 'replace')
        while '\n' in buf:
            line, buf = buf.split('\n', 1)
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get('type') == 'chunk':
                full += obj.get('content', '')
                chunks += 1
            elif obj.get('type') == 'complete':
                full = obj.get('content') or full
    return full, chunks, full


def parse_enum(text):
    """解析后端实际输出：第N行: 单列值 / 第N行: k=v | k=v 多列 / N | v | v 整表。"""
    out = []
    for ln in text.split('\n'):
        m = re.match(r'第(\d+)行:\s?(.*)', ln)
        if m:
            body = m.group(2)
            if '=' in body and ' | ' in body:  # 多列
                d = {}
                for part in body.split(' | '):
                    if '=' in part:
                        k, v = part.split('=', 1)
                        d[k.strip()] = v.strip()
                out.append((int(m.group(1)), d))
            else:
                out.append((int(m.group(1)), body))
            continue
        m = re.match(r'^(\d+)\s*\|\s?(.*)', ln)
        if m and not ln.startswith('行号'):
            out.append((int(m.group(1)), m.group(2)))
    return out


def cell_text(v):
    return RagService._cell_text(v)


def ground_rows(doc_id):
    rep = load_representation(doc_id)
    t = rep['workbook']['sheets'][0]['tables'][0]
    return t['rows'], t['columns']


def expected_enum(op, rows, columns, cols, limit=None, start=None, end=None):
    """返回可与后端实际输出逐行比对的结构：整表为 ' | ' 连接串，单列/多列为值或 dict。"""
    if op == 'first_n':
        sliced = rows[:limit] if limit and limit > 0 else []
    elif op == 'last_n':
        sliced = rows[-limit:] if limit and limit > 0 else []
    elif op == 'range':
        s, e = (start, end)
        if s > e:
            s, e = e, s
        sliced = rows[s - 1:e]
    else:
        sliced = rows
    ncols = len(columns)
    if not cols:
        return [(r['row_index'],
                 ' | '.join(cell_text(r['cells'][i].get('value')) if i < len(r['cells']) else ''
                            for i in range(ncols))) for r in sliced]
    if len(cols) == 1:
        ci = next(c['col_index'] for c in columns if c['technical_name'] == cols[0]) - 1
        return [(r['row_index'],
                 cell_text(r['cells'][ci].get('value') if ci < len(r['cells']) else None))
                for r in sliced]
    ci_map = {nm: next(c['col_index'] for c in columns if c['technical_name'] == nm) - 1 for nm in cols}
    out = []
    for r in sliced:
        d = {}
        for nm in cols:
            ci = ci_map[nm]
            d[nm] = cell_text(r['cells'][ci].get('value') if ci < len(r['cells']) else None)
        out.append((r['row_index'], d))
    return out


def expected_lookup(rows, columns, value):
    matched = []
    for r in rows:
        for cell in r['cells']:
            v = cell.get('value')
            if v is None:
                continue
            if RagService._norm(str(v)) == RagService._norm(value) or value in str(v):
                matched.append(r)
                break
    return [(r['row_index'], [cell_text(r['cells'][i].get('value')) if i < len(r['cells']) else ''
            for i in range(len(columns))]) for r in matched]


def main():
    token = login()
    print('LOGIN OK (FSimon)')
    rows5, cols5 = ground_rows(D5)
    rows1, cols1 = ground_rows(D1)
    n5 = len(rows5)

    cases = []  # (label, query, doc, op, cols, limit, start, end, lookup_value, expect_notfound, expect_count)

    # 1-2 全量单列
    cases.append(('1.全量 Shipping Provider Name', '把所有 Shipping Provider Name 列出来', D5, 'full', ['Shipping Provider Name'], None, None, None, None, False, n5))
    cases.append(('2.全量 SKU ID', '把所有 SKU ID 列出来', D5, 'full', ['SKU ID'], None, None, None, None, False, n5))
    # 3 前50
    cases.append(('3.前50 SKU ID', '把前50条 SKU ID 列出来', D5, 'first_n', ['SKU ID'], 50, None, None, None, False, 50))
    # 4 前100
    cases.append(('4.前100 SKU ID', '把前100条 SKU ID 列出来', D5, 'first_n', ['SKU ID'], 100, None, None, None, False, 100))
    # 5 最后10
    cases.append(('5.最后10 SKU ID', '把最后10条 SKU ID 列出来', D5, 'last_n', ['SKU ID'], 10, None, None, None, False, 10))
    # 6 第100~150
    cases.append(('6.第100~150 SKU ID', '把第100到150条 SKU ID 列出来', D5, 'range', ['SKU ID'], None, 100, 150, None, False, 51))
    # 7 前20 Order ID+SKU ID
    cases.append(('7.前20 Order ID+SKU ID', '把前20条 Order ID 和 SKU ID 列出来', D5, 'first_n', ['Order ID', 'SKU ID'], 20, None, None, None, False, 20))
    # 8 中间 Order ID lookup
    mid_oid = rows5[100]['cells'][next(i for i, c in enumerate(cols5) if c['technical_name'] == 'Order ID')].get('value')
    cases.append(('8.中间 Order ID 查 Variation', f'帮我查 Order ID 为 {mid_oid} 的 Variation', D5, 'lookup', None, None, None, None, mid_oid, False, 1))
    # 9 末尾 Order ID lookup
    end_oid = rows5[-1]['cells'][next(i for i, c in enumerate(cols5) if c['technical_name'] == 'Order ID')].get('value')
    cases.append(('9.末尾 Order ID 查 Variation', f'帮我查 Order ID 为 {end_oid} 的 Variation', D5, 'lookup', None, None, None, None, end_oid, False, 1))
    # 10 不存在 Order ID
    cases.append(('10.不存在 Order ID', '帮我查 Order ID 为 1234567890123456XY 的 Variation', D5, 'lookup', None, None, None, None, '1234567890123456XY', True, 0))
    # 11 普通语义
    cases.append(('11.普通语义(5店)', '这个表主要记录什么？', D5, 'semantic', None, None, None, None, None, False, None))
    # 12 旧表 19 行
    cases.append(('12.一店 全部 SKU(19)', '把这个表里的全部 SKU 都提取出来', D1, 'full', [], None, None, None, None, False, len(rows1)))

    print(f'GROUND: 5店={n5}行, 一店={len(rows1)}行')
    print('-' * 120)
    results = []
    for label, q, doc, op, cols, lim, st, en, lk, nf, exp_n in cases:
        rows, columns = (rows5, cols5) if doc == D5 else (rows1, cols1)
        text, nchunks, _ = stream_query(token, q, doc)
        if op == 'lookup':
            if nf:
                ok = any(k in text for k in ['未找到', '没有找到', '未检索到', '不存在', '无法找到', '查无', '未匹配'])
                h = ('未找到' in text)
                detail = f'诚实未找到={h}'
            else:
                ok = (lk in text)
                detail = f'回带OrderID={lk in text}'
            results.append((label, exp_n, 'lookup', 'n/a', 'n/a', detail, "PASS" if ok else "FAIL"))
            s1 = ("PASS" if ok else "FAIL")
            print(f'[{label}] chunks={nchunks} | {detail} | {s1}')
            continue
        if op == 'semantic':
            is_struct = '已按原始顺序完整列出' in text or '第' in text and '行:' in text
            ok = (len(text) > 20) and not is_struct
            ssem = ("否" if ok else "是(异常)")
            detail = f'len={len(text)} 走语义={ssem}'
            results.append((label, 'n/a', 'semantic', 'n/a', 'n/a', detail, "PASS" if ok else "FAIL"))
            s2 = ("PASS" if ok else "FAIL")
            print(f'[{label}] chunks={nchunks} | {detail} | {s2}')
            continue
        # 枚举类
        actual = parse_enum(text)
        if op == 'lookup':
            pass
        exp = expected_enum(op, rows, columns, cols, lim, st, en)
        # 数量 + 首末行 + 值
        cnt_ok = (len(actual) == len(exp) == exp_n)
        order_ok = (len(actual) == len(exp)) and all(a[0] == e[0] for a, e in zip(actual, exp))
        if cols and len(cols) == 1:
            val_ok = order_ok and all(a[1] == e[1] for a, e in zip(actual, exp))
        else:
            val_ok = order_ok and all(a[1] == e[1] for a, e in zip(actual, exp))
        ok = cnt_ok and order_ok and val_ok
        first = actual[0][0] if actual else None
        last = actual[-1][0] if actual else None
        detail = f'count={len(actual)}/{exp_n} first={first} last={last} order={order_ok} value={val_ok}'
        results.append((label, exp_n, op, first, last, detail, "PASS" if ok else "FAIL"))
        s3 = ("PASS" if ok else "FAIL")
        print(f'[{label}] chunks={nchunks} | {detail} | {s3}')

    print('-' * 120)
    npass = sum(1 for r in results if r[-1] == 'PASS')
    print(f'E2E SUMMARY: PASS={npass} FAIL={len(results)-npass}')
    for r in results:
        print(' | '.join(str(x) for x in r))


if __name__ == '__main__':
    main()
