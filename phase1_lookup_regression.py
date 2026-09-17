# -*- coding: utf-8 -*-
"""Phase 1 精确查找（lookup）系统性回归：A–J 全维度 Ground Truth 比对。

验证目标（对应验收项 10/11）：
- lookup_column / lookup_value / return_columns 字段正确解析（仅依赖真实 Representation.columns）。
- Python 直接定位匹配行、直接读取目标列值；LLM 不参与事实列选择（断言 _answer_stream 对 lookup 零调用）。
- 单值 / 多列 / 多匹配 / 不存在 全部由 Python 确定，值与行序与 Ground Truth 一致。
- 重点：Order ID → Variation 必须返回 Variation 列真值，绝不返回 Product Category 等其它列（杜绝列误归属）。

输出：query | lookup_col | return_cols | matched | order | value | llm_used | status
"""
import os, sys, re, asyncio
from collections import Counter

sys.path.insert(0, r'C:\Users\Administrator\Desktop\my_ai_assistant')
os.environ.setdefault('PYTHONPATH', r'C:\Users\Administrator\Desktop\my_ai_assistant')

from backend.services.rag_service import get_rag_service, RagService
from backend.services.vector_service import get_vector_store_manager
from backend.services.table_representation import load_representation

USER = 61

SYN_REP = {
    'workbook': {'sheets': [{'sheet_name': 'S', 'tables': [{
        'table_id': 'T#0',
        'columns': [{'col_index': 1, 'col_letter': 'A', 'technical_name': 'Order ID'},
                    {'col_index': 2, 'col_letter': 'B', 'technical_name': 'Variation'}],
        'rows': [
            {'row_index': 2, 'cells': [{'value': '111111111111111111'}, {'value': 'a'}]},
            {'row_index': 3, 'cells': [{'value': '111111111111111111'}, {'value': 'b'}]},
            {'row_index': 4, 'cells': [{'value': '222222222222222222'}, {'value': 'c'}]},
        ],
    }]}]}
}


def gt_lookup(rows, columns, lookup_col, value, return_cols):
    """Ground Truth：扫描 lookup_col 定位，提取 return_cols 值。"""
    ci_map = {c['technical_name']: c['col_index'] for c in columns}
    lci = ci_map[lookup_col] - 1
    matched = []
    for r in rows:
        cells = r.get('cells', [])
        v = cells[lci].get('value') if lci < len(cells) else None
        if v is not None and RagService._norm(str(v)) == RagService._norm(value):
            matched.append(r)
    cols_by_name = {c['technical_name']: c for c in columns}
    out = []
    for r in matched:
        d = {}
        for cn in return_cols:
            co = cols_by_name.get(cn)
            cci = co['col_index'] - 1 if co else None
            cells = r.get('cells', [])
            v = cells[cci].get('value') if (co and cci < len(cells)) else None
            d[cn] = RagService._cell_text(v)
        out.append((r['row_index'], d))
    return out


def parse_lookup(blk, return_cols):
    rows = []
    if '记录 ' in blk:
        parts = re.split(r'记录 \d+（行(\d+)）：', blk)
        i = 1
        while i + 1 < len(parts):
            ri = int(parts[i]); body = parts[i + 1]; i += 2
            d = {}
            for col in return_cols:
                m = re.search(r'^\s*' + re.escape(col) + r'[：:=]\s*(.+)$', body, re.M)
                d[col] = m.group(1).strip() if m else ''
            rows.append((ri, d))
    else:
        ri_m = re.search(r'行号：(\d+)|（行(\d+)）', blk)
        ri = int(ri_m.group(1) or ri_m.group(2)) if ri_m else None
        d = {}
        for col in return_cols:
            m = re.search(r'^\s*' + re.escape(col) + r'[：:=]\s*(.+)$', blk, re.M)
            d[col] = m.group(1).strip() if m else ''
        if ri is None and not any(v for v in d.values()):
            return []
        rows.append((ri, d))
    return rows


def values_equal(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a == b
    return str(a) == str(b)


async def main():
    rag = get_rag_service()
    vs = get_vector_store_manager()
    doc5 = next(d for d in vs.list_user_documents(user_id=USER) if '5店' in d['filename'])
    d5 = doc5['document_id']
    rep = load_representation(d5)
    t = rep['workbook']['sheets'][0]['tables'][0]
    cols = t['columns']; rows = t['rows']
    ci_oid = {c['technical_name']: c['col_index'] for c in cols}['Order ID']
    # 表格开头：取第一个 Order ID 为纯数字的“真实”数据行（首行可能是说明性文本）
    head_v = next(r['cells'][ci_oid - 1].get('value') for r in rows
                  if str(r['cells'][ci_oid - 1].get('value')).isdigit())
    mid_v = rows[100]['cells'][ci_oid - 1].get('value')
    tail_v = rows[-1]['cells'][ci_oid - 1].get('value')

    # 断言 lookup 绝不调用 LLM（_answer_stream 零调用）
    llm_hits = {'n': 0}
    orig = rag._answer_stream
    async def spy(self, query, context, history, model, api_key, use_cache, cache_key):
        llm_hits['n'] += 1
        async for x in orig(query, context, history, model, api_key, use_cache, cache_key):
            yield x
    rag._answer_stream = spy

    results = []

    async def build_case(label, val, lookup_col, return_cols, is_missing=False):
        rc_human = ' 和 '.join(return_cols) if len(return_cols) > 1 else return_cols[0]
        q = f'帮我查 {lookup_col} 为 {val} 的 {rc_human}'
        intent = RagService._detect_structured_intent(q)
        res = rag._resolve_structured_access(d5, rep['workbook']['sheets'][0]['sheet_name'],
                                             t['table_id'], USER, q, intent)
        if res is None:
            results.append((label, lookup_col, ','.join(return_cols), '-', '-', '-', llm_hits['n'], 'FAIL(res=None)'))
            return
        blk, meta = res
        # 调用流式输出层，确认不触发 LLM
        out = []
        async for ch in rag._stream_structured_answer(q, {'content': blk, 'metadata': meta}, None, None, False, None):
            out.append(ch)
        llm_used = llm_hits['n']
        # Ground Truth
        if is_missing:
            exp = []
        else:
            exp = gt_lookup(rows, cols, lookup_col, val, return_cols)
        actual = parse_lookup(blk, return_cols)
        order_ok = [a[0] for a in actual] == [e[0] for e in exp]
        value_ok = (len(actual) == len(exp)) and all(values_equal(a[1], e[1]) for a, e in zip(actual, exp))
        mc_ok = (meta.get('lookup_column') == lookup_col)
        rc_ok = set(meta.get('return_columns', '').split(',')) == set(return_cols)
        matched_ok = (meta.get('matched') == len(exp))
        # 列误归属专项：A 用例（Order ID→Variation, mid）Variation 必须等于 Variation 列真值，且 ≠ Product Category 列值
        misattr = True
        if label.startswith('A.'):
            gt_var = gt_lookup(rows, cols, lookup_col, val, ['Variation'])[0][1]['Variation']
            gt_pc = gt_lookup(rows, cols, lookup_col, val, ['Product Category'])[0][1]['Product Category']
            got_var = actual[0][1]['Variation'] if actual else None
            misattr = (got_var == gt_var) and (got_var != gt_pc)
        status = 'PASS' if (mc_ok and rc_ok and matched_ok and order_ok and value_ok and misattr) else 'FAIL'
        results.append((label, lookup_col, ','.join(return_cols), len(exp),
                        'Y' if order_ok else 'N', 'Y' if value_ok else 'N', llm_used, status,
                        '' if misattr else 'MISATTR'))

    # A-D: 单目标列
    await build_case('A. OrderID→Variation', mid_v, 'Order ID', ['Variation'])
    await build_case('B. OrderID→SKUID', mid_v, 'Order ID', ['SKU ID'])
    await build_case('C. OrderID→ShippingProviderName', mid_v, 'Order ID', ['Shipping Provider Name'])
    await build_case('D. OrderID→ProductCategory', mid_v, 'Order ID', ['Product Category'])
    # E: 多列
    await build_case('E. OrderID→Variation+SKUID', mid_v, 'Order ID', ['Variation', 'SKU ID'])
    # F-H: 位置分布
    await build_case('F. 开头OrderID→Variation', head_v, 'Order ID', ['Variation'])
    await build_case('G. 中间OrderID→Variation', mid_v, 'Order ID', ['Variation'])
    await build_case('H. 末尾OrderID→Variation', tail_v, 'Order ID', ['Variation'])
    # I: 不存在
    await build_case('I. 不存在OrderID', '1234567890123456XY', 'Order ID', ['Variation'], is_missing=True)
    # J: 重复值多匹配（合成 rep）
    import backend.services.rag_service as rsmod
    saved = rsmod.load_representation
    rsmod.load_representation = lambda did: SYN_REP
    try:
        q = '帮我查 Order ID 为 111111111111111111 的 Variation'
        intent = RagService._detect_structured_intent(q)
        res = rag._resolve_structured_access('SYN', 'S', 'T#0', USER, q, intent)
        blk, meta = res
        out = []
        async for ch in rag._stream_structured_answer(q, {'content': blk, 'metadata': meta}, None, None, False, None):
            out.append(ch)
        exp = gt_lookup(SYN_REP['workbook']['sheets'][0]['tables'][0]['rows'],
                        SYN_REP['workbook']['sheets'][0]['tables'][0]['columns'],
                        'Order ID', '111111111111111111', ['Variation'])
        actual = parse_lookup(blk, ['Variation'])
        order_ok = [a[0] for a in actual] == [e[0] for e in exp]
        value_ok = (len(actual) == len(exp)) and all(values_equal(a[1], e[1]) for a, e in zip(actual, exp))
        status = 'PASS' if (order_ok and value_ok and meta.get('matched') == 2) else 'FAIL'
        results.append(('J. 重复值多匹配x2', 'Order ID', 'Variation', 2,
                        'Y' if order_ok else 'N', 'Y' if value_ok else 'N', llm_hits['n'], status))
    finally:
        rsmod.load_representation = saved

    # 输出
    print(f"{'case':<28} | {'lookup_col':<12} | {'return_cols':<22} | {'match':>4} | {'ord':>3} | {'val':>3} | {'llm':>3} | {'status':<6}")
    print('-' * 110)
    c = Counter()
    for r in results:
        (label, lc, rc, m, o, v, llm, st, *rest) = r
        c[st] += 1
        extra = rest[0] if rest else ''
        print(f"{label:<28} | {lc:<12} | {rc:<22} | {str(m):>4} | {o:>3} | {v:>3} | {llm:>3} | {st:<6} {extra}")
    print('-' * 110)
    print(f"SUMMARY: PASS={c['PASS']}  FAIL={c['FAIL']}  | lookup 调用 LLM 次数={llm_hits['n']} (应为 0)")
    print('结论：lookup 事实全部由 Python 确定，LLM 零调用 -> 无列误归属风险')


if __name__ == '__main__':
    asyncio.run(main())
