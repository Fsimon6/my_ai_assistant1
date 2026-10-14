# -*- coding: utf-8 -*-
"""上传失败错误处理验证（不联网、不触碰真实 Chroma / Embedding）。

聚焦此前审查发现的统一上传错误模型：
- A. Embedding quota 错误：返回结构化 error_type（非裸异常）。
- B. Dimension mismatch：detail 含 EMBEDDING_DIMENSION_MISMATCH。
- C. Parser 错误（如 XLSX 解析失败）：不返回 traceback、不返回文件路径。
- D. Storage 错误（Chroma 写入失败）：统一错误格式，不泄露原始异常。
- E. 正常上传（TXT 真实解析 / XLSX 路由）仍成功。

为避开认证中间件 / 环境相关登录 fixture，直接将 rag.router 挂载到无中间件的
全新 FastAPI 应用上，并仅覆盖 get_current_active_user 依赖来驱动端点测试。
服务层测试通过 monkeypatch 隔离 embedding / 向量库 / 缓存，仅保留真实文档解析。
"""
import os
import sys
import asyncio
import tempfile
import pytest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 隔离向量库到临时目录，绝不触碰真实 Chroma
TMP = tempfile.mkdtemp(prefix='upload_err_')
os.environ['VECTOR_DB_PATH'] = os.path.join(TMP, 'chroma_db')

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import backend.services.rag_service as rs_mod  # noqa: E402
import backend.services.document_service as ds_mod  # noqa: E402

# 隔离 RagService 的重度依赖（llm / cache / vector store），仅保留真实文档解析
rs_mod.get_llm = lambda: mock.MagicMock()
rs_mod.get_cache_service = lambda: mock.MagicMock()
rs_mod.get_vector_store_manager = lambda: mock.MagicMock()

from backend.api.v1 import rag as rag_module  # noqa: E402
from backend.api.v1.rag import router as rag_router  # noqa: E402
from backend.utils.auth import get_current_active_user  # noqa: E402
from backend.services.rag_service import RagService  # noqa: E402


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(rag_router)

    class _User:
        id = 153
        username = 'tester'

    app.dependency_overrides[get_current_active_user] = lambda: _User()
    with TestClient(app) as c:
        yield c


def _fake_service_return(result):
    """构造一个 get_rag_service() 调用后返回、其 process_and_store_document 直接返回 result 的假服务。"""
    async def fake_process(file_path, metadata=None, user_id=None,
                           original_filename=None, file_size=0, embedding_model=None):
        return result

    return lambda: type('FakeSvc', (), {
        'process_and_store_document': staticmethod(fake_process)
    })()


def _write_tmp(suffix, content=b'hello world'):
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.write(content)
    f.close()
    return f.name


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# A / B / 未知：端点层结构化错误
# ---------------------------------------------------------------------------
def test_embedding_quota_error_returns_error_type(client, monkeypatch):
    svc = _fake_service_return({
        'success': False,
        'error_type': 'EMBEDDING_QUOTA_EXCEEDED',
        'error_message': '额度不足，请检查计费状态',
    })
    monkeypatch.setattr(rag_module, 'get_rag_service', svc)
    resp = client.post('/api/v1/rag/upload', files={'file': ('a.txt', b'hello', 'text/plain')})
    assert resp.status_code == 429, (resp.status_code, resp.text)
    detail = resp.json()['detail']
    assert detail['error_type'] == 'EMBEDDING_QUOTA_EXCEEDED'
    assert detail['message'] == '额度不足，请检查计费状态'
    # 不得泄露 API Key / 原始异常
    assert 'sk-' not in resp.text
    assert 'Traceback' not in resp.text


def test_dimension_mismatch_returns_error_type(client, monkeypatch):
    svc = _fake_service_return({
        'success': False,
        'error_type': 'EMBEDDING_DIMENSION_MISMATCH',
        'error_message': 'Embedding 模型维度与已有向量集合不一致，请使用原 embedding 模型或重建向量集合。',
    })
    monkeypatch.setattr(rag_module, 'get_rag_service', svc)
    resp = client.post('/api/v1/rag/upload', files={'file': ('a.txt', b'hello', 'text/plain')})
    assert resp.status_code == 503, (resp.status_code, resp.text)
    detail = resp.json()['detail']
    assert detail['error_type'] == 'EMBEDDING_DIMENSION_MISMATCH'
    assert '维度' in detail['message']
    assert 'sk-' not in resp.text


def test_unknown_error_no_raw_traceback(client, monkeypatch):
    svc = _fake_service_return({
        'success': False,
        'error_type': 'UNKNOWN_ERROR',
        'error_message': '文档处理失败，请稍后重试。',
    })
    monkeypatch.setattr(rag_module, 'get_rag_service', svc)
    resp = client.post('/api/v1/rag/upload', files={'file': ('a.txt', b'hello', 'text/plain')})
    assert resp.status_code == 500, (resp.status_code, resp.text)
    detail = resp.json()['detail']
    assert detail['error_type'] == 'UNKNOWN_ERROR'
    assert detail['message'] == '文档处理失败，请稍后重试。'
    # 不得包含原始异常 / 路径
    assert 'Traceback' not in resp.text
    assert 'C:\\' not in resp.text and 'C:/' not in resp.text


# ---------------------------------------------------------------------------
# C / D：服务层错误分类（解析 vs 存储）
# ---------------------------------------------------------------------------
def test_parser_error_no_path_leak(monkeypatch):
    class BadDocProc:
        def process_file(self, path):
            raise ValueError('parser blew up at C:\\Users\\admin\\secret\\x.txt')

    monkeypatch.setattr(ds_mod, 'DocumentProcessor', BadDocProc)
    svc = RagService()
    svc.vector_store = mock.MagicMock()  # 解析阶段失败，不会到达 add_documents
    path = _write_tmp('.txt')
    try:
        result = _run(svc.process_and_store_document(path, user_id=1, original_filename='x.txt'))
    finally:
        if os.path.exists(path):
            os.remove(path)
    assert result['success'] is False
    assert result['error_type'] == 'DOCUMENT_PARSE_ERROR'
    # 不返回原始异常字段，且友好提示中不含文件路径
    assert 'error' not in result
    assert 'C:\\Users' not in result.get('error_message', '')


def test_storage_error_unified_format(monkeypatch):
    svc = RagService()
    store = mock.MagicMock()
    store.add_documents = mock.AsyncMock(
        side_effect=RuntimeError('Chroma write failed: traceback at C:\\Users\\admin\\chroma'))
    svc.vector_store = store
    path = _write_tmp('.txt', b'line1\nline2\nline3')
    try:
        result = _run(svc.process_and_store_document(path, user_id=1, original_filename='x.txt'))
    finally:
        if os.path.exists(path):
            os.remove(path)
    assert result['success'] is False
    assert result['error_type'] == 'STORAGE_ERROR'
    assert 'error' not in result  # 绝不把原始异常字符串返回给调用方
    assert 'C:\\Users' not in str(result)  # 路径/堆栈不泄露


# ---------------------------------------------------------------------------
# E：正常上传仍成功
# ---------------------------------------------------------------------------
def test_normal_txt_upload_success(monkeypatch):
    svc = RagService()
    store = mock.MagicMock()
    store.add_documents = mock.AsyncMock(return_value=['id1', 'id2'])
    svc.vector_store = store
    svc._clear_related_cache = mock.AsyncMock()  # 跳过缓存清理，聚焦解析+写入链路
    path = _write_tmp('.txt', b'hello world\nsecond line\nthird line')
    try:
        result = _run(svc.process_and_store_document(path, user_id=1, original_filename='x.txt'))
    finally:
        if os.path.exists(path):
            os.remove(path)
    assert result['success'] is True
    assert result['total_chunks'] >= 1
    assert result['document_id']
    assert 'error_type' not in result  # 成功路径不含错误字段


def test_xlsx_upload_success_route(client, monkeypatch):
    """XLSX 扩展名经上传路由正常受理并返回成功（真实 XLSX 处理由 table QA 测试覆盖）。"""
    svc = _fake_service_return({
        'success': True,
        'document_id': 'd1',
        'total_chunks': 3,
        'filename': 'x.xlsx',
        'chunk_ids': ['c1'],
    })
    monkeypatch.setattr(rag_module, 'get_rag_service', svc)
    resp = client.post(
        '/api/v1/rag/upload',
        files={'file': ('x.xlsx', b'fake-xlsx-bytes',
                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},
    )
    assert resp.status_code == 200, (resp.status_code, resp.text)
    body = resp.json()
    assert body['success'] is True
    assert body['filename'] == 'x.xlsx'
