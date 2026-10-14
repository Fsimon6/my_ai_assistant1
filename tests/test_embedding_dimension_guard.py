# -*- coding: utf-8 -*-
"""Embedding 维度兼容防护测试（第一轮）。

目标：确认「当前 embedding 输出维度」与「既有 Chroma collection 维度」在单次写入前被校验，
维度不一致时显式失败（含 collection 维度 / 当前维度 / 模型名 / 处理建议），而非依赖 Chroma
模糊报错或静默损坏集合。

- 临时 Chroma：全部使用 tempfile，绝不触碰正式 data/chroma_db。
- 维度可控假 embedding：通过类属性 DIM 模拟「模型配置变更后维度变化」，
  真实写入临时 collection 并读取其既有维度，属于真实路径集成测试（非纯 mock）。
- 不联网、不消耗 quota、不重建向量、不修改 .env。
"""
import sys
import asyncio
import tempfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.embeddings import Embeddings  # type: ignore

import backend.services.vector_service as vs


def run(coro):
    """VectorStoreManager 的向量操作方法均为 async，测试中以 asyncio.run 驱动。"""
    return asyncio.run(coro)


class DimFakeEmbeddings(Embeddings):
    """维度可控的确定性假 embedding（无网络 / 无 quota）。

    DIM 作为类属性，测试中在两次写入之间切换以模拟「EMBEDDING_MODEL 变更导致维度变化」。
    同一文本始终得到同一向量（确定性），便于 Chroma 相似度查询回归。
    """

    DIM = 1024
    NAME = 'dim-model'

    def __init__(self, embedding_model=None):
        self.embedding_model = embedding_model or self.NAME

    def _vec(self, text):
        import hashlib
        import random
        seed = int.from_bytes(
            hashlib.md5(f'{self.embedding_model}|{text}'.encode()).digest()[:8], 'big'
        )
        rng = random.Random(seed)
        return [rng.random() for _ in range(self.DIM)]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


# 必须在实例化 VectorStoreManager 之前完成 patch
vs.AIAssistantEmbeddings = DimFakeEmbeddings

from backend.services.vector_service import (  # noqa: E402
    VectorStoreManager,
    EmbeddingDimensionMismatch,
)


def make_docs(doc_id, n, filename):
    """构造 n 个 chunk（路径无关，所有 TXT/MD/DOCX/PDF/XLSX 共用 add_documents 写入路径）。"""
    return [{
        'id': f'{doc_id}_{i}',
        'content': f'{doc_id} content {i}',
        'metadata': {
            'document_id': doc_id,
            'user_id': 1,
            'chunk_index': i,
            'filename': filename,
        },
    } for i in range(n)]


# ---------------------------------------------------------------------------
# A. 相同维度：existing=1024 current=1024 → 正常继续
# ---------------------------------------------------------------------------
def test_same_dimension_passes():
    vs.AIAssistantEmbeddings = DimFakeEmbeddings
    DimFakeEmbeddings.DIM = 1024
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        m = VectorStoreManager(tmp)
        # 首次写入：collection 为空 → 无历史维度约束，放行
        run(m.add_documents(make_docs('a', 3, 'a.txt')))
        # 二次写入：既有 1024 == 当前 1024 → 放行
        run(m.add_documents(make_docs('b', 2, 'b.txt')))
        assert m.vector_store._collection.count() == 5


# ---------------------------------------------------------------------------
# B. 不同维度：existing=1024 current=1536 → 明确失败
# ---------------------------------------------------------------------------
def test_dimension_mismatch_raises():
    vs.AIAssistantEmbeddings = DimFakeEmbeddings
    DimFakeEmbeddings.DIM = 1024
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        m = VectorStoreManager(tmp)
        run(m.add_documents(make_docs('a', 3, 'a.txt')))  # 既有 collection = 1024
        DimFakeEmbeddings.DIM = 1536  # 模拟 EMBEDDING_MODEL 变更 → 维度变化
        with pytest.raises(EmbeddingDimensionMismatch) as exc:
            run(m.add_documents(make_docs('b', 2, 'b.xlsx')))
        assert m.vector_store._collection.count() == 3  # 不一致向量未被写入


# ---------------------------------------------------------------------------
# C. 异常信息：包含 existing / current / model name
# ---------------------------------------------------------------------------
def test_mismatch_error_message_contains_details():
    vs.AIAssistantEmbeddings = DimFakeEmbeddings
    DimFakeEmbeddings.DIM = 1024
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        m = VectorStoreManager(tmp)
        run(m.add_documents(make_docs('a', 2, 'a.txt')))
        DimFakeEmbeddings.DIM = 1536
        with pytest.raises(EmbeddingDimensionMismatch) as exc:
            run(m.add_documents(make_docs('b', 1, 'b.pdf')))
        msg = str(exc.value)
        assert '1024' in msg
        assert '1536' in msg
        assert 'dim-model' in msg  # 当前 embedding model 名称
        assert 'rebuild' in msg.lower() or 'original embedding model' in msg


# ---------------------------------------------------------------------------
# 空 collection（新建自定义模型集合）允许不同维度，不误报
# ---------------------------------------------------------------------------
def test_empty_custom_collection_allows_any_dimension():
    vs.AIAssistantEmbeddings = DimFakeEmbeddings
    DimFakeEmbeddings.DIM = 1536
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        m = VectorStoreManager(tmp)
        # 全新自定义模型集合（空）→ 即使维度与 legacy 不同也应放行
        run(m.add_documents(
            make_docs('c', 2, 'c.md'),
            embedding_model='brand-new-1536-model',
        ))
        name = m._safe_collection_name('brand-new-1536-model')
        assert m._custom_stores[name]._collection.count() == 2


# ---------------------------------------------------------------------------
# _check_dimension_compatibility 单元直接测试（mock collection）
# ---------------------------------------------------------------------------
def test_check_dimension_compatibility_unit():
    # 不触发 VectorStoreManager.__init__ 的 Chroma 初始化
    mgr = VectorStoreManager.__new__(VectorStoreManager)

    # 空 collection → 无历史维度，放行
    col_empty = MagicMock()
    col_empty.count.return_value = 0
    col_empty.name = 'empty'
    mgr._check_dimension_compatibility(col_empty, 1024, 'm')  # 不应抛

    # 既有 1024 vs 当前 1536 → 失败
    col_full = MagicMock()
    col_full.count.return_value = 5
    col_full.name = 'full'
    col_full.get.return_value = {'embeddings': [[0.0] * 1024]}
    with pytest.raises(EmbeddingDimensionMismatch):
        mgr._check_dimension_compatibility(col_full, 1536, 'm')

    # 既有 1536 vs 当前 1536 → 放行
    col_full.get.return_value = {'embeddings': [[0.0] * 1536]}
    mgr._check_dimension_compatibility(col_full, 1536, 'm')  # 不应抛


# ---------------------------------------------------------------------------
# D. 回归：TXT / XLSX 上传 embedding、Chroma 查询、Table-aware Retrieval 未破坏
# ---------------------------------------------------------------------------
def test_regression_txt_xlsx_search_and_table_retrieval():
    vs.AIAssistantEmbeddings = DimFakeEmbeddings
    DimFakeEmbeddings.DIM = 1024
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        m = VectorStoreManager(tmp)
        # TXT 上传 embedding
        run(m.add_documents(make_docs('t1', 3, 'report.txt')))
        # XLSX 上传 embedding（含 table chunk）
        xlsx = [{
            'id': 'x1_0',
            'content': '销售表 行1',
            'metadata': {
                'document_id': 'x1',
                'user_id': 1,
                'chunk_index': 0,
                'filename': 's.xlsx',
                'chunk_type': 'row_group',
                'sheet_name': 'Sheet1',
                'table_id': 'tbl1',
            },
        }]
        run(m.add_documents(xlsx))

        # Chroma 查询（相似度，确定性向量）
        results = run(m.search('t1 content 0', k=3))
        assert any('t1 content 0' in (r.get('content') or '') for r in results), \
            'TXT 上传后 Chroma 查询应返回该文档'

        # Table-aware Retrieval
        tbl = m.get_table_row_groups('x1', user_id=1)
        assert len(tbl) == 1, 'XLSX 的 row_group chunk 应可被 Table-aware Retrieval 读取'
        assert tbl[0]['content'] == '销售表 行1'
