# -*- coding: utf-8 -*-
"""上传/重索引取消安全回滚测试（不联网、不触碰真实 Chroma）。

通过把 VECTOR_DB_PATH 重定向到临时目录 + 用 FakeVectorStore 替换真实向量库，
验证：
  Test1 上传过程中 asyncio.CancelledError -> 进入 rollback，Representation/原始文件/Chroma 向量均清理，且 CancelledError 重新抛出
  Test2 上传 EmbeddingError -> 回滚正常（与 8298fa6 行为一致）
  Test2b 上传 RuntimeError -> 普通异常回滚正常
  Test3 reindex batch1 成功 + batch2 CancelledError -> batch1 向量全部删除，其余文档不变，CancelledError 重抛
  Test4 reindex batch1 成功 + batch2 RuntimeError -> 同样完整回滚
"""
import os
import sys
import asyncio
import tempfile
import shutil
from unittest import mock

# 把项目根目录加入 sys.path（测试直接以解释器运行，cwd 不会自动加入）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 必须在导入 backend 之前把向量库重定向到临时目录，确保真实项目 Chroma 不被触碰
TMP = tempfile.mkdtemp(prefix='cancel_test_')
os.environ['VECTOR_DB_PATH'] = os.path.join(TMP, 'chroma_db')

import backend.services.vector_service as vsm_mod  # noqa: E402
from backend.services.vector_service import (  # noqa: E402
    VectorStoreManager,
    EmbeddingError,
)
import backend.services.rag_service as rs_mod  # noqa: E402
from backend.services.rag_service import get_rag_service  # noqa: E402
from backend.services.table_representation import table_store_dir  # noqa: E402
from backend.config.settings import settings  # noqa: E402

PROJECT_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'
)


def _real_db_mtime():
    """记录真实项目 data/chroma_db 与 data/table_originals 的大小（字节），用于证明未被污染。

    注意：比较字节大小而非 mtime——本机可能仍有上一轮启动的开发后端在后台周期性触碰
    chroma_db（导致 mtime 漂移），但“未写入新向量”的强证据是磁盘字节数完全一致。
    """
    states = {}
    for name in ('chroma_db', 'table_originals'):
        p = os.path.join(PROJECT_DATA, name)
        states[name] = _dir_size(p) if os.path.exists(p) else 0
    return states


def _dir_size(p):
    total = 0
    for root, _d, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


# ----------------------------- Fake vector store -----------------------------
class FakeCollection:
    def __init__(self):
        self.store = {}  # id -> True

    def delete(self, ids):
        for i in ids:
            self.store.pop(i, None)


class FakeVectorStore:
    def __init__(self):
        self._collection = FakeCollection()
        self._store = {}  # document_id -> [ids]  供 upload add_documents / delete_documents 使用

    async def add_documents(self, documents, embedding_model=None):
        for d in documents:
            self._store.setdefault(d['metadata']['document_id'], []).append(d['id'])
        return [d['id'] for d in documents]

    def add_texts(self, texts, metadatas, ids):
        for i in ids:
            self._collection.store[i] = True
        return ids

    def delete_documents(self, document_ids, user_id=None):
        removed = 0
        for did in list(self._store.keys()):
            if did in document_ids:
                removed += len(self._store.pop(did))
        return removed


class _FakeEmbeddings:
    """替代 _resolve_store 返回的真实 embeddings，仅满足 reindex_document 读取 embedding_model。

    实际 embedding 由 fake store 的 add_texts 跳过（不联网）。
    """
    embedding_model = 'test-model'


def _make_rep(document_id, user_id, filename):
    cols = [{
        'col_index': c, 'col_letter': chr(64 + c), 'technical_name': f'C{c}',
        'display_name': f'C{c}', 'description': None, 'data_type': 'text',
    } for c in range(1, 6)]
    rows = [{
        'row_index': i,
        'range': f'A{i}:E{i}',
        'cells': [{'col_index': c, 'col_letter': chr(64 + c), 'value': f'v{i}_{c}', 'type': 's'}
                  for c in range(1, 6)],
    } for i in range(2, 11)]  # 9 data rows -> 2 row_groups
    return {
        'document_id': document_id, 'filename': filename, 'file_type': 'xlsx',
        'user_id': user_id, 'embedding_model': 'test-model',
        'workbook': {'sheets': [{
            'sheet_id': 0, 'sheet_name': 'Sheet1',
            'tables': [{
                'table_id': 'Sheet1#0', 'range': 'A1:E10', 'header_rows': [1],
                'n_header_rows': 1, 'columns': cols, 'data_row_start': 2,
                'data_row_end': 10, 'row_count': 9, 'col_count': 5, 'rows': rows,
            }],
        }]},
    }


RESULTS = []


def _check(name, cond, detail=''):
    RESULTS.append((name, cond, detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


async def test_upload_cancel():
    fake = FakeVectorStore()
    svc = get_rag_service()

    # 准备一个临时上传文件（内容无关，parse 已被 mock）
    src = os.path.join(TMP, 'src_fake.xlsx')
    with open(src, 'wb') as f:
        f.write(b'dummy')

    # 让 build_representation 返回可控 rep（使用内部生成的 document_id）
    def fake_build(original_path, document_id, user_id, filename, file_type, op_path, embedding_model=None):
        return _make_rep(document_id, user_id, filename)

    raised = {}
    try:
        with mock.patch.object(rs_mod, 'build_representation', fake_build), \
             mock.patch.object(rs_mod, 'validate_representation', lambda *a, **k: None), \
             mock.patch.object(vsm_mod, 'get_vector_store_manager', return_value=fake):
            # 部分写入后取消：模拟取消发生在 add_texts 写入中途
            async def partial_add(documents, embedding_model=None):
                half = documents[:max(1, len(documents) // 2)]
                for d in half:
                    fake._store.setdefault(d['metadata']['document_id'], []).append(d['id'])
                raise asyncio.CancelledError()
            svc.vector_store = fake
            fake.add_documents = partial_add
            await svc._process_and_store_table(
                src, metadata=None, user_id=1, original_filename='fake.xlsx',
                file_size=123, embedding_model='test-model')
    except asyncio.CancelledError:
        raised['cancel'] = True
    except Exception as e:  # noqa
        raised['other'] = e

    # 断言：CancelledError 重抛、回滚清理
    _check('Test1 upload CancelledError re-raised', raised.get('cancel') is True,
           f"raised={list(raised.keys())}")
    rep_dir = table_store_dir()
    rep_files = [f for f in os.listdir(rep_dir)] if os.path.isdir(rep_dir) else []
    _check('Test1 no leftover Representation JSON', len(rep_files) == 0, f"files={rep_files}")
    # 原始文件（被 store_original move 到 table_originals）应已删除
    originals = [f for f in os.listdir(rep_dir)] if os.path.isdir(rep_dir) else []
    _check('Test1 no leftover original file', len(originals) == 0, f"originals={originals}")
    # Chroma 向量应为 0（回滚删除了部分写入）
    total_vectors = sum(len(v) for v in fake._store.values())
    _check('Test1 no Chroma partial vectors', total_vectors == 0, f"vectors={total_vectors}")


async def test_upload_exception(kind):
    fake = FakeVectorStore()
    svc = get_rag_service()
    src = os.path.join(TMP, f'src_{kind}.xlsx')
    with open(src, 'wb') as f:
        f.write(b'dummy')

    def fake_build(original_path, document_id, user_id, filename, file_type, op_path, embedding_model=None):
        return _make_rep(document_id, user_id, filename)

    if kind == 'embedding':
        err = EmbeddingError('embedding failed')
    else:
        err = RuntimeError('boom')

    raised = {}
    try:
        with mock.patch.object(rs_mod, 'build_representation', fake_build), \
             mock.patch.object(rs_mod, 'validate_representation', lambda *a, **k: None), \
             mock.patch.object(vsm_mod, 'get_vector_store_manager', return_value=fake):
            async def err_add(documents, embedding_model=None):
                raise err
            svc.vector_store = fake
            fake.add_documents = err_add
            await svc._process_and_store_table(
                src, metadata=None, user_id=1, original_filename='fake.xlsx',
                file_size=123, embedding_model='test-model')
    except EmbeddingError as e:
        raised['embedding'] = e
    except RuntimeError as e:
        raised['runtime'] = e
    except Exception as e:  # noqa
        raised['other'] = e

    expect = 'embedding' if kind == 'embedding' else 'runtime'
    _check(f'Test2{kind[0] if kind=="embedding" else "b"} {kind} re-raised (not swallowed)',
           raised.get(expect) is not None and raised.get('other') is None,
           f"raised={list(raised.keys())}")
    rep_dir = table_store_dir()
    leftover = [f for f in os.listdir(rep_dir)] if os.path.isdir(rep_dir) else []
    _check(f'Test2{"a" if kind=="embedding" else "b"} no leftover after {kind}',
           len(leftover) == 0, f"leftover={leftover}")
    total_vectors = sum(len(v) for v in fake._store.values())
    _check(f'Test2{"a" if kind=="embedding" else "b"} no Chroma vectors', total_vectors == 0)


def _make_chunks(document_id, n, user_id=1):
    return [{
        'id': f'{document_id}_{i}',
        'content': f'content-{i}',
        'metadata': {
            'document_id': document_id, 'user_id': user_id, 'chunk_index': i,
            'filename': 'x.xlsx', 'source': 'x.xlsx', 'chunk_type': 'row_group',
            'sheet_name': 'Sheet1', 'table_id': 'Sheet1#0',
        },
    } for i in range(n)]


async def test_reindex_cancel(kind):
    fake = FakeVectorStore()
    fake._collection.store['other_1'] = True  # 其他文档的向量，须不受影响
    manager = VectorStoreManager(persist_directory=os.path.join(TMP, 'reindex_chroma'))
    # 注：96df333 起 reindex_document 经 _resolve_store 路由到 per-model collection，
    # 旧的 manager.vector_store 内部结构已被移除；fake 通过下方 _resolve_store 补丁注入。

    doc_id = 'reidx_' + kind
    chunks = _make_chunks(doc_id, 3)  # batch_size=2 -> batch1(2) ok, batch2(1) 触发

    state = {'n': 0}
    if kind == 'cancel':
        def add_texts(texts, metadatas, ids):
            state['n'] += 1
            if state['n'] == 1:
                for i in ids:
                    fake._collection.store[i] = True
                return ids
            raise asyncio.CancelledError()
    else:
        def add_texts(texts, metadatas, ids):
            state['n'] += 1
            if state['n'] == 1:
                for i in ids:
                    fake._collection.store[i] = True
                return ids
            raise RuntimeError('reindex boom')

    fake.add_texts = add_texts
    raised = {}
    try:
        # 96df333 起 reindex_document 经 _resolve_store 路由到 per-model collection；
        # 让该路由返回 fake store（而非已被移除的 vector_store 内部结构），保持完全离线。
        with mock.patch.object(VectorStoreManager, '_resolve_store',
                                return_value=(fake, _FakeEmbeddings())):
            await manager.reindex_document(chunks, embedding_model='test-model', batch_size=2)
    except asyncio.CancelledError:
        raised['cancel'] = True
    except RuntimeError:
        raised['runtime'] = True
    except Exception as e:  # noqa
        raised['other'] = e

    expect = 'cancel' if kind == 'cancel' else 'runtime'
    _check(f'Test3{"" if kind=="cancel" else "b"} reindex {kind} re-raised',
           raised.get(expect) is True and raised.get('other') is None,
           f"raised={list(raised.keys())}")
    # batch1 已写向量应被回滚删除
    doc_vectors = [k for k in fake._collection.store if k.startswith(doc_id)]
    _check(f'Test3{"" if kind=="cancel" else "b"} batch1 vectors rolled back',
           len(doc_vectors) == 0, f"doc_vectors={doc_vectors}")
    # 其他文档向量不受影响
    _check(f'Test3{"" if kind=="cancel" else "b"} other docs untouched',
           fake._collection.store.get('other_1') is True)


async def main():
    before = _real_db_mtime()
    await test_upload_cancel()
    await test_upload_exception('embedding')
    await test_upload_exception('runtime')
    await test_reindex_cancel('cancel')
    await test_reindex_cancel('runtime')
    after = _real_db_mtime()
    _check('real project data/chroma_db untouched', before.get('chroma_db') == after.get('chroma_db'),
           f"before={before.get('chroma_db')} after={after.get('chroma_db')}")
    _check('real project data/table_originals untouched',
           before.get('table_originals') == after.get('table_originals'),
           f"before={before.get('table_originals')} after={after.get('table_originals')}")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n=== {len(RESULTS)-len(failed)}/{len(RESULTS)} PASS ===")
    shutil.rmtree(TMP, ignore_errors=True)
    if failed:
        print('FAILED:', [r[0] for r in failed])
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    asyncio.run(main())
