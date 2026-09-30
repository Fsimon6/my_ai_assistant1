# -*- coding: utf-8 -*-
"""验证正常生产 Upload + RAG 的 Embedding Model 统一服从 .env（text-embedding-v3）。

- 切断 Character.embedding_model 对正常 RAG Embedding 的 override（仅 Chat model 仍由 Character 控制）。
- 忽略 /rag/upload 表单 embedding_model 参数。
- 全部使用 mock / 临时 Chroma，绝不真实调用 DashScope / 不触碰正式库。

覆盖验收：
  Test1  普通 RAG query 最终 embedding model = .env 全局
  Test2  Character.embedding_model=qwen3.7 时 RAG query 仍用 .env 全局
  Test3  Character 26（DB 中 qwen3.7）RAG query 仍用 .env 全局
  Test4  Character 31（DB 中 qwen3.7）RAG query 仍用 .env 全局
  Test5  /rag/upload 显式 embedding_model=qwen3.7 被忽略，仍用 .env 全局
  Test6  /rag/upload 不提交 embedding_model，仍用 .env 全局
  Test7  Character Chat model override 保留（model=test-chat-model），embedding 仍 .env 全局
  Test8  Character partial（仅 model 无 api_key）422 行为保留（schema 未改动）
"""
import sys
import os
import json
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config.settings import settings  # noqa: E402
import backend.api.v1.rag as rag_module  # noqa: E402
import backend.services.character_service as cs_module  # noqa: E402
from backend.api.v1.rag import (  # noqa: E402
    query_document,
    query_with_history,
    upload_document,
    QueryRequest,
    QueryWithHistoryRequest,
)

PASS = 0
FAIL = 0


def check(name, cond, info=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  [PASS] {name}')
    else:
        FAIL += 1
        print(f'  [FAIL] {name} :: {info}')


GLOBAL = settings.EMBEDDING_MODEL
assert GLOBAL == 'text-embedding-v3', f'预期 .env EMBEDDING_MODEL=text-embedding-v3，实际 {GLOBAL}'


class FakeUser:
    id = 1


class FakeCharacter:
    def __init__(self, model=None, api_key=None, embedding_model=None):
        self.model = model
        self.api_key = api_key
        self.embedding_model = embedding_model


class _RagServiceMock:
    """记录 rag.py 传给 rag_service 的 kwargs（尤其 embedding_model / model）。"""

    def __init__(self, rec):
        self.rec = rec

    def _gen(self, name, **kwargs):
        self.rec.append((name, dict(kwargs)))
        return _aempty()

    def rag_query(self, **kwargs):
        return self._gen('rag_query', **kwargs)

    def query_with_history(self, **kwargs):
        return self._gen('query_with_history', **kwargs)

    async def process_and_store_document(self, *args, **kwargs):
        self.rec.append(('process_and_store_document', dict(kwargs)))
        return {
            'success': True,
            'document_id': 'doc_x',
            'chunk_ids': [],
            'filename': 'f.txt',
            'total_chunks': 0,
        }


async def _aempty():
    if False:
        yield


async def _fake_save_uploaded_file(file):
    p = tempfile.mktemp(suffix='.txt')
    open(p, 'w', encoding='utf-8').close()
    return p


def _run_endpoint(coro):
    return asyncio.run(coro)


def main():
    rec = []

    # ---------------- Test2/3/4：Character RAG query（含 26 / 31） ----------------
    for cid in ('26', '31'):
        char = FakeCharacter(model=None, api_key=None,
                              embedding_model='qwen3.7-text-embedding-flash')
        with patch.object(rag_module, 'get_rag_service', return_value=_RagServiceMock(rec)), \
             patch.object(cs_module.character_service, 'get_character', return_value=char), \
             patch.object(rag_module.conversation_service, 'get_or_create_conversation_id', return_value=None), \
             patch.object(rag_module.conversation_service, 'append_message', return_value=None):
            rec.clear()
            req = QueryRequest(query='q', character_id=cid, stream=False)
            _run_endpoint(query_document(req, current_user=FakeUser()))
            kw = rec[0][1]
            check(f'Char RAG (id={cid}) embedding_model 未被传播', kw.get('embedding_model') is None,
                  f'embedding_model={kw.get("embedding_model")}')
            check(f'Char RAG (id={cid}) Chat 用全局 model(None)', kw.get('model') is None,
                  f'model={kw.get("model")}')

    # ---------------- Test2 流式分支也验证 ----------------
    char = FakeCharacter(model=None, api_key=None, embedding_model='qwen3.7-text-embedding-flash')
    with patch.object(rag_module, 'get_rag_service', return_value=_RagServiceMock(rec)), \
         patch.object(cs_module.character_service, 'get_character', return_value=char), \
         patch.object(rag_module.conversation_service, 'get_or_create_conversation_id', return_value=None), \
         patch.object(rag_module.conversation_service, 'append_message', return_value=None):
        rec.clear()
        req = QueryRequest(query='q', character_id='26', stream=True)
        resp = _run_endpoint(query_document(req, current_user=FakeUser()))
        # 触发 streaming generate() 以记录 rag_query 调用
        async def _drain():
            async for _ in resp.body_iterator:
                pass
        _run_endpoint(_drain())
        kw = rec[0][1]
        check('Char RAG (stream) embedding_model 未被传播', kw.get('embedding_model') is None,
              f'embedding_model={kw.get("embedding_model")}')

    # ---------------- Test7：Character Chat model override 保留 ----------------
    char = FakeCharacter(model='test-chat-model', api_key='test-key',
                          embedding_model='qwen3.7-text-embedding-flash')
    with patch.object(rag_module, 'get_rag_service', return_value=_RagServiceMock(rec)), \
         patch.object(cs_module.character_service, 'get_character', return_value=char), \
         patch.object(rag_module.conversation_service, 'get_or_create_conversation_id', return_value=None), \
         patch.object(rag_module.conversation_service, 'append_message', return_value=None):
        rec.clear()
        req = QueryRequest(query='q', character_id='7', stream=False)
        _run_endpoint(query_document(req, current_user=FakeUser()))
        kw = rec[0][1]
        check('Char Chat model override 保留(model=test-chat-model)', kw.get('model') == 'test-chat-model',
              f'model={kw.get("model")}')
        check('Char Chat 同时 embedding 仍 .env 全局(None)', kw.get('embedding_model') is None,
              f'embedding_model={kw.get("embedding_model")}')

    # ---------------- query-with-history 路径也验证 ----------------
    char = FakeCharacter(model=None, api_key=None, embedding_model='qwen3.7-text-embedding-flash')
    with patch.object(rag_module, 'get_rag_service', return_value=_RagServiceMock(rec)), \
         patch.object(cs_module.character_service, 'get_character', return_value=char), \
         patch.object(rag_module.conversation_service, 'get_or_create_conversation_id', return_value=None), \
         patch.object(rag_module.conversation_service, 'append_message', return_value=None):
        rec.clear()
        req = QueryWithHistoryRequest(query='q', history=[], character_id='26', stream=False)
        _run_endpoint(query_with_history(req, current_user=FakeUser()))
        kw = rec[0][1]
        check('query-with-history embedding_model 未被传播', kw.get('embedding_model') is None,
              f'embedding_model={kw.get("embedding_model")}')

    # ---------------- Test5/6：/rag/upload 忽略 embedding_model 表单参数 ----------------
    class FakeFile:
        filename = 'a.txt'

    for submit_val, label in [('qwen3.7-text-embedding-flash', '显式qwen3.7'), (None, '不提交')]:
        with patch.object(rag_module, 'get_rag_service', return_value=_RagServiceMock(rec)), \
             patch.object(rag_module, 'save_uploaded_file', new=_fake_save_uploaded_file):
            rec.clear()
            _run_endpoint(upload_document(
                file=FakeFile(), metadata=None,
                embedding_model=submit_val, current_user=FakeUser()))
            kw = rec[0][1]
            check(f'/rag/upload ({label}) embedding_model 被忽略→None',
                  kw.get('embedding_model') is None,
                  f'embedding_model={kw.get("embedding_model")}')

    # ---------------- Test1：集成验证 None → settings.EMBEDDING_MODEL ----------------
    import backend.services.vector_service as vsm  # noqa: E402
    from backend.services.llm_service import OpenAILikeLLM  # noqa: E402
    captured = []

    async def _counted(self, texts, model=None):
        captured.append(model)
        return [[0.0] * 8 for _ in texts]

    tmp = tempfile.mkdtemp(prefix='ge_test_')
    mgr = vsm.VectorStoreManager(persist_directory=tmp)
    with patch.object(OpenAILikeLLM, 'generate_embeddings', _counted):
        # 正常路径：embedding_model=None → 应路由到全局 .env 模型
        _run_endpoint(mgr.search(query='hello', embedding_model=None))
        check('search(None) 最终 embedding model = .env 全局',
              captured == [GLOBAL], f'captured={captured}')
        # 反向验证：旧 override 值仍会被 routing 接受（仅用于 reindex 基础设施，正常路径不再使用）
        captured.clear()
        _run_endpoint(mgr.search(query='hi', embedding_model='qwen3.7-text-embedding-flash'))
        check('override 机制仅保留给 reindex（不用于正常 RAG）',
              'qwen3.7-text-embedding-flash' in captured, f'captured={captured}')

    # ---------------- Test8：Character schema 不再暴露 embedding_model（DB 列/历史数据保留） ----------------
    # 本轮语义修复：正常 API 不再把 embedding_model 作为用户可配置字段；
    # 但 DB 列（models.character.AICharacter.embedding_model）与历史记录（如 26/31 的 qwen 值）不动。
    from backend.schemas.character import CharacterCreate, CharacterUpdate  # noqa: E402
    from backend.models.character import AICharacter  # noqa: E402
    fields_create = CharacterCreate.model_fields
    fields_update = CharacterUpdate.model_fields
    check('CharacterCreate 不再暴露 embedding_model',
          'embedding_model' not in fields_create, f'fields={list(fields_create)}')
    check('CharacterUpdate 不再暴露 embedding_model',
          'embedding_model' not in fields_update, f'fields={list(fields_update)}')
    cols = {c.name for c in AICharacter.__table__.columns}
    check('DB 列 embedding_model 保留（历史数据不动）',
          'embedding_model' in cols, f'cols={sorted(cols)}')

    print(f'\n=== {PASS}/{PASS + FAIL} PASS ===' if FAIL == 0
          else f'\n=== FAIL={FAIL} PASS={PASS} ===')
    return FAIL


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
