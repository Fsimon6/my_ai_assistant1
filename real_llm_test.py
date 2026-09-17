# -*- coding: utf-8 -*-
"""Phase 1 真实端到端 LLM 测试：验证“程序直接输出结构化数据，LLM 不参与事实数据/列选择”。

核心验证：
- 大数据枚举（全量/前N/后N/中间范围/多列）由 Python 直接流式输出，不被 max_tokens 截断。
- 精确查找（lookup）：事实值与目标列由 Python 从 Structured Representation 精确定位并提取，
  LLM 不参与列选择（最终回答中的事实值必须来自 Python 已确定的 structured result）。
  重点：Order ID → Variation 必须返回 Variation 列真值，绝不返回 Product Category（如“男士运动套装”）。
"""
import sys, asyncio, re
sys.path.insert(0, r'C:\Users\Administrator\Desktop\my_ai_assistant')
import os
os.environ.setdefault('PYTHONPATH', r'C:\Users\Administrator\Desktop\my_ai_assistant')
from backend.services.rag_service import get_rag_service, RagService
from backend.services.table_representation import load_representation

D5 = 'c3294aa7e9fa4dc19f3a4738ae2c53c8'


async def ask(query, doc_id=D5):
    rag = get_rag_service()
    parts = []
    async for ch in rag.rag_query(query, context_count=3, user_id=61, document_id=doc_id):
        parts.append(ch)
    return ''.join(parts)


def gt_for_oid(oid):
    rep = load_representation(D5)
    t = rep['workbook']['sheets'][0]['tables'][0]
    cols = t['columns']; rows = t['rows']
    ci = {c['technical_name']: c['col_index'] for c in cols}
    lci = ci['Order ID'] - 1
    for r in rows:
        cells = r.get('cells', [])
        v = cells[lci].get('value') if lci < len(cells) else None
        if v is not None and RagService._norm(str(v)) == RagService._norm(oid):
            return {cn: RagService._cell_text(cells[ci[cn] - 1].get('value') if ci[cn] - 1 < len(cells) else None)
                    for cn in ['Variation', 'SKU ID', 'Shipping Provider Name', 'Product Category']}
    return None


def enum_rows(text):
    return [int(m.group(1)) for ln in text.split('\n') if (m := re.match(r'第(\d+)行:\s', ln))]


async def main():
    rep = load_representation(D5)
    t = rep['workbook']['sheets'][0]['tables'][0]
    rows = t['rows']
    ci_oid = {c['technical_name']: c['col_index'] for c in t['columns']}['Order ID']
    head_v = next(r['cells'][ci_oid - 1].get('value') for r in rows if str(r['cells'][ci_oid - 1].get('value')).isdigit())
    mid_v = rows[100]['cells'][ci_oid - 1].get('value')
    tail_v = rows[-1]['cells'][ci_oid - 1].get('value')

    enum_cases = [
        ('1.全量单列', '把所有 Shipping Provider Name 列出来', 448, 2, 449),
        ('2.前50条', '把前50条 SKU ID 列出来', 50, 2, 51),
        ('3.第100~150条', '把第100到150条 SKU ID 列出来', 51, 101, 151),
        ('4.最后10条', '把最后10条 SKU ID 列出来', 10, 440, 449),
        ('5.多列全量', '把所有订单的 Order ID 和 SKU ID 列出来', 448, 2, 449),
    ]
    # (label, oid, 目标列或列列表) —— 验证事实值来自 Python structured result
    lookup_cases = [
        ('6.开头→Variation', head_v, 'Variation'),
        ('7.中间→Variation', mid_v, 'Variation'),
        ('8.末尾→Variation', tail_v, 'Variation'),
        ('9.中间→SKUID', mid_v, 'SKU ID'),
        ('10.中间→ShippingProviderName', mid_v, 'Shipping Provider Name'),
        ('11.中间→ProductCategory', mid_v, 'Product Category'),
        ('12.中间→Variation+SKUID', mid_v, ['Variation', 'SKU ID']),
        ('13.不存在值', '1234567890123456XY', None),
    ]

    print('================ 枚举类（验证不被 max_tokens 截断 / 顺序 / 完整性） ================')
    for label, q, exp_n, exp_first, exp_last in enum_cases:
        text = await ask(q)
        rows_n = enum_rows(text)
        n = len(rows_n)
        first = min(rows_n) if rows_n else None
        last = max(rows_n) if rows_n else None
        ok = (n == exp_n and first == exp_first and last == exp_last)
        print(f'[{label}] {q}')
        print(f'    数据行数={n} (期望 {exp_n}) | 首行={first} (期望 {exp_first}) | 末行={last} (期望 {exp_last}) | {"PASS" if ok else "FAIL"}')
        if not ok:
            print(f'    首80字: {text[:80].replace(chr(10), " ")}')
            print(f'    尾80字: {text[-80:].replace(chr(10), " ")}')
        print()

    print('================ 精确查找类（Python 直出；验证事实值来自 structured result，无列误归属） ================')
    npass = nfail = 0
    for label, oid, col in lookup_cases:
        if oid is None:
            text = await ask(f'帮我查 Order ID 为 {oid} 的 Variation')
            ok = ('未找到' in text)
            print(f'[{label}] 不存在值 -> 诚实未找到={ok} | {"PASS" if ok else "FAIL"}')
        else:
            cols = col if isinstance(col, list) else [col]
            q = f'帮我查 Order ID 为 {oid} 的 {" 和 ".join(cols)}'
            text = await ask(q)
            gt = gt_for_oid(oid)
            exp_vals = [gt[c] for c in cols]
            present = all(v in text for v in exp_vals)
            # 列误归属专项：问 Variation 时，答案绝不应出现 Product Category 值（如“男士运动套装”）
            misattr = True
            if 'Variation' in cols:
                misattr = gt['Product Category'] not in text
                # 反之，问 Product Category 时，应正确出现其真值
            if col == 'Product Category':
                misattr = gt['Product Category'] in text
            ok = present and misattr and ('未找到' not in text)
            print(f'[{label}] {q}')
            print(f'    期望值={exp_vals} | 命中={present} | 列误归属防护={misattr} | {"PASS" if ok else "FAIL"}')
            if not ok:
                print(f'    回答片段: {text[:120].replace(chr(10), " ")}')
        if ok:
            npass += 1
        else:
            nfail += 1
        print()
    print(f'LOOKUP SUMMARY: PASS={npass} FAIL={nfail}')


asyncio.run(main())
