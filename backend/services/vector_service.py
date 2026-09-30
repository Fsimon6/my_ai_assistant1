# -*- coding: utf-8 -*-
import asyncio
import os
import re
import concurrent.futures
from typing import List, Dict, Any, Optional
import logging
import uuid
from backend.services.types import SearchResult

from langchain_classic.vectorstores import Chroma
from langchain_classic.embeddings.base import Embeddings

from backend.services.llm_service import get_llm
from backend.config.settings import settings

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """Embedding 生成失败：禁止返回空向量或静默回退。

    作为所有 embedding 错误的基类。子类携带友好的 error_type / user_message，
    供 API 层映射为明确的 HTTP 状态与用户提示（不暴露 API Key / 原始响应细节）。
    """
    error_type: str = 'EMBEDDING_SERVICE_ERROR'
    user_message: str = 'Embedding 服务暂时不可用，请稍后重试。'


class EmbeddingQuotaError(EmbeddingError):
    """额度/计费耗尽（如 429 insufficient_quota / FreeTierOnly）。"""
    error_type = 'EMBEDDING_QUOTA_EXCEEDED'
    user_message = 'Embedding 服务额度不足，请检查 Embedding 服务配额/计费状态。'


class EmbeddingRateLimitError(EmbeddingError):
    """真实限流（如 429 rate_limit_exceeded / too many requests）。"""
    error_type = 'EMBEDDING_RATE_LIMITED'
    user_message = 'Embedding 请求过于频繁，请稍后重试。'


class EmbeddingServiceUnavailableError(EmbeddingError):
    """其它 embedding 服务错误（provider 5xx / 超时 / 连接错误等）。"""
    error_type = 'EMBEDDING_SERVICE_ERROR'
    user_message = 'Embedding 服务暂时不可用，请稍后重试。'


class EmbeddingAuthError(EmbeddingError):
    """Provider 401/403 鉴权失败（API Key / 凭证问题），属配置错误而非可重试故障。"""
    error_type = 'EMBEDDING_AUTH_ERROR'
    user_message = 'Embedding 服务鉴权失败，请检查 Provider API Key / 凭证配置。'


class EmbeddingBadRequestError(EmbeddingError):
    """Provider 400 请求参数错误（如模型不存在 / 不支持的参数），属配置错误。"""
    error_type = 'EMBEDDING_BAD_REQUEST'
    user_message = 'Embedding 请求参数错误，请检查 Provider 模型与参数配置（如模型名称/维度）。'


class ReindexError(Exception):
    """显式 document reindex 业务异常基类。"""


class ReindexDocumentNotFound(ReindexError):
    """指定 document_id 在 source collection 中找不到。"""


class ReindexSourceNotFound(ReindexError):
    """指定的 source_embedding_model 对应的 collection 不存在。"""


class ReindexPermissionDenied(ReindexError):
    """当前 user_id 不拥有该 document，禁止 reindex。"""


def _run_async(coro):
    """在独立线程中运行协程，避免被 LangChain 在“运行中事件循环”内同步调用时
    asyncio.run() 报 “cannot be called from a running event loop”。

    embed_documents / embed_query 是同步回调（LangChain Chroma 在添加/检索时同步调用），
    而 RAG 链路本身运行在 async 事件循环里，因此必须用独立线程 + 新事件循环执行。
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


class AIAssistantEmbeddings(Embeddings):
    """远程/云端 embedding（通过 LLM provider 的 embedding API）。

    - embedding model 来自 settings.EMBEDDING_MODEL，与 LLM_MODEL（Chat）解耦；
    - 远程 embedding 调用失败时显式 raise，绝不返回空向量、也绝不静默回退到未安装的本地模型，
      避免把无有效向量的文档写入 Chroma。
    """

    def __init__(self, embedding_model: str = None):
        self.llm = get_llm()
        self.embedding_model = embedding_model or settings.EMBEDDING_MODEL

    def embed_documents(self, texts: List[str], model: str = None) -> List[List[float]]:
        """嵌入文档列表；失败抛出（已分类的）EmbeddingError，禁止空向量写入。"""
        try:
            vectors = _run_async(
                self.llm.generate_embeddings(texts, model=model or self.embedding_model)
            )
        except EmbeddingError:
            raise
        except Exception as e:
            raise self._classify_embedding_cause(e) from e
        self._validate_vectors(vectors, expected=len(texts))
        return vectors

    def embed_query(self, text: str, model: str = None) -> list[float]:
        """嵌入查询；失败抛出（已分类的）EmbeddingError。"""
        try:
            vectors = _run_async(
                self.llm.generate_embeddings([text], model=model or self.embedding_model)
            )
        except EmbeddingError:
            raise
        except Exception as e:
            raise self._classify_embedding_cause(e) from e
        self._validate_vectors(vectors, expected=1)
        return vectors[0]

    @staticmethod
    def _classify_embedding_cause(exc: Exception) -> 'EmbeddingError':
        """把底层 embedding 异常归类为友好错误类型（不暴露 API Key / 原始响应细节）。

        分类依据：openai 兼容异常的 status_code / body.error.type / body.error.code / 文本。
        - 429 + quota/billing/free-tier        -> EmbeddingQuotaError (额度/计费)
        - 429 + rate limit / too many requests -> EmbeddingRateLimitError (限流)
        - 其它 429                             -> EmbeddingRateLimitError (保守，不当成配额)
        - 401 / 403 鉴权                      -> EmbeddingAuthError (API Key / 凭证)
        - 400 请求参数错误                    -> EmbeddingBadRequestError (模型/参数配置)
        - 5xx / 超时 / 连接错误                -> EmbeddingServiceUnavailableError (服务错误)
        """
        status = getattr(exc, 'status_code', None)
        body = getattr(exc, 'body', None)
        err_obj = body.get('error', {}) if isinstance(body, dict) else {}
        code = str(err_obj.get('code') or getattr(exc, 'code', '') or '').lower()
        etype = str(err_obj.get('type') or '').lower()
        text = str(exc).lower()

        if status == 429 or '429' in text:
            quota_hit = (
                'insufficient_quota' in code or 'insufficient_quota' in etype
                or 'allocationquota' in code or 'allocationquota' in etype
                or 'quota' in text or 'free tier' in text or 'free quota' in text
                or 'billing' in text
            )
            if quota_hit:
                return EmbeddingQuotaError(str(exc))
            if ('rate_limit' in code or 'rate_limit' in etype
                    or 'rate limit' in text or 'too many requests' in text):
                return EmbeddingRateLimitError(str(exc))
            return EmbeddingRateLimitError(str(exc))

        # 401/403：Provider 鉴权失败（API Key / 凭证问题），属配置错误而非可重试的临时故障
        if status in (401, 403) or any(k in text for k in
                                       ('authentication', 'unauthorized', 'api key', 'permission', 'forbidden')):
            return EmbeddingAuthError(str(exc))

        # 400：请求参数错误（如模型不存在 / 不支持的参数），属配置错误而非服务不可用
        if status == 400 or '400' in text or 'bad request' in text:
            return EmbeddingBadRequestError(str(exc))

        return EmbeddingServiceUnavailableError(str(exc))

    @staticmethod
    def _validate_vectors(vectors, expected: int) -> None:
        if not vectors or len(vectors) != expected:
            raise EmbeddingError(
                f'embedding 返回数量异常：期望 {expected} 条，实际 '
                f'{len(vectors) if vectors else 0} 条'
            )
        for v in vectors:
            if not isinstance(v, (list, tuple)) or len(v) == 0:
                raise EmbeddingError('embedding 返回空向量，已禁止写入 Chroma')


class VectorStoreManager:
    """向量存储管理器"""

    # collection 路由基础设施相关常量
    _LEGACY_COLLECTION = 'ai_assistant_docs'
    _CUSTOM_PREFIX = 'ai_assistant_docs__'
    _MAX_NAME_LEN = 63  # Chroma collection 名长度上限
    _EMBED_BATCH = 20   # 远程 embedding 单次请求上限（与 add_texts 统一边界）

    def __init__(self, persist_directory: str = './data/chroma_db'):
        self.persist_directory = persist_directory
        os.makedirs(persist_directory, exist_ok=True)

        # 全局/legacy 模型（= settings.EMBEDDING_MODEL）对应的既有 collection 与 embeddings。
        # 历史 271 条向量全部位于此 collection，本次改造保持 100% 兼容（绝不迁移/重嵌/删除）。
        self.embeddings = AIAssistantEmbeddings()
        self.vector_store = Chroma(
            persist_directory=persist_directory,
            embedding_function=self.embeddings,
            collection_name=self._LEGACY_COLLECTION,
        )
        # 单一 chromadb 客户端，供所有 collection 共享（避免多 PersistentClient 实例的锁冲突）。
        self._client = self.vector_store._client

        # 自定义模型（!= 全局模型）的 collection / embeddings 注册表，按【安全 collection 名】索引。
        # 每个自定义 collection 绑定自己专属的 AIAssistantEmbeddings(model) 实例，
        # 不复用、也不在调用间变更共享实例的 embedding_model（避免多模型竞态）。
        self._custom_stores: Dict[str, Chroma] = {}
        self._custom_embeddings: Dict[str, AIAssistantEmbeddings] = {}

        # 启动时登记磁盘上已存在的自定义 collection（跨进程恢复，避免遗漏）。
        self._register_existing_custom_collections()

    # ------------------------------------------------------------------ #
    # collection 路由基础设施
    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe_collection_name(model: str) -> str:
        """把任意 embedding model 名稳定映射为合法 Chroma collection 名。

        - 仅保留 [A-Za-z0-9_-]，其余字符统一替换为 '_'（点号/短横线/空格等）。
        - 前缀固定为 `ai_assistant_docs__`；同一 model 永远得到同一 collection 名。
        - 简单、可读、稳定；不追求 hash。
        """
        if not model:
            raise ValueError('embedding_model 不能为空')
        # 仅保留 [A-Za-z0-9]，其余字符（点号/短横线/空格等）统一替换为 '_'。
        safe = re.sub(r'[^A-Za-z0-9]', '_', model).strip('_')
        name = f'{VectorStoreManager._CUSTOM_PREFIX}{safe}'
        # 截断到 Chroma 上限（极少触发）；保留前缀与常规模型可读性。
        return name[: VectorStoreManager._MAX_NAME_LEN]

    def _resolve_store(self, embedding_model: str = None):
        """按 embedding_model 返回 (Chroma, AIAssistantEmbeddings) 二元组。

        - 全局模型 → 既有 `ai_assistant_docs`（legacy，兼容历史 271 条向量）。
        - 自定义模型 → 懒创建独立 collection + 独立 embeddings 实例。
        """
        model = embedding_model or settings.EMBEDDING_MODEL
        if model == settings.EMBEDDING_MODEL:
            return self.vector_store, self.embeddings
        name = self._safe_collection_name(model)
        store = self._custom_stores.get(name)
        if store is None:
            store = self._create_custom_store(model, name)
        return store, self._custom_embeddings[name]

    def _create_custom_store(self, model: str, name: str) -> Chroma:
        """为自定义模型创建（或复用）独立 collection，并绑定专属 embeddings。"""
        # 把 embedding_model 记入 collection metadata，供跨进程恢复时正确绑定模型。
        self._client.get_or_create_collection(
            name=name, metadata={'embedding_model': model}
        )
        emb = AIAssistantEmbeddings(embedding_model=model)
        store = Chroma(
            client=self._client,
            embedding_function=emb,
            collection_name=name,
        )
        self._custom_stores[name] = store
        self._custom_embeddings[name] = emb
        logger.info(f'路由自定义 embedding model="{model}" → collection="{name}"')
        return store

    def _register_existing_custom_collections(self) -> None:
        """启动登记磁盘上已存在的自定义 collection（不含 legacy）。"""
        try:
            for col in self._client.list_collections():
                name = getattr(col, 'name', None)
                if not name or not name.startswith(self._CUSTOM_PREFIX):
                    continue
                if name in self._custom_stores:
                    continue
                model = None
                try:
                    meta = self._client.get_collection(name=name).metadata or {}
                    model = meta.get('embedding_model')
                except Exception:
                    model = None
                if not model:
                    logger.warning(
                        f'跳过缺少 embedding_model 标记的自定义集合（不参与路由）：{name}'
                    )
                    continue
                emb = AIAssistantEmbeddings(embedding_model=model)
                store = Chroma(
                    client=self._client,
                    embedding_function=emb,
                    collection_name=name,
                )
                self._custom_stores[name] = store
                self._custom_embeddings[name] = emb
                logger.info(f'恢复自定义集合：name="{name}" model="{model}"')
        except Exception as e:
            logger.warning(f'登记已存在自定义 collection 失败（忽略，不影响启动）：{e}')

    def _iter_stores(self):
        """遍历全部 collection（legacy + 已实例化自定义），用于需跨模型扫描的读/删操作。"""
        yield self.vector_store
        for s in self._custom_stores.values():
            yield s

    async def add_documents(
        self,
        documents: List[Dict[str, Any]],
        collection_name: str = _LEGACY_COLLECTION,
        embedding_model: str = None,
    ) -> List[str]:
        """添加文档到向量数据库。

        按 embedding_model 路由到对应 collection（全局模型→ai_assistant_docs，
        自定义模型→ai_assistant_docs__<safe>）。复用单例 Chroma 客户端与单例 chromadb
        客户端，避免每次上传重建客户端导致查询/删除状态不一致。
        `collection_name` 仅保留为兼容参数（路由以 embedding_model 为准）。
        """
        try:
            # 按 embedding_model 路由到正确的 collection 与专属 embeddings（不突变共享实例）。
            store, emb = self._resolve_store(embedding_model)
            effective_model = emb.embedding_model
            # 提取内容和元数据
            contents = [doc['content'] for doc in documents]
            # 复制 metadata，避免就地修改调用方传入的对象
            metadatas = [dict(doc['metadata']) for doc in documents]
            # provenance：明确区分“历史原始索引模型（embedding_model）”与
            # “本次向量实际使用的模型（indexed_embedding_model）”，绝不伪造历史信息。
            # 历史 embedding_model 保持原值（孤儿为 null=未知），本字段如实记录当前向量模型。
            for m in metadatas:
                m['indexed_embedding_model'] = effective_model
            ids = [doc['id'] for doc in documents]

            # 向路由目标 collection 追加文本（add_texts 使用其专属 embeddings 实例）。
            result_ids = store.add_texts(
                texts=contents,
                metadatas=metadatas,
                ids=ids,
            )

            logger.info(
                f'成功添加{len(documents)}个文档到向量数据库 '
                f'(collection={store._collection.name}, model={effective_model})'
            )
            return result_ids

        except Exception as e:
            logger.error(f'添加文档到向量数据库失败：{e}')
            raise

    async def reindex_document(
        self,
        chunks: List[Dict[str, Any]],
        embedding_model: str = None,
        batch_size: int = 20,
    ) -> List[str]:
        """单文档重索引（补建 Chroma 向量），带文档级事务回滚。

        用于把“Representation 完整、Chroma 缺失”的历史孤儿文档重新建立向量，而不触碰
        其他文档/用户。

        - 复用单例 embedding 客户端与 Chroma 客户端（不产生第二套 batch 机制）；
        - 按 batch_size 顺序提交，每批 <=20 texts，每批独立日志；
        - 任一批失败：删除本次已写入的、属于该 document_id 的全部 chunk ids
          （id 形如 document_id_index，精确按 id 删除），不影响其他文档/用户；
          原始 Representation 与原始文件不变；原始异常继续向上抛出。

        调用方需保证 chunks 全部属于同一个 document_id。
        """
        if not chunks:
            return []
        # 按 embedding_model 路由到目标 collection 与专属 embeddings（不突变共享实例）。
        store, emb = self._resolve_store(embedding_model)
        effective_model = emb.embedding_model
        document_id = chunks[0].get('metadata', {}).get('document_id')
        total_batches = (len(chunks) + batch_size - 1) // batch_size

        added_ids: List[str] = []
        collection = store._collection
        try:
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start:start + batch_size]
                texts = [c['content'] for c in batch]
                metas = [dict(c['metadata']) for c in batch]
                # provenance：本批向量实际使用的模型（不覆盖历史 embedding_model）
                for m in metas:
                    m['indexed_embedding_model'] = effective_model
                ids = [c['id'] for c in batch]
                store.add_texts(texts=texts, metadatas=metas, ids=ids)
                added_ids.extend(ids)
                logger.info(
                    f'重索引批次 {start // batch_size + 1}/{total_batches} 完成，'
                    f'本批大小={len(batch)}，累计={len(added_ids)}'
                )
            return added_ids
        except asyncio.CancelledError:
            # 客户端超时/用户取消/进程被中止 → 任务被取消；必须回滚本次已写入的向量，
            # 否则留下“半截 vectors”（历史已真实出现过 171 条半截向量），绝不吞掉取消。
            logger.error(f'单文档重索引被取消（CancelledError），回滚本次写入：document_id={document_id}')
            if added_ids:
                # 仅删除本次该 document_id 已写入的 chunk ids（id 前缀保证只命中本文档）
                collection.delete(ids=added_ids)
                logger.info(
                    f'回滚删除 {len(added_ids)} 条本次写入向量（document_id={document_id}），'
                    f'其他文档/用户不受影响'
                )
            raise
        except Exception as e:
            logger.error(f'单文档重索引失败，回滚本次写入：{e}')
            if added_ids:
                # 仅删除本次该 document_id 已写入的 chunk ids（id 前缀保证只命中本文档）
                collection.delete(ids=added_ids)
                logger.info(
                    f'回滚删除 {len(added_ids)} 条本次写入向量（document_id={document_id}），'
                    f'其他文档/用户不受影响'
                )
            raise

    # ------------------------------------------------------------------ #
    # 显式 document-level reindex（跨 model collection 重建索引）
    # ------------------------------------------------------------------ #
    def _get_source_collection(self, embedding_model: str):
        """按 source_embedding_model 定位「只读」source collection（不会自动创建）。

        - 全局模型 → legacy `ai_assistant_docs`（始终存在）。
        - 自定义模型 → 按安全名查已存在的 collection；不存在则抛 ReindexSourceNotFound。
        """
        if embedding_model == settings.EMBEDDING_MODEL:
            return self._LEGACY_COLLECTION, self.vector_store._collection
        name = self._safe_collection_name(embedding_model)
        try:
            col = self._client.get_collection(name=name)
        except Exception:
            raise ReindexSourceNotFound(
                f'source collection for model "{embedding_model}" not found (name={name})'
            )
        return name, col

    def _get_document_chunks_from_collection(
        self,
        document_id: str,
        user_id: Optional[int],
        embedding_model: str = None,
    ) -> List[Dict[str, Any]]:
        """从「单一指定」source collection 读取某文档全部 chunk（不跨多模型合并）。

        返回每项为 {id, content, metadata}，并按 chunk_index 排序；保留完整 metadata 与原始 id。
        供显式 reindex 作为 source 使用，绝不影响 get_document_chunks 既有公开 API。
        """
        model = embedding_model or settings.EMBEDDING_MODEL
        _, collection = self._get_source_collection(model)
        conds: List[Dict[str, Any]] = [{'document_id': document_id}]
        if user_id is not None:
            conds.append({'user_id': user_id})
        where: Dict[str, Any] = {'$and': conds} if len(conds) > 1 else conds[0]

        data = collection.get(where=where, include=['documents', 'metadatas'])
        docs = data.get('documents', []) or []
        metas = data.get('metadatas', []) or []
        ids = data.get('ids', []) or []
        out: List[Dict[str, Any]] = []
        for content, meta, cid in zip(docs, metas, ids):
            meta = meta or {}
            out.append({'id': cid, 'content': content, 'metadata': dict(meta)})
        out.sort(key=lambda c: int(c['metadata'].get('chunk_index', 0) or 0))
        return out

    def _restore_target(
        self,
        collection,
        ids: List[str],
        embeddings: List[List[float]],
        documents: List[str],
        metadatas: List[Dict[str, Any]],
    ) -> None:
        """把快照的旧 target 文档向量写回（失败恢复用）。"""
        if not ids:
            return
        try:
            collection.add(ids=ids, embeddings=embeddings,
                           documents=documents, metadatas=metadatas)
        except Exception as e:
            logger.error(f'恢复旧 target 版本失败（数据可能丢失）：{e}')

    async def _reindex_document_replace(
        self,
        chunks: List[Dict[str, Any]],
        embedding_model: str,
        user_id: Optional[int],
        document_id: str,
    ) -> List[str]:
        """target collection 已存在该 document 时：安全「原子替换」并保留旧版可恢复。

        顺序：
        1) 快照旧 target 文档（含向量）；
        2) 先离线生成全部新 embedding（唯一会触发远程调用的步骤；失败则旧版完全不动）；
        3) 删除旧版本 + 写入新版本；任一失败 → 从快照恢复旧版本并 re-raise。
        绝不产生「旧 + 新」混合（duplicate），也绝不会在失败时让旧版永久消失。
        """
        store, emb = self._resolve_store(embedding_model)
        effective_model = emb.embedding_model
        collection = store._collection

        old = collection.get(
            where={'$and': [{'document_id': document_id}, {'user_id': user_id}]},
            include=['embeddings', 'documents', 'metadatas'],
        )
        old_ids = old.get('ids', []) or []
        _emb = old.get('embeddings')
        # chromadb 可能以 numpy ndarray 返回 embeddings，需转回 list[list] 以便恢复写入
        old_embeddings = (
            [e.tolist() if hasattr(e, 'tolist') else e for e in _emb]
            if _emb is not None else []
        )
        old_docs = old.get('documents', []) or []
        old_metas = old.get('metadatas', []) or []

        contents = [c['content'] for c in chunks]
        metas = []
        for c in chunks:
            m = dict(c['metadata'])
            m['indexed_embedding_model'] = effective_model
            metas.append(m)
        new_ids = [c['id'] for c in chunks]

        # 2) 先生成全部新 embedding（一次性提交完整 texts，由 llm.generate_embeddings 内部统一拆批 <=20）
        try:
            new_embeddings = emb.embed_documents(contents)
        except Exception as e:
            logger.error(f'重索引生成 embedding 失败，旧版本保留：document_id={document_id}: {e}')
            raise

        # 3) 删除旧版本 + 写入新版本（失败恢复旧版本）
        try:
            if old_ids:
                collection.delete(ids=old_ids)
            collection.add(ids=new_ids, embeddings=new_embeddings,
                           documents=contents, metadatas=metas)
        except asyncio.CancelledError:
            logger.error(f'重索引被取消，恢复旧版本：document_id={document_id}')
            self._restore_target(collection, old_ids, old_embeddings, old_docs, old_metas)
            raise
        except Exception as e:
            logger.error(f'重索引写入失败，恢复旧版本：document_id={document_id}: {e}')
            self._restore_target(collection, old_ids, old_embeddings, old_docs, old_metas)
            raise
        return new_ids

    async def reindex_document_to_model(
        self,
        document_id: str,
        user_id: Optional[int],
        target_embedding_model: str,
        source_embedding_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """显式 document-level reindex：把已有文档从 source model collection 重建到 target model collection。

        流程：校验 user 归属 → 从单一 source collection 读取最终 chunks（保持 content / metadata /
        chunk 顺序 / id）→ 用现有 llm embedding（统一 batch <=20）写入 target collection。

        - source_embedding_model 默认 settings.EMBEDDING_MODEL（legacy），覆盖 271 条历史文档。
        - source == target 明确拒绝（避免原地自删自写导致文档消失）。
        - 不重新解析任何原文件；不修改 source collection；失败不影响 source。
        - 不新增 SQL / DB / API。
        """
        source_model = source_embedding_model or settings.EMBEDDING_MODEL
        if source_model == target_embedding_model:
            raise ValueError(
                'source and target embedding model are identical; '
                'no cross-model reindex required'
            )

        # 1) 定位 source collection（只读，不自动创建）
        try:
            src_name, src_col = self._get_source_collection(source_model)
        except ReindexSourceNotFound:
            raise

        # 2) 存在性 + user 归属校验（越权/不存在给出明确错误，区分于“未找到”）
        probe = src_col.get(where={'document_id': document_id}, include=['metadatas'])
        metas = probe.get('metadatas', []) or []
        if not metas:
            raise ReindexDocumentNotFound(
                f'document {document_id} not found in source collection "{src_name}"'
            )
        if not any((m or {}).get('user_id') == user_id for m in metas):
            raise ReindexPermissionDenied(
                f'user {user_id} is not permitted to reindex document {document_id}'
            )

        # 3) 读取单一 source collection 的 chunks（保持顺序/metadata/id）
        chunks = self._get_document_chunks_from_collection(
            document_id, user_id, source_model)
        if not chunks:
            raise ReindexDocumentNotFound(
                f'document {document_id} has no chunks in source collection "{src_name}"'
            )

        re_chunks = [{
            'content': c['content'],
            'metadata': dict(c['metadata']),
            'id': c['id'],
        } for c in chunks]

        # 4) 判定 target 是否已存在该文档（决定首次创建还是安全替换）
        target_name = (self._LEGACY_COLLECTION
                       if target_embedding_model == settings.EMBEDDING_MODEL
                       else self._safe_collection_name(target_embedding_model))
        try:
            target_col = self._client.get_collection(name=target_name)
            existing = target_col.get(
                where={'$and': [{'document_id': document_id}, {'user_id': user_id}]},
                include=[])
            existing_ids = existing.get('ids', []) or []
        except Exception:
            existing_ids = []

        if existing_ids:
            await self._reindex_document_replace(
                re_chunks, target_embedding_model, user_id, document_id)
        else:
            await self.reindex_document(
                re_chunks, embedding_model=target_embedding_model,
                batch_size=self._EMBED_BATCH)

        return {
            'document_id': document_id,
            'source_model': source_model,
            'target_model': target_embedding_model,
            'target_collection': target_name,
            'chunk_count': len(re_chunks),
        }

    async def search(
        self,
        query: str,
        query_embedding=None,
        k: int = 5,
        filter_dict: Optional[Dict] = None,
        embedding_model: str = None,
    ) -> List[SearchResult]:
        """相似度搜索（按 embedding_model 路由到对应 collection）。"""
        try:
            # 按 embedding_model 路由到目标 collection（其专属 embeddings 用于 query 嵌入）。
            store, emb = self._resolve_store(embedding_model)
            results = store.similarity_search_with_relevance_scores(
                query=query,
                k=k,
                filter=filter_dict
            )

            # 格式化结果
            formatted_results = []
            for doc, score in results:
                formatted_results.append({
                    'content': doc.page_content,
                    'metadata': doc.metadata,
                    'score': score,
                    'id': doc.metadata.get('id', str(uuid.uuid4())),
                })

            logger.info(
                f'搜索查询"{query}"返回{len(formatted_results)}个结果 '
                f'(collection={store._collection.name}, model={emb.embedding_model})'
            )
            return formatted_results

        except Exception as e:
            logger.error(f'向量搜索失败：{e}')
            raise

    async def delete_documents(
        self,
        document_ids: List[str],
        user_id: Optional[int] = None,
    ) -> int:
        """按 document_id（文档级）+ user_id 精确删除向量。

        架构 C 下文档只属于单一 embedding model，但本方法不依赖外部知晓其模型：
        遍历全部 collection（legacy + 自定义），仅删除归属当前用户、且 document_id
        在给定列表中的向量；不会误删其他文档/用户。返回实际删除的向量总数
        （0 表示无匹配 / 越权）。
        """
        try:
            if not document_ids:
                return 0
            target = set(document_ids)
            deleted_total = 0
            for store in self._iter_stores():
                collection = store._collection
                if not collection:
                    continue
                # 取出全量 id 与元数据，按 document_id + user_id 精确过滤
                existing = collection.get(include=['metadatas'])
                all_ids = existing.get('ids', [])
                all_metas = existing.get('metadatas', [])
                allowed_ids = [
                    vid for vid, meta in zip(all_ids, all_metas)
                    if meta and meta.get('document_id') in target
                    and (user_id is None or meta.get('user_id') == user_id)
                ]
                if allowed_ids:
                    collection.delete(ids=allowed_ids)
                    deleted_total += len(allowed_ids)
            logger.info(f'成功删除{deleted_total}/{len(document_ids)}个文档的向量')
            return deleted_total

        except Exception as e:
            logger.error(f'删除文档失败：{e}')
            return 0

    def get_collection_info(self) -> Dict[str, Any]:
        """获取集合信息（legacy + 自定义）。

        返回 legacy 集合作为主信息，并附 custom_collections 列表
        （含各自定义集合的 embedding_model 与 count）。
        """
        try:
            info: Dict[str, Any] = {
                'total_documents': (
                    self.vector_store._collection.count()
                    if self.vector_store._collection else 0
                ),
                'collection_name': self._LEGACY_COLLECTION,
                'persist_directory': self.persist_directory,
                'custom_collections': [],
            }
            for name, store in self._custom_stores.items():
                try:
                    info['custom_collections'].append({
                        'collection_name': name,
                        'embedding_model': store._embedding_function.embedding_model,
                        'count': store._collection.count() if store._collection else 0,
                    })
                except Exception:
                    continue
            return info
        except Exception as e:
            logger.error(f'获取集合信息失败：{e}')
            return {'total_documents': 0}


    def list_user_documents(self, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """按归属用户列出其全部文档（聚合 document_id），仅返回该用户自己的文档，
        用于知识库文档列表。无需新增数据库表，直接基于 Chroma 向量 metadata 聚合。

        user_id: 服务端强制隔离；只聚合 metadata.user_id == user_id 的向量，杜绝跨用户泄露。
        """
        try:
            docs: Dict[str, Dict[str, Any]] = {}
            # 遍历全部 collection，覆盖多模型索引的文档（同一用户可能跨模型建索引）。
            for store in self._iter_stores():
                collection = store._collection
                if not collection:
                    continue
                data = collection.get(include=['metadatas'])
                all_metas = data.get('metadatas', []) or []

                for meta in all_metas:
                    if not meta:
                        continue
                    # 用户隔离：跳过非当前用户的向量
                    if user_id is not None and meta.get('user_id') != user_id:
                        continue
                    doc_id = meta.get('document_id')
                    if not doc_id:
                        continue

                    filename = meta.get('filename') or meta.get('source') or '未知文件'
                    existed = docs.get(doc_id)
                    if existed is None:
                        existed = docs[doc_id] = {
                            'document_id': doc_id,
                            'filename': filename,
                            'type': filename.rsplit('.', 1)[-1].lower() if '.' in filename else '',
                            'size': int(meta.get('file_size') or 0),
                            'chunks': 0,
                            'created_at': meta.get('processed_at'),
                        }
                    existed['chunks'] += 1
                    # 取最早的处理时间作为文档上传时间
                    pa = meta.get('processed_at')
                    if pa and (existed['created_at'] is None or pa < existed['created_at']):
                        existed['created_at'] = pa

            return list(docs.values())
        except Exception as e:
            logger.error(f'列出用户文档失败：{e}')
            return []

    async def get_document_chunks(
        self,
        document_id: str,
        user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """按 document_id 读取某文档全部分块内容（服务端强制 user_id 隔离），供文档预览/查看原文。

        无需新增数据库表，直接基于 Chroma 向量（每个 chunk 已存 document_id + user_id + chunk_index）。
        """
        try:
            # Chroma where 仅支持单操作符；多条件必须用 $and 包裹
            conds: List[Dict[str, Any]] = [{'document_id': document_id}]
            if user_id is not None:
                conds.append({'user_id': user_id})
            where: Dict[str, Any] = {'$and': conds} if len(conds) > 1 else conds[0]

            chunks: List[Dict[str, Any]] = []
            # 文档只存在于其索引所用的单一 collection；跨全部 collection 扫描以正确定位。
            for store in self._iter_stores():
                collection = store._collection
                if not collection:
                    continue
                data = collection.get(where=where, include=['documents', 'metadatas'])
                docs = data.get('documents', []) or []
                metas = data.get('metadatas', []) or []
                for content, meta in zip(docs, metas):
                    meta = meta or {}
                    idx = int(meta.get('chunk_index', 0) or 0)
                    chunks.append({
                        'index': idx,
                        'content': content,
                        'source': meta.get('source') or meta.get('filename') or '',
                    })
            chunks.sort(key=lambda c: c['index'])
            return chunks
        except Exception as e:
            logger.error(f'读取文档分块失败：{e}')
            return []

    def _get_table_chunks(
        self,
        document_id: str,
        sheet_name: Optional[str],
        table_id: Optional[str],
        user_id: Optional[int],
        chunk_types: List[str],
    ) -> List[Dict[str, Any]]:
        """按 (document_id[, sheet_name][, table_id]) 读取指定类型表格 chunk（只读）。

        复用 get_document_chunks 的 $and 过滤风格；chunk_type 过滤在 Python 侧完成（单表 chunk 极少）。
        不调用 embedding、不写入 Chroma。
        """
        try:
            conds: List[Dict[str, Any]] = [{'document_id': document_id}]
            if sheet_name:
                conds.append({'sheet_name': sheet_name})
            if table_id:
                conds.append({'table_id': table_id})
            if user_id is not None:
                conds.append({'user_id': user_id})
            where: Dict[str, Any] = {'$and': conds} if len(conds) > 1 else conds[0]

            out: List[Dict[str, Any]] = []
            # 跨全部 collection 扫描（文档只存在于其索引所用的单一 collection）。
            for store in self._iter_stores():
                collection = store._collection
                if not collection:
                    continue
                data = collection.get(where=where, include=['documents', 'metadatas'])
                docs = data.get('documents', []) or []
                metas = data.get('metadatas', []) or []
                for content, meta in zip(docs, metas):
                    meta = meta or {}
                    if meta.get('chunk_type') in chunk_types:
                        out.append({
                            'content': content,
                            'metadata': meta,
                            'score': 1.0,
                            'id': meta.get('id') or str(meta.get('chunk_index')),
                        })
            return out
        except Exception as e:
            logger.error(f'读取表格 chunk 失败：{e}')
            return []

    def get_table_row_groups(
        self,
        document_id: str,
        sheet_name: Optional[str] = None,
        table_id: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """读取某表全部 row_group chunk（Table-aware Retrieval 扩展用，只读）。"""
        return self._get_table_chunks(document_id, sheet_name, table_id, user_id, ['row_group'])

    def get_table_aux_chunks(
        self,
        document_id: str,
        sheet_name: Optional[str] = None,
        table_id: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """读取某表的 workbook_summary + sheet_schema（辅助上下文，只读）。"""
        return self._get_table_chunks(
            document_id, sheet_name, table_id, user_id,
            ['workbook_summary', 'sheet_schema'])


# 单例实例
_vector_store_manager = None


def get_vector_store_manager() -> VectorStoreManager:
    """获取向量存储管理器单例"""
    global _vector_store_manager
    if _vector_store_manager is None:
        _vector_store_manager = VectorStoreManager()
    return _vector_store_manager
