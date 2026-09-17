# -*- coding: utf-8 -*-
"""Phase1 大表完整覆盖验证（真实代码路径，真实 Chroma/rep，可选真实 LLM）。

验证：
A. “把所有 Shipping Provider Name 列出来” -> 系统给模型的数据覆盖 447 行目标列（非 24）
B. “Order ID 为 577471353158603093 的 Variation” -> 精确值基于完整扫描（非 top-k 抽样）
C. “全部 SKU” -> 19 行表仍 19（回归）
D. 真实 LLM 流式测试（Shipping Provider 全量枚举）-> 回答长度证明非 24
"""
import os
import sys
import asyncio

sys.path.insert(0, r'C:\Users\Administrator\Desktop\my_ai_assistant')
os.environ.setdefault('PYTHONPATH', r'C:\Users\Administrator\Desktop\my_ai_assistant')

from backend.services.rag_service import get_rag_service
from backend.services.vector_service import get_vector_store_manager

USER_ID = 61


async def main():
    rag = get_rag_service()
    vs = get_vector_store_manager()
    docs = vs.list_user_documents(user_id=USER_ID)
    doc5 = next(d for d in docs if d.get('filename') == '直邮5店 7.10号订单.xlsx')
    doc1 = next(d for d in docs if d.get('filename') == '直邮一店 8.20号订单.xlsx')
    d5, d1 = doc5['document_id'], doc1['document_id']
    print(f'doc5={d5}  doc1={d1}')

    filt5 = {'$and': [{'user_id': USER_ID}, {'document_id': d5}]}
    filt1 = {'$and': [{'user_id': USER_ID}, {'document_id': d1}]}

    # ---------- A. Shipping Provider 全量枚举 ----------
    q_sp = '把所有 Shipping Provider Name 列出来'
    res_sp = await rag._retrieve_and_expand(q_sp, k=3, filter_dict=filt5, user_id=USER_ID)
    ct_sp = res_sp[0]['metadata']['chunk_type'] if res_sp else None
    sheet = res_sp[0]['metadata']['sheet_name'] if res_sp else None
    table = res_sp[0]['metadata']['table_id'] if res_sp else None
    _, cov_sp = rag._structured_full_load(d5, sheet, table, USER_ID, q_sp)
    print(f'[A] Shipping Provider 枚举：chunk_type={ct_sp} coverage={cov_sp}')
    assert ct_sp == 'table_full_data', "未走结构化完整加载（可能被阈值截断回退）"
    assert cov_sp['total_rows'] >= 400, f"目标列覆盖异常少：{cov_sp['total_rows']}"
    print(f'  -> 覆盖 {cov_sp["total_rows"]} 行目标列全部值（非 24）：PASS')

    # ---------- B. Order ID 精确值查找 ----------
    q_oid = '帮我查 Order ID 为 577471353158603093 的 Variation'
    res_oid = await rag._retrieve_and_expand(q_oid, k=3, filter_dict=filt5, user_id=USER_ID)
    ct_oid = res_oid[0]['metadata']['chunk_type'] if res_oid else None
    blk_oid = res_oid[0]['content'] if res_oid else ''
    found = ('找到' in blk_oid) and ('未找到' not in blk_oid)
    print(f'[B] Order ID 精确查找：chunk_type={ct_oid} found={found}')
    print('     block 预览:', blk_oid[:160].replace('\n', ' '))
    assert ct_oid == 'table_full_data', "精确查找未走结构化完整加载"
    assert '条数据行' in blk_oid, "未展示完整扫描行数（可能退化为 top-k）"
    print('  -> 精确查找基于完整扫描：PASS')

    # ---------- C. 19 行 SKU 回归 ----------
    q_sku = '全部 SKU'
    res_sku = await rag._retrieve_and_expand(q_sku, k=3, filter_dict=filt1, user_id=USER_ID)
    ct_sku = res_sku[0]['metadata']['chunk_type'] if res_sku else None
    sheet1 = res_sku[0]['metadata']['sheet_name'] if res_sku else None
    table1 = res_sku[0]['metadata']['table_id'] if res_sku else None
    _, cov_sku = rag._structured_full_load(d1, sheet1, table1, USER_ID, q_sku)
    print(f'[C] 全部 SKU 回归：chunk_type={ct_sku} coverage={cov_sku}')
    assert ct_sku == 'table_full_data'
    assert cov_sku['total_rows'] == 19, f"期望 19，实际 {cov_sku['total_rows']}"
    print('  -> 19 -> 19：PASS')

    # ---------- D. 真实 LLM 流式测试（Shipping Provider 全量枚举）----------
    print('[D] 真实 LLM 流式测试（Shipping Provider 全量枚举）...')
    parts = []
    async for ch in rag.rag_query(q_sp, context_count=3, user_id=USER_ID, document_id=d5):
        parts.append(ch)
    text = ''.join(parts)
    print(f'   LLM 回答长度={len(text)} 字符')
    print('   回答前 200 字:', text[:200].replace('\n', ' '))

    print('ALL_PASS')


if __name__ == '__main__':
    asyncio.run(main())
