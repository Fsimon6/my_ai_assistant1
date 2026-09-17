# -*- coding: utf-8 -*-
"""Phase1 真实上传验证（真实 DashScope embedding + 真实 Chroma 入库）。

需在后端停止时运行（避免两个进程同时写同一个 Chroma 持久目录）。
验证：
B. 448 chunks 真实 XLSX 解析->embedding->Chroma 入库->成功
C. Chroma 实际写入数量 == total_chunks
D. 不是只写入前 20 个
E. chunk_index 顺序连续（无丢序/丢块）
回归：已存在的 19 行文档 Table-aware Retrieval 仍为 19
"""
import os
import sys
import asyncio
import shutil
import tempfile

sys.path.insert(0, r'C:\Users\Administrator\Desktop\my_ai_assistant')
os.environ.setdefault('PYTHONPATH', r'C:\Users\Administrator\Desktop\my_ai_assistant')

SRC = r'C:\Users\Administrator\Desktop\my_ai_assistant\直邮5店 7.10号订单.xlsx'
USER_ID = 61

from backend.services.rag_service import get_rag_service
from backend.services.vector_service import get_vector_store_manager


async def main():
    rag = get_rag_service()
    vs = get_vector_store_manager()

    # 复制源文件到临时位置，避免移动项目根目录原件
    tmp = tempfile.mktemp(suffix='.xlsx')
    shutil.copy2(SRC, tmp)
    fsize = os.path.getsize(tmp)

    print('=== 真实上传（解析 + embedding + Chroma 入库）===')
    # 若同名文档已存在（避免重复验证时重复写入），直接复用
    existing = next((d for d in vs.list_user_documents(user_id=USER_ID)
                     if d.get('filename') == '直邮5店 7.10号订单.xlsx'), None)
    if existing:
        doc_id = existing['document_id']
        chunks0 = await vs.get_document_chunks(doc_id, user_id=USER_ID)
        total = len(chunks0)
        print(f'复用已上传文档 document_id={doc_id} total_chunks={total}')
    else:
        res = await rag.process_and_store_document(
            tmp, {}, USER_ID,
            original_filename='直邮5店 7.10号订单.xlsx',
            file_size=fsize,
        )
        print('upload result:', {k: res.get(k) for k in ('success', 'total_chunks', 'document_id', 'filename', 'error')})
        assert res.get('success'), f"上传失败: {res.get('error')}"
        total = res['total_chunks']
        doc_id = res['document_id']

    chunks = await vs.get_document_chunks(doc_id, user_id=USER_ID)
    print(f'Chroma 实际写入 chunk 数: {len(chunks)} (期望 {total})')
    assert len(chunks) == total, f"Chroma 写入数({len(chunks)}) != total_chunks({total})"
    assert len(chunks) > 20, "疑似只写入了前 20 个"
    idxs = sorted(c['index'] for c in chunks)
    assert idxs == list(range(total)), f"chunk index 不连续: {idxs[:5]}..{idxs[-5:]}"
    print('顺序校验通过：chunk_index 连续覆盖 0..%d' % (total - 1))

    # 回归：已存在 19 行文档 Table-aware Retrieval 是否仍为 19
    docs = vs.list_user_documents(user_id=USER_ID)
    doc19 = next((d for d in docs if d.get('filename') == '直邮一店 8.20号订单.xlsx'), None)
    if doc19:
        d19 = doc19['document_id']
        # 生产路径会用 $and 包裹多条件；这里复刻同样的 filter（等价于 rag_query 内部构造）
        filter_dict = {'$and': [{'user_id': USER_ID}, {'document_id': d19}]}
        expanded = await rag._retrieve_and_expand(
            query='请把这个表里的全部 SKU 都提取出来',
            k=3,
            filter_dict=filter_dict,
            user_id=USER_ID,
        )
        rg = [c for c in expanded if c['metadata'].get('chunk_type') == 'row_group']
        cover = sum((c['metadata'].get('row_end', 0) - c['metadata'].get('row_start', 0) + 1) for c in rg)
        print(f'Table-aware 回归：19行文档 row_group 数={len(rg)}，行覆盖={cover}')
        assert cover == 19, f"Table-aware 行覆盖={cover} != 19"
        print('Table-aware Retrieval 回归 PASS（19 行）')
    else:
        print('（未找到 19 行文档，跳过 Table-aware 回归）')

    print('REAL_UPLOAD_RESULT: PASS')


if __name__ == '__main__':
    asyncio.run(main())
