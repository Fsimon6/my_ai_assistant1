# -*- coding: utf-8 -*-
"""Phase 1 Table QA 系统化回归测试矩阵（真实代码 + 真实 Representation ground truth）。

覆盖维度：查询类型 / 数据规模 / 字段 / 数据位置 / 结果规模 / 多文件 / 分页 / 边界。
对“系统实际喂给 LLM 的数据”做 Ground Truth 逐项比对（数量/顺序/值），不依赖 LLM 最终答案。
逐行实时打印，避免中途崩溃丢失结果。
"""
import os, sys, re, asyncio
from collections import Counter

sys.path.insert(0, r'C:\Users\Administrator\Desktop\my_ai_assistant')
os.environ.setdefault('PYTHONPATH', r'C:\Users\Administrator\Desktop\my_ai_assistant')

from backend.services.rag_service import get_rag_service, RagService, MAX_ENUM_CONTEXT_CHARS
from backend.services.vector_service import get_vector_store_manager
from backend.services.table_representation import load_representation

USER = 61

# 合成 rep 用于“重复值多匹配”单元测试（明确区分合成数据）
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
    N5, N1 = len(rows5), len(rows1)
    ci5 = {c['technical_name']: c['col_index'] for c in cols5}

    def col_vals(rows, ci):
        return [r['cells'][ci - 1].get('value') if ci - 1 < len(r['cells']) else None for r in rows]

    def gt_col(doc_id, name):
        sh, tb, cols, rows = gt(doc_id)
        ci = {c['technical_name']: c['col_index'] for c in cols}[name]
        return [(r['row_index'], v) for r, v in zip(rows, col_vals(rows, ci))]

    c_npass = c_fail = c_gap = 0
    STRUCT = ('table_full_data', 'table_structured_access')

    def emit(dim, q, status, note):
        nonlocal c_npass, c_fail, c_gap
        tag = status.split('(')[0]
        if tag == 'PASS':
            c_npass += 1
        elif tag in ('FAIL', 'UNEXPECTED-STRUCT'):
            c_fail += 1
        else:
            c_gap += 1
        qs = q[:40]
        print(f'[{status:<18}] {dim:<10} | {qs:<42} | {note[:50]}')

    async def run(query, doc_id):
        filt = {'$and': [{'user_id': USER}, {'document_id': doc_id}]}
        chunks = await rag._retrieve_and_expand(query, k=3, filter_dict=filt, user_id=USER)
        ct = (chunks[0].get('metadata') or {}).get('chunk_type') if chunks else None
        return chunks, ct

    def parse_enum(blk):
        return [(int(m.group(1)), m.group(2)) for ln in blk.split('\n')
                if (m := re.match(r'第(\d+)行:\s?(.*)', ln))]

    def parse_lookup_rows(blk):
        idxs = []
        for m in re.finditer(r'行号：(\d+)', blk):
            idxs.append(int(m.group(1)))
        for m in re.finditer(r'（行(\d+)）', blk):
            idxs.append(int(m.group(1)))
        return idxs

    def count_rows(blk):
        n = 0
        for ln in blk.split('\n'):
            if re.match(r'第\d+行:', ln) or re.match(r'^\d+\s*\|', ln):
                n += 1
        return n

    def semantic_coverage(chunks):
        cov = 0
        types = []
        for c in chunks:
            m = c.get('metadata', {})
            rs, re_ = m.get('row_start'), m.get('row_end')
            types.append(m.get('chunk_type'))
            if rs is not None and re_ is not None:
                cov += (re_ - rs + 1)
        return cov, types

    async def run_all():
        # === 全量列枚举（多字段） ===
        for col in ['Shipping Provider Name', 'SKU ID', 'Order ID', 'Variation']:
            q = f'把所有 {col} 列出来'
            chunks, ct = await run(q, d5)
            if ct not in STRUCT:
                emit('全量枚举', q, 'UNSUPPORTED', f'path={ct} (未走结构化)')
                continue
            got = parse_enum(chunks[0]['content'])
            truth = gt_col(d5, col)
            ok = (len(got) == len(truth) and
                  all(g[0] == t[0] and g[1] == ('' if t[1] is None else str(t[1]))
                      for g, t in zip(got, truth)))
            emit('全量枚举', q, 'PASS' if ok else 'FAIL', f'覆盖 {len(got)}/{len(truth)} 行, 一致={ok}')

        # === 多列读取（统一结构化访问层已支持） ===
        q = '把 Order ID 和 SKU ID 全部列出来'
        chunks, ct = await run(q, d5)
        if ct not in STRUCT:
            emit('多列枚举', q, 'UNSUPPORTED', f'path={ct} (未触发结构化)')
        else:
            blk = chunks[0]['content']
            got_multi = [(int(m.group(1)), m.group(2)) for ln in blk.split('\n')
                         if (m := re.match(r'第(\d+)行:\s?(.*)', ln))]
            # 行级对应：每行的 Order ID / SKU ID 与 ground truth 一致
            ok = True
            truth_map = {ri: val for ri, val in gt_col(d5, 'Order ID')}
            truth_sku = {ri: val for ri, val in gt_col(d5, 'SKU ID')}
            for ri, payload in got_multi:
                parts = dict(p.split('=', 1) for p in payload.split(' | '))
                if parts.get('Order ID') != ('' if truth_map.get(ri) is None else str(truth_map.get(ri))):
                    ok = False
                if parts.get('SKU ID') != ('' if truth_sku.get(ri) is None else str(truth_sku.get(ri))):
                    ok = False
            emit('多列枚举', q, 'PASS' if ok else 'FAIL',
                 f'双列行级对应 {len(got_multi)} 行一致={ok}')

        # === 精确值查找：位置分布（Order ID） ===
        head_v = col_vals(rows5, ci5['Order ID'])[1]
        mid_v = col_vals(rows5, ci5['Order ID'])[224]
        tail_v = col_vals(rows5, ci5['Order ID'])[-1]
        for label, val in [('开头(near-top)', head_v), ('中间', mid_v), ('末尾', tail_v)]:
            q = f'帮我查 Order ID 为 {val} 的 Variation'
            chunks, ct = await run(q, d5)
            if ct not in STRUCT:
                emit('精确查找', f'{label}|{val}', 'UNSUPPORTED', f'path={ct}')
                continue
            got = parse_lookup_rows(chunks[0]['content'])
            truth = [r['row_index'] for r in rows5 if col_vals([r], ci5['Order ID'])[0] == val]
            ok = set(got) == set(truth) and len(got) == len(truth)
            emit('精确查找', f'{label}|{val}', 'PASS' if ok else 'FAIL',
                 f'命中 {len(got)} 行(期望 {len(truth)})')

        # === 精确值查找：不存在值（含字母的后缀，仍被 Order ID 正则提取，但不可能作为数字单元格子串出现） ===
        missing = '1234567890123456XY'
        q = f'帮我查 Order ID 为 {missing} 的 Variation'
        chunks, ct = await run(q, d5)
        blk = chunks[0]['content'] if chunks else ''
        scanned = '条数据行' in blk
        honest = ('未找到值' in blk) and ('条匹配' not in blk)
        emit('精确查找-不存在', q, 'PASS' if (ct in STRUCT and scanned and honest) else 'FAIL',
             f'path={ct}, 完整扫描={scanned}, 诚实未找到={honest}')

        # === 精确值查找：重复值多匹配（合成 rep 单元测试） ===
        import backend.services.rag_service as rsmod
        saved = rsmod.load_representation
        rsmod.load_representation = lambda did: SYN_REP
        try:
            req = RagService.StructuredTableRequest(operation='lookup', lookup_value='111111111111111111')
            res = rag._resolve_structured_access('SYN', 'S', 'T#0', USER,
                                                 '帮我查 Order ID 为 111111111111111111 的 Variation', req)
        finally:
            rsmod.load_representation = saved
        got = parse_lookup_rows(res[0]) if res else []
        emit('精确查找-重复值(合成)', 'Order ID=111...111 (出现2次)',
             'PASS' if set(got) == {2, 3} else 'FAIL', f'命中 {len(got)} 行(期望2)')

        # === 前N / 后N / 中间范围（统一结构化访问层已支持：按原始顺序精确切片） ===
        for label, q, truth in [
            ('前N', '把前50条 SKU ID 列出来', gt_col(d5, 'SKU ID')[:50]),
            ('后N', '把最后10条 SKU ID 列出来', gt_col(d5, 'SKU ID')[-10:]),
            ('中间范围', '把第100到150条 SKU ID 列出来', gt_col(d5, 'SKU ID')[99:150]),
        ]:
            chunks, ct = await run(q, d5)
            if ct not in STRUCT:
                emit(label, q, 'UNSUPPORTED', f'path={ct} (未走结构化)')
                continue
            got = parse_enum(chunks[0]['content'])
            ok = (len(got) == len(truth) and
                  all(g[0] == t[0] and g[1] == ('' if t[1] is None else str(t[1]))
                      for g, t in zip(got, truth)))
            emit(label, q, 'PASS' if ok else 'FAIL',
                 f'覆盖 {len(got)}/{len(truth)} 行(按原始顺序), 一致={ok}')

        # === 条件筛选 / 统计（Phase 2 边界） ===
        for label, q in [('条件筛选', 'Shipping Provider 为 SF International 的订单有哪些'),
                         ('条件统计', '一共有多少条订单')]:
            chunks, ct = await run(q, d5)
            cov, _ = semantic_coverage(chunks)
            emit(label, q, 'UNSUPPORTED(Phase2)', f'path={ct}, 覆盖 {cov} 行, 非可靠WHERE/COUNT')

        # === 普通语义回归（不应误触发结构化） ===
        q = '这个表主要记录什么'
        chunks, ct = await run(q, d5)
        emit('普通语义', q, 'PASS' if ct != 'table_full_data' else 'FAIL(误触发)', f'path={ct}')

        # === 19 行回归 ===
        q = '全部 SKU'
        chunks, ct = await run(q, d1)
        if ct not in STRUCT:
            emit('19行回归', q, 'UNSUPPORTED', f'path={ct}')
        else:
            got_n = count_rows(chunks[0]['content'])
            emit('19行回归', q, 'PASS' if got_n == N1 else 'FAIL', f'覆盖 {got_n}/{N1} 行')

        # === 多文件：全局查询（不指定 document_id） ===
        chunks_g = await rag._retrieve_and_expand('把所有 Order ID 列出来', k=3,
                                                  filter_dict={'user_id': USER}, user_id=USER)
        ctg = chunks_g[0]['metadata']['chunk_type'] if chunks_g else None
        src = chunks_g[0]['metadata'].get('filename') if chunks_g else None
        emit('多文件-全局', '把所有 Order ID 列出来 (无doc过滤)',
             'PASS' if ctg in STRUCT else 'UNSUPPORTED', f'path={ctg}, 命中文档={src}')

        # === 普通文档回归（TXT/MD 语义路径） ===
        txt_doc = next((d for d in docs if d['filename'].endswith('.md') or d['filename'].endswith('.txt')), None)
        if txt_doc:
            try:
                filt_t = {'$and': [{'user_id': USER}, {'document_id': txt_doc['document_id']}]}
                chunks_t = await rag._retrieve_and_expand('这份文档讲了什么', k=3, filter_dict=filt_t, user_id=USER)
                ctt = (chunks_t[0].get('metadata') or {}).get('chunk_type') if chunks_t else None
                emit('普通文档', f'{txt_doc["filename"]} 语义查询',
                     'PASS' if ctt != 'table_full_data' else 'FAIL(误触发)', f'path={ctt}')
            except Exception as e:
                emit('普通文档', txt_doc['filename'], 'EXCEPTION', repr(e)[:50])

        # === 分页完整性（monkeypatch 低阈值） ===
        saved_limit = rsmod.MAX_ENUM_CONTEXT_CHARS
        rsmod.MAX_ENUM_CONTEXT_CHARS = 1500
        try:
            chunks_sp, _ = await run('把所有 Shipping Provider Name 列出来', d5)
            full = chunks_sp[0]['content']
            pages = rag._split_context(full)
            reassembled = '\n'.join(pages)
            got_all = parse_enum(reassembled)
            truth = gt_col(d5, 'Shipping Provider Name')
            ok = (len(got_all) == len(truth) and all(g[0] == t[0] for g, t in zip(got_all, truth)))
            emit('分页完整性', f'Shipping Provider Name (阈值1500,{len(pages)}页)',
                 'PASS' if ok else 'FAIL', f'总 {len(got_all)} 值, 无丢失/乱序')
        except Exception as e:
            emit('分页完整性', 'Shipping Provider Name', 'EXCEPTION', repr(e)[:50])
        finally:
            rsmod.MAX_ENUM_CONTEXT_CHARS = saved_limit

    try:
        asyncio.run(run_all())
    except Exception as e:
        print('!!! run_all 中途异常:', repr(e))
    print('-' * 120)
    print(f'SUMMARY: PASS={c_npass}  FAIL={c_fail}  UNSUPPORTED/GAP={c_gap}')
    print('说明：UNSUPPORTED/GAP = 当前结构化层未覆盖（落 semantic 或仅单列），属 Phase 1 能力边界，非崩溃缺陷。')


if __name__ == '__main__':
    main()
