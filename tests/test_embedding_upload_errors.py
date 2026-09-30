# -*- coding: utf-8 -*-
"""Embedding / 上传失败错误处理验证（不联网、不触碰真实 Chroma）。

覆盖 provider 400/401/403/429-quota/429-rate/5xx/timeout/connection：
- 经 _classify_embedding_cause 归类为正确的 EmbeddingError 子类（不暴露 API Key）；
- 上传 API 据此返回语义化 HTTP 状态 + 结构化 {error_type, message}；
- 确认诊断信息（str(e)）不泄露给前端（detail 仅含 error_type + message）。

为避开认证中间件 / 环境相关登录 fixture，直接将 rag.router 挂载到无中间件的
全新 FastAPI 应用上，并仅覆盖 get_current_active_user 依赖来驱动错误映射测试。
"""
import os
import sys
import tempfile
import pytest
from unittest import mock
from fastapi import FastAPI
from fastapi.testclient import TestClient

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 隔离向量库到临时目录，绝不触碰真实 Chroma
TMP = tempfile.mkdtemp(prefix='emb_err_')
os.environ['VECTOR_DB_PATH'] = os.path.join(TMP, 'chroma_db')

from backend.services.vector_service import (  # noqa: E402
    AIAssistantEmbeddings,
    EmbeddingError,
    EmbeddingQuotaError,
    EmbeddingRateLimitError,
    EmbeddingAuthError,
    EmbeddingBadRequestError,
    EmbeddingServiceUnavailableError,
)
from backend.api.v1 import rag as rag_module  # noqa: E402
from backend.api.v1.rag import router as rag_router  # noqa: E402
from backend.utils.auth import get_current_active_user  # noqa: E402


@pytest.fixture
def client():
    # 全新应用，不带认证中间件，仅用于验证端点错误映射
    app = FastAPI()
    app.include_router(rag_router)

    class _User:
        id = 153
        username = 'tester'

    def override_user():
        return _User()

    app.dependency_overrides[get_current_active_user] = override_user
    with TestClient(app) as c:
        yield c


def _fake_provider_error(status, text='', body=None):
    """构造一个模仿 openai 兼容异常的假异常：携带 status_code / body。"""
    class _E(Exception):
        pass
    e = _E(text)
    e.status_code = status
    e.body = body or {}
    return e


def test_classify_provider_status():
    assert isinstance(
        AIAssistantEmbeddings._classify_embedding_cause(_fake_provider_error(400)),
        EmbeddingBadRequestError)
    assert isinstance(
        AIAssistantEmbeddings._classify_embedding_cause(_fake_provider_error(401)),
        EmbeddingAuthError)
    assert isinstance(
        AIAssistantEmbeddings._classify_embedding_cause(_fake_provider_error(403)),
        EmbeddingAuthError)
    assert isinstance(
        AIAssistantEmbeddings._classify_embedding_cause(_fake_provider_error(429, 'insufficient_quota')),
        EmbeddingQuotaError)
    assert isinstance(
        AIAssistantEmbeddings._classify_embedding_cause(_fake_provider_error(429, 'rate limit')),
        EmbeddingRateLimitError)
    assert isinstance(
        AIAssistantEmbeddings._classify_embedding_cause(_fake_provider_error(500)),
        EmbeddingServiceUnavailableError)
    # 连接 / 超时：无 status_code -> 服务不可用
    assert isinstance(
        AIAssistantEmbeddings._classify_embedding_cause(_fake_provider_error(None, 'connection timeout')),
        EmbeddingServiceUnavailableError)


def test_classify_does_not_leak_api_key():
    e = _fake_provider_error(401, 'AuthenticationError: Incorrect API key provided: sk-abc123XYZ')
    classified = AIAssistantEmbeddings._classify_embedding_cause(e)
    # error_type 只是固定常量，绝不携带密钥
    assert classified.error_type == 'EMBEDDING_AUTH_ERROR'
    # 友好提示中不含密钥片段
    assert 'sk-abc123XYZ' not in classified.user_message


def test_upload_embedding_error_status_mapping(client, monkeypatch):
    """上传 API 对每个 embedding 错误类型返回语义化 HTTP 状态与结构化 detail。"""
    cases = [
        ('EMBEDDING_QUOTA_EXCEEDED', 429),
        ('EMBEDDING_RATE_LIMITED', 429),
        ('EMBEDDING_AUTH_ERROR', 502),
        ('EMBEDDING_BAD_REQUEST', 502),
        ('EMBEDDING_SERVICE_ERROR', 503),
    ]
    for error_type, expected_status in cases:
        async def fake_process(file_path, metadata=None, user_id=None,
                               original_filename=None, file_size=0, embedding_model=None):
            return {
                'success': False,
                'error_type': error_type,
                'error_message': '友好提示文本',
                'error': '仅后端诊断用，不得泄露',  # 前端不应收到
                'filename': 'x.txt',
            }

        monkeypatch.setattr(
            rag_module, 'get_rag_service',
            lambda: type('FakeSvc', (), {
                'process_and_store_document': staticmethod(fake_process)
            })())

        resp = client.post(
            '/api/v1/rag/upload',
            files={'file': ('x.txt', b'hello world', 'text/plain')},
        )
        assert resp.status_code == expected_status, (error_type, resp.status_code, resp.text)
        detail = resp.json()['detail']
        assert detail['error_type'] == error_type
        assert detail['message'] == '友好提示文本'
        # 诊断信息不得泄露给前端
        assert '仅后端诊断用，不得泄露' not in resp.text
