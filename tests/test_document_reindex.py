# -*- coding: utf-8 -*-
"""document-level 显式 reindex workflow 单元测试（不联网、不触碰真实 Chroma 写入）。

- 临时 Chroma：所有写入/路由用例使用 tempfile，绝不触碰正式 data/chroma_db（仅只读 count/get）。
- 假 embedding：确定性 1024 维（与 test_collection_router 风格一致），避免真实网络 / quota 消耗。
- 正式库：仅对 ai_assistant_docs 做 count 只读校验（=271），并用 58ece85 作为只读 source 验证
  Excel metadata / content 完整保留（读取后写入「临时」custom collection，不写正式库）。
"""
import sys
import os
import re
import hashlib
import asyncio
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.embeddings import Embeddings  # type: ignore

import backend.services.vector_service as vs
from backend.config.settings import settings


def run(coro):
    """VectorStoreManager 的向量操作方法均为 async，测试中以 asyncio.run 驱动。"""
    return asyncio.run(coro)


class FakeEmbeddings(Embeddings):
    """确定性 1024 维假 embedding（无网络 / 无 quota）。"""

    def __init__(self, embedding_model=None):
        self.embedding_model = embedding_model or 'text-embedding-v3'

    def _vec(self, text):
        seed = int.from_bytes(
            hashlib.md5(f'{self.embedding_model}|{text}'.encode()).digest()[:8], 'big'
        )
        import random
        rng = random.Random(seed)
        return [rng.random() for _ in range(1024)]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


# 必须在实例化 VectorStoreManager 之前完成 patch
vs.AIAssistantEmbeddings = FakeEmbeddings

from backend.services.vector_service import (  # noqa: E402
    VectorStoreManager,
    ReindexError,
    ReindexDocumentNotFound,
    ReindexSourceNotFound,
    ReindexPermissionDenied,
)

GLOBAL_MODEL = 'text-embedding-v3'
CUSTOM_MODEL = 'qwen3.7-text-embedding-flash'
EXPECTED_CUSTOM_COLLECTION = 'ai_assistant_docs__qwen3_7_text_embedding_flash'

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


def fe_embed(texts):
    return FakeEmbeddings().embed_documents(texts)


def clean_meta(meta):
    """chromadb 不接受 None 的 metadata 值；落盘前剔除 None（保持与 langchain add_texts 行为一致）。"""
    return {k: v for k, v in meta.items() if v is not None}


def seed_legacy(m, doc_id, user_id, n, content_fn, meta_extra=None):
    """把文档写入 legacy collection（source），保留指定 metadata；用假 embedding 落盘。"""
    chunks = []
    for i in range(n):
        meta = {
            'document_id': doc_id,
            'user_id': user_id,
            'chunk_index': i,
            'filename': f'{doc_id}.txt',
            'source': f'{doc_id}.txt',
            'indexed_embedding_model': None,
        }
        if meta_extra:
            meta.update(meta_extra(i))
        chunks.append({'id': f'{doc_id}_{i}', 'content': content_fn(i), 'metadata': meta})
    col = m.vector_store._collection
    col.add(
        ids=[c['id'] for c in chunks],
        embeddings=fe_embed([c['content'] for c in chunks]),
        documents=[c['content'] for c in chunks],
        metadatas=[clean_meta(c['metadata']) for c in chunks],
    )
    return chunks


def read_target_chunks(m, doc_id, user_id, model):
    return m._get_document_chunks_from_collection(doc_id, user_id, model)


def main():
    print('=== document-level reindex workflow 测试（临时 Chroma）===')
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        m = VectorStoreManager(tmp)

        # ---------------- T1：legacy -> custom 首次 reindex ----------------
        seed_legacy(m, 'd1', 1, 5, lambda i: f'd1 content {i}')
        legacy_before = [c['content'] for c in
                        m._get_document_chunks_from_collection('d1', 1, GLOBAL_MODEL)]
        res = run(m.reindex_document_to_model('d1', 1, CUSTOM_MODEL))
        tgt = read_target_chunks(m, 'd1', 1, CUSTOM_MODEL)
        check('T1 首次 reindex 写入 custom 集合（5 chunks）', len(tgt) == 5, f'len={len(tgt)}')
        check('T1 target collection 名正确', res['target_collection'] == EXPECTED_CUSTOM_COLLECTION,
              res['target_collection'])
        check('T1 source(legacy) 不变（仍为 5）',
              len(m._get_document_chunks_from_collection('d1', 1, GLOBAL_MODEL)) == 5)
        check('T1 content 完全一致且有序',
              [c['content'] for c in tgt] == legacy_before, f'{[c["content"] for c in tgt]}')
        check('T1 id 顺序一致',
              [c['id'] for c in tgt] == [f'd1_{i}' for i in range(5)])

        # ---------------- T3：普通 TXT metadata/content 完整保留 ----------------
        def normal_meta(i):
            return {'filename': 'mysql知识库.txt', 'source': 'mysql知识库.txt',
                    'file_type': 'txt'}
        seed_legacy(m, 'd3', 1, 4, lambda i: f'mysql line {i}', meta_extra=normal_meta)
        run(m.reindex_document_to_model('d3', 1, CUSTOM_MODEL))
        tgt3 = read_target_chunks(m, 'd3', 1, CUSTOM_MODEL)
        src3 = m._get_document_chunks_from_collection('d3', 1, GLOBAL_MODEL)
        check('T3 普通文档 chunk 数一致', len(tgt3) == len(src3) == 4)
        check('T3 metadata 完整保留（filename/file_type）',
              all(t['metadata'].get('filename') == 'mysql知识库.txt'
                  and t['metadata'].get('file_type') == 'txt' for t in tgt3))
        check('T3 document_id/user_id 一致',
              all(t['metadata'].get('document_id') == 'd3'
                  and t['metadata'].get('user_id') == 1 for t in tgt3))

        # ---------------- T2：Excel 58ece85 作为只读 source（正式库只读） ----------------
        excel_src = read_formal_excel_chunks('58ece85cfbe843de8484886d5ec14114')
        if excel_src is None:
            print('  [SKIP] 正式库 58ece85 不可读（可能后端占用锁），跳过 T2')
        else:
            check('T2 正式 58ece85 读取到 5 chunks', len(excel_src) == 5, f'len={len(excel_src)}')
            # 把正式 source 原样写入临时 legacy（仅 metadata/content/id，不写正式库）
            col = m.vector_store._collection
            col.add(
                ids=[c['id'] for c in excel_src],
                embeddings=fe_embed([c['content'] for c in excel_src]),
                documents=[c['content'] for c in excel_src],
                metadatas=[clean_meta(c['metadata']) for c in excel_src],
            )
            run(m.reindex_document_to_model('58ece85cfbe843de8484886d5ec14114', 144, CUSTOM_MODEL))
            tgtx = read_target_chunks(m, '58ece85cfbe843de8484886d5ec14114', 144, CUSTOM_MODEL)
            check('T2 Excel reindex 后仍为 5 chunks', len(tgtx) == 5, f'len={len(tgtx)}')
            check('T2 Excel content 完全一致',
                  sorted(c['content'] for c in tgtx) == sorted(c['content'] for c in excel_src))
            # metadata 关键字段（workbook_summary/sheet_schema/row_group）不丢
            types = {t['metadata'].get('chunk_type') for t in tgtx}
            check('T2 Excel chunk_type 完整保留',
                  {'workbook_summary', 'sheet_schema', 'row_group'}.issubset(types), f'types={types}')
            check('T2 Excel sheet_name/table_id 保留',
                  all(t['metadata'].get('sheet_name') and t['metadata'].get('table_id') for t in tgtx))

        # ---------------- T4：错误 user 拒绝 ----------------
        seed_legacy(m, 'd4', 163, 3, lambda i: f'd4 {i}')
        raised4 = None
        try:
            run(m.reindex_document_to_model('d4', 153, CUSTOM_MODEL))
        except ReindexPermissionDenied as e:
            raised4 = e
        check('T4 错误 user 被拒绝（ReindexPermissionDenied）', raised4 is not None)

        # ---------------- T5：不存在 document 拒绝 ----------------
        raised5 = None
        try:
            run(m.reindex_document_to_model('nonexistent_doc', 1, CUSTOM_MODEL))
        except ReindexDocumentNotFound as e:
            raised5 = e
        check('T5 不存在 document 明确报错（ReindexDocumentNotFound）', raised5 is not None)

        # ---------------- T6：21 chunks = 20 + 1（batch <=20） ----------------
        seed_legacy(m, 'd6', 1, 21, lambda i: f'd6 chunk {i:02d}')
        run(m.reindex_document_to_model('d6', 1, CUSTOM_MODEL))
        tgt6 = read_target_chunks(m, 'd6', 1, CUSTOM_MODEL)
        check('T6 21 chunks 全部写入（无丢失/无重复）',
              len(tgt6) == 21, f'len={len(tgt6)}')
        check('T6 顺序一致',
              [c['content'] for c in tgt6] == [f'd6 chunk {i:02d}' for i in range(21)])

        # ---------------- T7/T8：target 已存在 → 替换（不重复，新版本胜出） ----------------
        seed_legacy(m, 'd8', 1, 10, lambda i: f'd8 v1 {i}')
        run(m.reindex_document_to_model('d8', 1, CUSTOM_MODEL))  # 首次：v1
        # 更新 source 为 v2（只改 legacy，不动 custom target）
        m.vector_store._collection.delete(
            where={'$and': [{'document_id': 'd8'}, {'user_id': 1}]})
        seed_legacy(m, 'd8', 1, 10, lambda i: f'd8 v2 {i}')
        run(m.reindex_document_to_model('d8', 1, CUSTOM_MODEL))  # 替换：v2
        tgt8 = read_target_chunks(m, 'd8', 1, CUSTOM_MODEL)
        check('T7 target 已存在替换不产生 duplicate（=10）', len(tgt8) == 10, f'len={len(tgt8)}')
        check('T8 新版本胜出（content=v2）',
              all('v2' in x['content'] for x in tgt8),
              f'{[x["content"] for x in tgt8][:2]}')

        # ---------------- T9：target 已存在 + 新版本写入失败 → 旧版本完整恢复 ----------------
        seed_legacy(m, 'd9', 1, 10, lambda i: f'd9 v1 {i}')
        run(m.reindex_document_to_model('d9', 1, CUSTOM_MODEL))  # v1
        tcol = m._resolve_store(CUSTOM_MODEL)[0]._collection
        orig_add = tcol.add
        calls = {'n': 0}

        def flaky_add(**kw):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('simulated new-version add failure')
            return orig_add(**kw)

        tcol.add = flaky_add
        raised9 = False
        try:
            run(m.reindex_document_to_model('d9', 1, CUSTOM_MODEL))  # 再次替换，add 失败
        except Exception:
            raised9 = True
        tcol.add = orig_add
        tgt9 = read_target_chunks(m, 'd9', 1, CUSTOM_MODEL)
        check('T9 写入失败 → 旧版本完整恢复（=10）', len(tgt9) == 10, f'len={len(tgt9)}')
        check('T9 旧版本 content 保留（v1）',
              all('v1' in x['content'] for x in tgt9),
              f'{[x["content"] for x in tgt9][:2]}')
        check('T9 异常已上抛', raised9)

        # ---------------- T10：target 首次创建 + 写入失败 → target=0 ----------------
        # 直接让该 store 的 embedding 函数抛错（add_texts 与 reindex 两条路径都走 embed_documents）
        seed_legacy(m, 'd10', 1, 5, lambda i: f'd10 {i}')
        emb10 = m._resolve_store(CUSTOM_MODEL)[0]._embedding_function
        orig_emb10 = emb10.embed_documents

        def boom10(texts):
            raise RuntimeError('simulated first-create embedding failure')

        emb10.embed_documents = boom10
        raised10 = False
        try:
            run(m.reindex_document_to_model('d10', 1, CUSTOM_MODEL))
        except Exception:
            raised10 = True
        emb10.embed_documents = orig_emb10
        d10_tgt = read_target_chunks(m, 'd10', 1, CUSTOM_MODEL)
        check('T10 首次创建 embedding 失败 → target 文档 0 向量', len(d10_tgt) == 0, f'len={len(d10_tgt)}')
        check('T10 异常已上抛', raised10)

        # ---------------- T11：CancelledError → rollback ----------------
        seed_legacy(m, 'd11', 1, 5, lambda i: f'd11 {i}')
        emb11 = m._resolve_store(CUSTOM_MODEL)[0]._embedding_function
        orig_emb11 = emb11.embed_documents

        def boom11(texts):
            raise asyncio.CancelledError()

        emb11.embed_documents = boom11
        raised11 = False
        try:
            run(m.reindex_document_to_model('d11', 1, CUSTOM_MODEL))
        except asyncio.CancelledError:
            raised11 = True
        except Exception:
            raised11 = 'other'
        emb11.embed_documents = orig_emb11
        d11_tgt = read_target_chunks(m, 'd11', 1, CUSTOM_MODEL)
        check('T11 CancelledError 回滚 → target 文档 0 向量', len(d11_tgt) == 0, f'len={len(d11_tgt)}')
        check('T11 CancelledError 重抛（未被吞）', raised11 is True, f'raised={raised11}')

        # ---------------- T12：source collection 永远不变 ----------------
        legacy_d1_after = [c['content'] for c in
                          m._get_document_chunks_from_collection('d1', 1, GLOBAL_MODEL)]
        check('T12 source(legacy) 在多次 reindex 后内容不变',
              legacy_d1_after == legacy_before, f'{legacy_d1_after}')

        # ---------------- T13：custom collection 正确创建 ----------------
        check('T13 target collection 名为安全名',
              res['target_collection'] == EXPECTED_CUSTOM_COLLECTION, res['target_collection'])

        # ---------------- T14：user_id isolation ----------------
        tgt_d1 = read_target_chunks(m, 'd1', 1, CUSTOM_MODEL)
        check('T14 target 向量 user_id 正确', all(c['metadata'].get('user_id') == 1 for c in tgt_d1))
        cross = read_target_chunks(m, 'd1', 163, CUSTOM_MODEL)
        check('T14 跨 user 读取 target 被隔离', len(cross) == 0)

        # ---------------- T15：source != target ----------------
        check('T15 source_model != target_model',
              res['source_model'] == GLOBAL_MODEL and res['target_model'] == CUSTOM_MODEL)

        # ---------------- T16：source == target 安全拒绝 ----------------
        raised16 = None
        try:
            run(m.reindex_document_to_model('d1', 1, GLOBAL_MODEL, source_embedding_model=GLOBAL_MODEL))
        except ValueError as e:
            raised16 = e
        check('T16 source==target 明确拒绝（ValueError）', raised16 is not None,
              f'msg={raised16}')

        # ---------------- T17：indexed_embedding_model 正确写为 target ----------------
        check('T17 indexed_embedding_model == target',
              all(c['metadata'].get('indexed_embedding_model') == CUSTOM_MODEL for c in tgt_d1),
              f'{[c["metadata"].get("indexed_embedding_model") for c in tgt_d1]}')

        # ---------------- T18：重启后 custom collection 仍能识别 ----------------
        m2 = VectorStoreManager(tmp)
        check('T18 重启后 custom collection 被恢复识别',
              EXPECTED_CUSTOM_COLLECTION in m2._custom_stores,
              f'custom_stores={list(m2._custom_stores.keys())}')
        tgt18 = read_target_chunks(m2, 'd1', 1, CUSTOM_MODEL)
        check('T18 重启后仍可读取 custom 文档', len(tgt18) == 5, f'len={len(tgt18)}')

        # ---------------- 正式库只读校验 ----------------
        print('')
        print('=== 正式库只读校验（data/chroma_db，不写入）===')
        real_path = ROOT / 'data' / 'chroma_db'
        if real_path.exists():
            try:
                rm = VectorStoreManager(str(real_path))
                info = rm.get_collection_info()
                n = info.get('total_documents', 0)
                check('正式 ai_assistant_docs count == 271', n == 271, f'count={n}')
            except Exception as e:
                check('正式库只读校验', False, f'异常={e}')
        else:
            print(f'  [SKIP] 正式库路径不存在: {real_path}')

    print('')
    print(f'=== 结果: PASS={PASS} FAIL={FAIL} ===')
    sys.exit(1 if FAIL else 0)


def read_formal_excel_chunks(doc_id):
    """只读打开正式 chroma_db，抽取 58ece85 的 content/metadata/id（绝不写入）。"""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=settings.VECTOR_DB_PATH)
        col = client.get_collection('ai_assistant_docs')
        data = col.get(where={'document_id': doc_id},
                       include=['documents', 'metadatas'])
        docs = data.get('documents', []) or []
        metas = data.get('metadatas', []) or []
        ids = data.get('ids', []) or []
        out = []
        for content, meta, cid in zip(docs, metas, ids):
            out.append({'id': cid, 'content': content, 'metadata': dict(meta or {})})
        out.sort(key=lambda c: int(c['metadata'].get('chunk_index', 0) or 0))
        return out
    except Exception as e:
        print(f'  [WARN] 读取正式 58ece85 失败：{e}')
        return None


if __name__ == '__main__':
    main()
