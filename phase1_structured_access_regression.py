# -*- coding: utf-8 -*-
"""Phase 1 Structured Table Access 回归测试（统一层：全量/前N/后N/范围/多列/精确查找）。

对“系统实际返回给用户的结构化数据”逐项与真实 Representation (ground truth) 比对：
数量 / 顺序 / 值 / 行级对应。覆盖正常规模 + 边界（N>总行数 / 越界范围 / 前0条 / start>end）。
输出列：query | operation | document | sheet | columns | expected_count | actual_count |
       range | order_ok | value_match | status
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


def values_equal(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return a == b
    return str(a) == str(b)


def parse_actual(blk):
    rows = []  # (row_index, payload)  payload: str | dict | list
    for ln in blk.split('\n'):
        m = re.match(r'第(\d+)行:\s?(.*)', ln)
        if m:
            ri = int(m.group(1))
            payload = m.group(2)
            if '=' in payload and ' | ' in payload and re.match(r'^[^=]+=', payload):
                d = {}
                for part in payload.split(' | '):
                    k, v = part.split('=', 1)
                    d[k.strip()] = v
                rows.append((ri, d))
            else:
                rows.append((ri, payload))
            continue
        m = re.match(r'^(\d+)\s*\|\s*(.*)', ln)
        if m:
            ri = int(m.group(1))
            vals = [x.strip() for x in m.group(2).split(' | ')]
            rows.append((ri, vals))
    return rows


def expected(operation, rows, columns, col_names, limit=None, start=None, end=None):
    if operation == 'first_n':
        sliced = rows[:limit] if limit and limit > 0 else []
    elif operation == 'last_n':
        sliced = rows[-limit:] if limit and limit > 0 else []
    elif operation == 'range':
        s, e = (start, end)
        if s > e:
            s, e = e, s
        sliced = rows[s - 1:e]
    else:  # full_column
        sliced = rows
    ncols = len(columns)

    def row_vals(r):
        # 与系统 _format_rows / _cell_text 保持一致：按列数补齐/截断，缺失填 ''，合并单元格内换行/制表
        vals = []
        for i in range(ncols):
            cell = r['cells'][i] if i < len(r['cells']) else None
            v = cell.get('value') if cell else None
            vals.append(RagService._cell_text(v))
        return vals

    if not col_names:
        exp = [(r['row_index'], row_vals(r)) for r in sliced]
        return exp, len(exp)
    ci_map = {name: next(c['col_index'] for c in columns if c['technical_name'] == name) for name in col_names}
    if len(col_names) == 1:
        ci = ci_map[col_names[0]]
        exp = [(r['row_index'], RagService._cell_text(
            r['cells'][ci - 1].get('value') if (ci - 1) < len(r['cells']) else None)) for r in sliced]
        return exp, len(exp)
    exp = []
    for r in sliced:
        d = {}
        for name in col_names:
            ci = ci_map[name]
            v = r['cells'][ci - 1].get('value') if ci - 1 < len(r['cells']) else None
            d[name] = RagService._cell_text(v)
        exp.append((r['row_index'], d))
    return exp, len(exp)


def expected_lookup(rows, columns, value, return_cols=None):
    """Python ground truth：扫描匹配 value（精确归一化匹配），仅提取 return_cols（缺省=全部列）。"""
    matched = []
    for r in rows:
        for cell in r['cells']:
            v = cell.get('value')
            if v is None:
                continue
            if RagService._norm(str(v)) == RagService._norm(value):
                matched.append(r)
                break
    cols_by_name = {c['technical_name']: c for c in columns}
    out = return_cols if return_cols else [c['technical_name'] for c in columns]
    exp = []
    for r in matched:
        d = {}
        for cn in out:
            co = cols_by_name.get(cn)
            ci = co['col_index'] - 1 if co else None
            cells = r.get('cells', [])
            v = cells[ci].get('value') if (co and ci < len(cells)) else None
            d[cn] = RagService._cell_text(v)
        exp.append((r['row_index'], d))
    return exp, len(exp)


def parse_lookup(blk, return_cols):
    """解析新 lookup 块（Python 直出格式）为 [(row_index, {col: value})]。"""
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
            return []  # 未找到：无行且无任何列值
        rows.append((ri, d))
    return rows


def main():
    rag = get_rag_service()
    vs = get_vector_store_manager()
    docs = vs.list_user_documents(user_id=USER)
    doc5 = next(d for d in docs if '5店' in d['filename'])
    doc1 = next(d for d in docs if '一店' in d['filename'])
    d5, d1 = doc5['document_id'], doc1['document_id']

    def gt(doc_id):
        rep = load_representation(doc_id)
        s = rep['workbook']['sheets'][0]
        t = s['tables'][0]
        return s['sheet_name'], t['table_id'], t['columns'], t['rows']

    sh5, tb5, cols5, rows5 = gt(d5)
    sh1, tb1, cols1, rows1 = gt(d1)
    N5 = len(rows5)

    results = []

    async def run_case(query, doc_id, operation, col_names, limit=None, start=None, end=None,
                       lookup_value=None, sheet='', columns_disp='', rng=''):
        filt = {'$and': [{'user_id': USER}, {'document_id': doc_id}]}
        chunks = await rag._retrieve_and_expand(query, k=3, filter_dict=filt, user_id=USER)
        ct = chunks[0]['metadata']['chunk_type'] if chunks else None
        if ct != 'table_structured_access':
            results.append((query, operation, doc_id, sheet, columns_disp, '-', '-', rng,
                            '-', '-', f'FAIL(path={ct})'))
            return
        blk = chunks[0]['content']
        if lookup_value is not None:
            cols = cols5 if doc_id == d5 else cols1
            rows = rows5 if doc_id == d5 else rows1
            # 与系统一致的 return_columns 解析（仅依赖真实列名，不写死列序号）
            _, return_cols = rag._resolve_lookup_targets(query, cols, lookup_value)
            actual = parse_lookup(blk, return_cols if return_cols else [c['technical_name'] for c in cols])
            exp_rows, exp_count = expected_lookup(rows, cols, lookup_value, return_cols)
        else:
            actual = parse_actual(blk)
            rows = rows5 if doc_id == d5 else rows1
            cols = cols5 if doc_id == d5 else cols1
            exp_rows, exp_count = expected(operation, rows, cols, col_names, limit, start, end)
        act_count = len(actual)
        order_ok = [a[0] for a in actual] == [e[0] for e in exp_rows]
        value_ok = (len(actual) == len(exp_rows)) and all(values_equal(a[1], e[1]) for a, e in zip(actual, exp_rows))
        status = 'PASS' if (act_count == exp_count and order_ok and value_ok) else 'FAIL'
        results.append((query, operation, doc_id, sheet, columns_disp, exp_count, act_count, rng,
                        order_ok, value_ok, status))

    async def run_all():
        # ---- 全量单列 ----
        for col in ['Shipping Provider Name', 'SKU ID', 'Order ID', 'Variation']:
            await run_case(f'把所有 {col} 列出来', d5, 'full_column', [col],
                           sheet=sh5, columns_disp=col, rng='1-448')

        # ---- 前N ----
        for n in [1, 5, 20, 50, 100]:
            await run_case(f'把前{n}条 SKU ID 列出来', d5, 'first_n', ['SKU ID'], limit=n,
                           sheet=sh5, columns_disp='SKU ID', rng=f'1-{n}')
        # 边界：N > 总行数（不得伪造）
        await run_case('把前1000条 SKU ID 列出来', d5, 'first_n', ['SKU ID'], limit=1000,
                       sheet=sh5, columns_disp='SKU ID', rng=f'1-{N5}(clamp)')
        # 边界：前0条
        await run_case('把前0条 SKU ID 列出来', d5, 'first_n', ['SKU ID'], limit=0,
                       sheet=sh5, columns_disp='SKU ID', rng='empty')

        # ---- 后N ----
        await run_case('把最后10条 SKU ID 列出来', d5, 'last_n', ['SKU ID'], limit=10,
                       sheet=sh5, columns_disp='SKU ID', rng=f'{N5-9}-{N5}')

        # ---- 中间范围 ----
        await run_case('把第100到150条 SKU ID 列出来', d5, 'range', ['SKU ID'], start=100, end=150,
                       sheet=sh5, columns_disp='SKU ID', rng='100-150')
        # 边界：end 越界
        await run_case('把第400到500条 SKU ID 列出来', d5, 'range', ['SKU ID'], start=400, end=500,
                       sheet=sh5, columns_disp='SKU ID', rng='400-500(clamp)')
        # 边界：start>end
        await run_case('把第150到100条 SKU ID 列出来', d5, 'range', ['SKU ID'], start=150, end=100,
                       sheet=sh5, columns_disp='SKU ID', rng='150>100(swap)')

        # ---- 多列 ----
        await run_case('把所有订单的 Order ID 和 SKU ID 列出来', d5, 'full_column', ['Order ID', 'SKU ID'],
                       sheet=sh5, columns_disp='Order ID,SKU ID', rng='1-448')
        await run_case('把前20条 Order ID 和 SKU ID 列出来', d5, 'first_n', ['Order ID', 'SKU ID'], limit=20,
                       sheet=sh5, columns_disp='Order ID,SKU ID', rng='1-20')

        # ---- 精确值查找：位置分布 ----
        ci_oid = {c['technical_name']: c['col_index'] for c in cols5}['Order ID']
        oid_vals = [r['cells'][ci_oid - 1].get('value') for r in rows5]
        head_v, mid_v, tail_v = oid_vals[1], oid_vals[224], oid_vals[-1]
        for label, val in [('开头', head_v), ('中间', mid_v), ('末尾', tail_v)]:
            await run_case(f'帮我查 Order ID 为 {val} 的 Variation', d5, 'lookup', None,
                           lookup_value=val, sheet=sh5, columns_disp='Order ID', rng=label)
        # 不存在值
        await run_case('帮我查 Order ID 为 1234567890123456XY 的 Variation', d5, 'lookup', None,
                       lookup_value='1234567890123456XY', sheet=sh5, columns_disp='Order ID', rng='不存在')

        # ---- 合成重复值多匹配（明确标记 synthetic） ----
        import backend.services.rag_service as rsmod
        saved = rsmod.load_representation
        rsmod.load_representation = lambda did: SYN_REP
        try:
            req = RagService.StructuredTableRequest(operation='lookup', lookup_value='111111111111111111')
            res = rag._resolve_structured_access('SYN', 'S', 'T#0', USER,
                                                 '帮我查 Order ID 为 111111111111111111 的 Variation', req)
        finally:
            rsmod.load_representation = saved
        if res is None:
            results.append(('[synthetic] Order ID=111...111 x2', 'lookup', 'SYN', 'S', 'Order ID',
                            '-', '-', 'repeat', '-', '-', 'FAIL(res=None)'))
        else:
            actual = parse_lookup(res[0], ['Variation'])
            exp_rows, exp_count = expected_lookup(SYN_REP['workbook']['sheets'][0]['tables'][0]['rows'],
                                                  SYN_REP['workbook']['sheets'][0]['tables'][0]['columns'],
                                                  '111111111111111111', ['Variation'])
            order_ok = [a[0] for a in actual] == [e[0] for e in exp_rows]
            value_ok = all(values_equal(a[1], e[1]) for a, e in zip(actual, exp_rows))
            status = 'PASS' if (len(actual) == exp_count and order_ok and value_ok) else 'FAIL'
            results.append(('[synthetic] Order ID=111...111 x2', 'lookup', 'SYN', 'S', 'Order ID',
                            exp_count, len(actual), 'repeat', order_ok, value_ok, status))

        # ---- 19 行文档 ----
        await run_case('把所有 SKU ID 列出来', d1, 'full_column', ['SKU ID'], sheet=sh1, columns_disp='SKU ID', rng='1-19')
        await run_case('把前5条 SKU ID 列出来', d1, 'first_n', ['SKU ID'], limit=5,
                       sheet=sh1, columns_disp='SKU ID', rng='1-5')

    asyncio.run(run_all())

    # 输出
    print(f"{'query':<46} | {'op':<10} | {'doc':<10} | {'sheet':<12} | {'cols':<18} | {'exp':>4} | {'act':>4} | {'range':<14} | {'order':<5} | {'value':<5} | {'status':<8}")
    print('-' * 200)
    c = Counter()
    for r in results:
        (q, op, doc, sheet, cols, exp, act, rng, order_ok, value_ok, status) = r
        c[status.split('(')[0]] += 1
        print(f"{q[:45]:<46} | {op:<10} | {doc[:9]:<10} | {sheet:<12} | {cols:<18} | {str(exp):>4} | {str(act):>4} | {rng:<14} | {str(order_ok):<5} | {str(value_ok):<5} | {status:<8}")
    print('-' * 200)
    print(f"SUMMARY: PASS={c['PASS']}  FAIL={c['FAIL']}")


def get_vector_store_manager_safe():
    from backend.services.vector_service import get_vector_store_manager
    return get_vector_store_manager()


def get_vector_store_manager_safe():
    from backend.services.vector_service import get_vector_store_manager
    return get_vector_store_manager()


if __name__ == '__main__':
    main()
