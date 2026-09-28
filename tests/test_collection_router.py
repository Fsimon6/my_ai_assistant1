# -*- coding: utf-8 -*-
"""collection 路由基础设施单元测试（架构 C 地基）。

- 临时 Chroma：所有“写入/路由”用例使用 tempfile，绝不触碰正式 data/chroma_db。
- 假 embedding：确定性 1024 维，避免真实网络 / quota 消耗。
- 真实库只读：仅对正式 ai_assistant_docs 做 count 只读校验（=271），不写入。
"""
import sys
import os
import re
import random
import hashlib
import asyncio
import tempfile
from pathlib import Path

# 让 backend 包可导入
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.embeddings import Embeddings  # type: ignore

import backend.services.vector_service as vs


def run(coro):
    """VectorStoreManager 的向量操作方法均为 async，测试中以 asyncio.run 驱动。"""
    return asyncio.run(coro)


class FakeEmbeddings(Embeddings):
    """确定性 1024 维假 embedding，仅用于路由测试（无网络 / 无 quota）。"""

    def __init__(self, embedding_model=None):
        self.embedding_model = embedding_model or 'text-embedding-v3'

    def _vec(self, text):
        seed = int.from_bytes(
            hashlib.md5(f'{self.embedding_model}|{text}'.encode()).digest()[:8], 'big'
        )
        rng = random.Random(seed)
        return [rng.random() for _ in range(1024)]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


# 在实例化 VectorStoreManager 之前完成 patch
vs.AIAssistantEmbeddings = FakeEmbeddings

from backend.services.vector_service import VectorStoreManager  # noqa: E402

GLOBAL_MODEL = 'text-embedding-v3'
CUSTOM_MODEL = 'qwen3.7-text-embedding-flash'
OTHER_MODEL = 'deepseek-v3.2'

PASS = 0
FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  [PASS] {name}')
    else:
        FAIL += 1
        print(f'  [FAIL] {name}  {detail}')


def make_chunks(prefix, n, user_id, model, doc_id):
    chunks = []
    for i in range(n):
        chunks.append({
            'id': f'{doc_id}_{i}',
            'content': f'{prefix} chunk {i}',
            'metadata': {
                'document_id': doc_id,
                'user_id': user_id,
                'chunk_index': i,
                'filename': f'{doc_id}.txt',
                'indexed_embedding_model': model,
            },
        })
    return chunks


def main():
    print('=== collection router 路由测试（临时 Chroma）===')
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        m = VectorStoreManager(tmp)

        # Test 1：全局模型 → ai_assistant_docs
        store, emb = m._resolve_store(GLOBAL_MODEL)
        check('T1 全局模型映射到 ai_assistant_docs',
              store is m.vector_store and store._collection.name == 'ai_assistant_docs',
              store._collection.name)
        check('T1 默认(None)也映射到 legacy',
              m._resolve_store(None)[0] is m.vector_store)

        # Test 2 / 13：safe collection name
        name_custom = m._safe_collection_name(CUSTOM_MODEL)
        expected = 'ai_assistant_docs__qwen3_7_text_embedding_flash'
        check('T2/T13 safe name = %s' % expected, name_custom == expected, name_custom)
        check('T13 仅含合法字符 [A-Za-z0-9_-]',
              bool(re.fullmatch(r'[A-Za-z0-9_-]+', name_custom)), name_custom)
        check('T13 同名重复调用稳定', m._safe_collection_name(CUSTOM_MODEL) == name_custom)
        check('T13 deepseek-v3.2 稳定',
              m._safe_collection_name(OTHER_MODEL) == 'ai_assistant_docs__deepseek_v3_2',
              m._safe_collection_name(OTHER_MODEL))
        check('T13 全局模型 safe name（自定义风格；但路由仍走 legacy）',
              m._safe_collection_name(GLOBAL_MODEL) == 'ai_assistant_docs__text_embedding_v3',
              m._safe_collection_name(GLOBAL_MODEL))

        # Test 4：不同模型 → 不同 collection
        s_custom = m._resolve_store(CUSTOM_MODEL)[0]
        s_other = m._resolve_store(OTHER_MODEL)[0]
        check('T4 不同模型 → 不同 collection 对象',
              s_custom is not s_other and s_custom._collection.name != s_other._collection.name,
              f'{s_custom._collection.name} vs {s_other._collection.name}')

        # Test 3：同模型不同 user → 相同 collection
        run(m.add_documents(make_chunks('u153', 3, 153, CUSTOM_MODEL, 'docA'),
                            embedding_model=CUSTOM_MODEL))
        run(m.add_documents(make_chunks('u163', 2, 163, CUSTOM_MODEL, 'docB'),
                            embedding_model=CUSTOM_MODEL))
        check('T3 同模型两 user 路由到同一 collection（共 5 向量）',
              m._resolve_store(CUSTOM_MODEL)[0]._collection.count() == 5,
              f'cust_count={m._resolve_store(CUSTOM_MODEL)[0]._collection.count()}')

        # Test 5：add_documents custom → 只写 custom collection
        cust_count = s_custom._collection.count()
        legacy_count = m.vector_store._collection.count()
        check('T5 custom 集合写入数量正确', cust_count == 5, f'cust={cust_count}')
        check('T5 legacy 集合未被污染', legacy_count == 0, f'legacy={legacy_count}')

        # Test 6：search custom → 只查 custom collection
        res = run(m.search('u153 chunk 0', k=5, filter_dict={'user_id': 153},
                           embedding_model=CUSTOM_MODEL))
        check('T6 custom 搜索返回本集合结果', len(res) >= 1 and
              res[0]['metadata'].get('user_id') == 153, f'res={len(res)}')
        res_global = run(m.search('u153 chunk 0', k=5, filter_dict={'user_id': 153},
                                 embedding_model=GLOBAL_MODEL))
        check('T6 全局集合(空)搜索返回空', len(res_global) == 0, f'res={len(res_global)}')

        # Test 8：reindex_document custom → 写 custom collection
        before = s_custom._collection.count()
        run(m.reindex_document(
            make_chunks('reidx', 4, 153, CUSTOM_MODEL, 'docC'),
            embedding_model=CUSTOM_MODEL, batch_size=2))
        after = s_custom._collection.count()
        check('T8 reindex 写入 custom 集合', after - before == 4, f'{before}->{after}')

        # Test 9：list/get chunk 能读 custom 集合
        docs = m.list_user_documents(user_id=153)
        ids = {d['document_id'] for d in docs}
        check('T9 list_user_documents 覆盖 custom 文档',
              {'docA', 'docC'}.issubset(ids), f'ids={ids}')
        chunks = run(m.get_document_chunks('docA', user_id=153))
        check('T9 get_document_chunks 读取 custom 文档', len(chunks) == 3, f'n={len(chunks)}')
        tg = m.get_table_row_groups('docA', user_id=153)
        check('T9 get_table_row_groups 跨集合读取(空表)', isinstance(tg, list))

        # Test 10：user_id isolation（同 collection 内）
        docs163 = m.list_user_documents(user_id=163)
        check('T10 user163 只看到自己文档',
              all(d['document_id'] == 'docB' for d in docs163) and len(docs163) == 1,
              f'{[d["document_id"] for d in docs163]}')
        cross = run(m.get_document_chunks('docA', user_id=163))
        check('T10 跨 user 读取被隔离', len(cross) == 0, f'n={len(cross)}')
        sres = run(m.search('u153 chunk 0', k=5, filter_dict={'user_id': 163},
                            embedding_model=CUSTOM_MODEL))
        check('T10 搜索按 user_id 隔离（仅返回 user163 数据）',
              len(sres) >= 1 and all(r['metadata'].get('user_id') == 163 for r in sres),
              f'res={len(sres)}')

        # Test 7：delete_documents custom → 只删 custom 集合
        leg_before = m.vector_store._collection.count()
        cust_before = s_custom._collection.count()
        deleted = run(m.delete_documents(['docA'], user_id=153))
        check('T7 删除数量正确', deleted == 3, f'deleted={deleted}')
        check('T7 custom 集合减少', s_custom._collection.count() == cust_before - 3)
        check('T7 legacy 集合不受影响', m.vector_store._collection.count() == leg_before)

        # 自定义集合 metadata 记录 embedding_model（供跨进程恢复）
        meta = m._client.get_collection(name=name_custom).metadata or {}
        check('自定义集合 metadata 记录 embedding_model',
              meta.get('embedding_model') == CUSTOM_MODEL, f'meta={meta}')

        print('')
        print('=== 正式库只读校验（data/chroma_db，不写入）===')
        real_path = ROOT / 'data' / 'chroma_db'
        if real_path.exists():
            try:
                rm = VectorStoreManager(str(real_path))
                info = rm.get_collection_info()
                n = info.get('total_documents', 0)
                check('T12 正式 ai_assistant_docs count == 271', n == 271, f'count={n}')
                print(f'  正式库信息: {info}')
            except Exception as e:
                check('T12 正式库只读校验', False, f'异常={e}')
        else:
            print(f'  [SKIP] 正式库路径不存在: {real_path}')

    print('')
    print(f'=== 结果: PASS={PASS} FAIL={FAIL} ===')
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
