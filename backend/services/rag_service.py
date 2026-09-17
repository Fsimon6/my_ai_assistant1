# -*- coding: utf-8 -*-
import os
import os
import uuid
from typing import List, Dict, Any, Optional
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import logging
from datetime import datetime
import re

from backend.config.settings import settings
from backend.services.llm_service import get_llm, LLMFactory, LLMConfig
from backend.services.vector_service import get_vector_store_manager
from backend.services.document_service import DocumentProcessor
from backend.services.cache_service import get_cache_service, CacheKey
from backend.services.table_representation import (
    TABLE_EXTENSIONS,
    build_representation,
    validate_representation,
    build_table_chunks,
    store_original,
    save_representation_json,
    load_representation,
    TableParseValidationError,
)
from pathlib import Path

logger = logging.getLogger(__name__)

# Phase 1 Table-aware Retrieval：全量枚举识别（方案 A，关键词规则；无 LLM 调用、无额外 embedding）
_ENUM_PHRASES = ["列出所有", "全部列出", "都列出来", "全部提取", "每一行", "每一",
                "所有订单", "所有记录", "全部记录", "所有行", "全部行", "完整列表",
                "全量列出", "全量提取", "逐行列出", "逐行列举"]
# 裸词（全部/所有/完整）仅当其后紧跟下列枚举名词时才视为全量枚举，降低误触发
_ENUM_NOUNS = ["SKU", "订单", "记录", "行", "数据", "表", "商品", "列表", "内容", "项", "明细"]
_ENUM_BARE = ["全部", "所有", "完整"]
# 全量修饰词 + 枚举动词 同时出现即视为全量枚举（覆盖“把所有 Shipping Provider Name 列出来”等句式）
_ENUM_FULL = ["全部", "所有", "每个", "每一", "完整", "都", "全表", "整表", "全量"]
_ENUM_VERBS = ["列出", "列举", "枚举", "提取", "显示", "展示", "给我", "输出", "整理", "汇总", "导出", "列出来"]
# 精确值查找意图词（配合 _extract_lookup_value 使用，避免误把含数字的普通语义问当成查找）
_LOOKUP_INTENT = ["查", "找", "定位", "对应", "匹配", "为", "=", "的variation", "的 variation", "是什么", "在哪里", "在哪"]
# 全量枚举/精确查找时的 Context 单页预算（字符数）；超过则自动分页为多轮 LLM 调用（不一次性塞入超大 prompt）
MAX_ENUM_CONTEXT_CHARS = int(os.getenv('TABLE_ENUM_MAX_CONTEXT_CHARS', '24000'))


class RagService:
    """RAG服务类(带缓存优化)"""

    def __init__(self):
        self.llm = get_llm()
        self.vector_store = get_vector_store_manager()
        self.document_processor = DocumentProcessor(
            chunk_size=800,
            chunk_overlap=150
        )
        self.cache = get_cache_service()
        logger.info(' RAG服务初始化（带缓存）')

    async def process_and_store_document(
        self,
        file_path: str,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[int] = None,
        original_filename: Optional[str] = None,
        file_size: int = 0,
        embedding_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """处理并存储文档（优化缓存清理）

        user_id: 文档归属用户，写入每个 chunk 的 metadata，用于向量检索按用户隔离。
        original_filename: 用户上传的原始文件名，写入 metadata 供文档列表/引用展示（覆盖临时文件名）。
        file_size: 文件字节大小，写入 metadata 供文档列表展示。
        """
        try:
            # 表格类文档走独立 Table Representation 路径（不影响既有 TXT/MD/DOCX 文本链路）
            ext = Path(file_path).suffix.lower()
            if ext in TABLE_EXTENSIONS:
                return await self._process_and_store_table(
                    file_path, metadata, user_id, original_filename, file_size, embedding_model
                )

            # 处理文档（文本）
            from backend.services.document_service import DocumentProcessor
            processor = DocumentProcessor()
            chunks = processor.process_file(file_path)

            # 生成单文档级 id（覆盖本次上传的所有 chunk，用于“文档 → 向量”生命周期管理）
            document_id = uuid.uuid4().hex

            # 添加额外元数据（含 document_id 与归属用户）
            for chunk in chunks:
                chunk['metadata'].update({
                    'document_id': document_id,
                    'processed_at': datetime.now().isoformat(),
                    **(metadata or {})
                })
                # 服务端强制写入：原始文件名（source 用于引用、filename 用于列表）、大小、归属用户，
                # 必须置于 **(metadata) 之后，确保不被客户端 metadata 覆盖。
                if original_filename:
                    chunk['metadata']['source'] = original_filename
                    chunk['metadata']['filename'] = original_filename
                if file_size:
                    chunk['metadata']['file_size'] = file_size
                if user_id is not None:
                    chunk['metadata']['user_id'] = user_id
                # 以 document_id 命名空间 chunk id，便于按文档精确删除
                chunk_index = chunk['metadata'].get('chunk_index', 0)
                chunk['id'] = f'{document_id}_{chunk_index}'

            # 存储到向量数据库
            ids = await self.vector_store.add_documents(chunks, embedding_model=embedding_model)

            # 清理与新文档可能相关的缓存
            filename = os.path.basename(file_path)
            await self._clear_related_cache(filename, chunks)

            # 清理临时文件
            try:
                os.remove(file_path)
            except OSError as e:
                logger.debug(f'清理临时文件失败：{file_path}, 错误：{e}')

            return {
                'success': True,
                'total_chunks': len(chunks),
                'document_id': document_id,
                'chunk_ids': ids,
                'filename': filename
            }

        except Exception as e:
            logger.error(f'处理存储文档失败：{e}')
            # 失败清理：embedding/向量写入失败时在成功落盘的文件即为半成品，需删除避免孤儿文件
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except OSError as oe:
                logger.debug(f'清理失败临时文件出错：{file_path}, {oe}')
            return {
                'success': False,
                'error': str(e),
                'filename': os.path.basename(file_path)
            }

    async def _process_and_store_table(
        self,
        file_path: str,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[int] = None,
        original_filename: Optional[str] = None,
        file_size: int = 0,
        embedding_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """表格文档专用处理：解析 -> Unified Representation -> Table Chunks -> Chroma。

        保留原始文件（§18），解析后做丢列校验（§21），校验失败不上 Chroma。
        """
        try:
            document_id = uuid.uuid4().hex
            ext = Path(file_path).suffix.lower()
            original_path = store_original(file_path, document_id)

            rep = build_representation(
                original_path, document_id, user_id,
                original_filename or Path(original_path).name,
                ext.lstrip('.'), original_path,
            )
            # §21 校验：原始列数 vs Representation 列数，防止异常 Excel 静默丢列
            validate_representation(original_path, rep)

            save_representation_json(rep)
            processed_at = datetime.now().isoformat()
            chunks = build_table_chunks(rep, processed_at)

            for chunk in chunks:
                chunk['metadata'].update(metadata or {})
                if original_filename:
                    chunk['metadata']['filename'] = original_filename
                if file_size:
                    chunk['metadata']['file_size'] = file_size
                if user_id is not None:
                    chunk['metadata']['user_id'] = user_id

            ids = await self.vector_store.add_documents(chunks, embedding_model=embedding_model)

            try:
                await self._clear_related_cache(Path(original_path).name, chunks)
            except Exception:
                pass

            return {
                'success': True,
                'total_chunks': len(chunks),
                'document_id': document_id,
                'chunk_ids': ids,
                'filename': Path(original_path).name,
            }
        except TableParseValidationError as e:
            logger.error(f'表格解析校验失败：{e}')
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except OSError:
                pass
            return {'success': False, 'error': f'解析校验失败：{e}', 'filename': os.path.basename(file_path)}
        except Exception as e:
            logger.error(f'表格文档处理失败：{e}')
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except OSError:
                pass
            return {'success': False, 'error': str(e), 'filename': os.path.basename(file_path)}

    def get_table_structure(self, document_id: str) -> Optional[Dict[str, Any]]:
        """读取已保存的 Unified Table Representation（供前端预览 Workbook/Sheet/Columns/Rows）。"""
        return load_representation(document_id)

    # ----------------- 结构化访问快捷路径（规避 Embedding / Chroma / LLM） -----------------
    def _structured_direct_chunks(self, query, document_id, user_id, model, api_key):
        """STRUCTURED_ACCESS 且 document_id 已知时，直接基于 Representation 精确读取，
        跳过 Embedding / Chroma / LLM。

        权限：仅当 rep.user_id == user_id 时生效（本地读 rep 校验，不触网络）；
              - rep 不存在 / user_id 明确不匹配 -> 明确拒绝（不回退 Chroma，避免跨用户泄露）
              - rep 无 user_id 字段（极旧文档）-> 回退原检索链路（保持原 Chroma 隔离行为）
        返回 async generator（命中结构化结果 / 越权拒绝）或 None（未命中，由调用方回退检索）。
        """
        if not document_id or user_id is None:
            return None
        struct_intent = self._detect_structured_intent(query)
        if struct_intent is None:
            return None
        rep = load_representation(document_id)
        if rep is None:
            async def _deny():
                yield '抱歉，没有权限访问该文档或文档不存在。'
            return _deny()
        rep_uid = rep.get('user_id')
        if rep_uid is not None and rep_uid != user_id:
            async def _deny():
                yield '抱歉，没有权限访问该文档或文档不存在。'
            return _deny()
        if rep_uid is None:
            # 旧 rep 无归属信息：无法本地校验，回退 Chroma 检索（保持原隔离行为）
            return None
        sheets = rep.get('workbook', {}).get('sheets', [])
        if not sheets or not sheets[0].get('tables'):
            return None
        s = sheets[0]
        t = s['tables'][0]
        res = self._resolve_structured_access(
            document_id, s['sheet_name'], t['table_id'], user_id, query, struct_intent)
        if res is None:
            return None
        block, meta = res
        struct_chunk = {
            'id': f'{document_id}_struct_0',
            'content': block,
            'metadata': {
                **meta,
                'document_id': document_id,
                'chunk_type': 'table_structured_access',
                'sheet_name': s['sheet_name'],
                'table_id': t['table_id'],
                'filename': rep.get('filename') or '',
                'source': rep.get('filename') or '',
                'chunk_index': 0,
            },
        }

        async def _gen():
            async for chunk in self._stream_structured_answer(
                    query, struct_chunk, model, api_key, False, None):
                yield chunk
        return _gen()

    async def _clear_related_cache(self, filename: str, chunks: List[Dict[str, Any]]):
        """清理与文档相关的缓存（缓存键为 md5，无法按前缀匹配，直接清空全局 RAG 缓存）"""
        try:
            await self.cache.clear()
            logger.info('已清理全部 RAG 查询缓存')
        except Exception as e:
            logger.warning(f'清理相关缓存时出错：{e}')

    def _resolve_llm(self, model: Optional[str], api_key: Optional[str]) -> 'BaseLLM':
        """解析用于本次生成的 LLM 实例（与 /speak/stream 行为一致）：
        - 同时提供 model 与 api_key → 角色专属配置
        - 否则 → 全局默认实例 self.llm
        """
        if model and api_key:
            logger.info(f'RAG 使用角色专属模型：{model}')
            return LLMFactory.create_llm(LLMConfig(
                provider=settings.LLM_PROVIDER,
                api_key=api_key,
                base_url=settings.LLM_BASE_URL,
                model=model,
                embedding_model=settings.EMBEDDING_MODEL,
                temperature=float(os.getenv('LLM_TEMPERATURE', '0.7')),
                max_tokens=int(os.getenv('LLM_MAX_TOKENS', '2000')),
            ))
        logger.info(f'RAG 使用全局默认模型：{settings.LLM_MODEL}')
        return self.llm

    async def rag_query(
        self,
        query: str,
        context_count: int = 3,
        stream: bool = False,
        user_id: Optional[int] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        document_id: Optional[str] = None,
        embedding_model: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """RAG查询（集成缓存优化）

        user_id: 用于缓存键隔离 + 向量检索按用户过滤，避免跨用户数据泄露。
        model/api_key: 角色级配置（两者都填才生效），用于覆盖全局默认模型。
        document_id: 可选，限定只在该文档内检索（按文档查询/预览场景）。
        """
        # === 结构化访问快捷路径：document_id 已知 + 结构化意图 -> 直接 Representation（无 Embedding/Chroma/LLM）===
        direct = self._structured_direct_chunks(query, document_id, user_id, model, api_key)
        if direct is not None:
            async for chunk in direct:
                yield chunk
            return

        # 按文档检索不走全局缓存，避免不同文档的查询结果被同一 cache key 污染
        use_cache = document_id is None
        cache_key = CacheKey.rag_query(query, context_count, user_id) if use_cache else None
        if use_cache:
            cached_result = await self.cache.get(cache_key)
            if cached_result:
                logger.info(f' RAG查询缓存命中：{query[:50]}...')
                yield cached_result
                return

        try:
            # 1. 检索相关文档（按归属用户 + 可选 document_id 过滤）
            # Chroma where 仅支持单操作符；多条件用 $and 包裹
            conds: List[Dict[str, Any]] = []
            if user_id is not None:
                conds.append({'user_id': user_id})
            if document_id:
                conds.append({'document_id': document_id})
            filter_dict: Optional[Dict[str, Any]] = (
                {'$and': conds} if len(conds) > 1 else (conds[0] if conds else None)
            )
            # 1. 检索 + Table-aware 扩展（普通语义查询保持 top-k；结构化表格访问精确读取）
            search_results = await self._retrieve_and_expand(
                query=query,
                k=context_count,
                filter_dict=filter_dict,
                user_id=user_id,
                embedding_model=embedding_model,
            )

            # 结构化表格访问结果：由程序直接输出（大数据不被 max_tokens 截断），lookup 交 LLM 叙述
            struct = next((c for c in search_results
                          if c.get('metadata', {}).get('chunk_type') == 'table_structured_access'), None)
            if struct:
                async for chunk in self._stream_structured_answer(query, struct, model, api_key, use_cache, cache_key):
                    yield chunk
                return

            # 2. 构建上下文
            context = self._build_context(search_results)

            # 3. 构建prompt
            messages = self._build_messages(query, context)

            # 4. 选择 LLM 并流式回答（含超大 Context 自动分页；完整结果按 cache key 缓存）
            async for chunk in self._answer_stream(query, context, None, model, api_key, use_cache, cache_key):
                yield chunk

        except Exception as e:
            logger.error(f'RAG查询失败：{e}')
            yield f'抱歉，查询过程中出现错误：{str(e)}'

    # ----------------- 结构化完整加载（Phase 1 修正：不受 row_group 数量限制） -----------------
    async def _answer_stream(
        self,
        query: str,
        context: str,
        history: Optional[List[Dict[str, str]]],
        model: Optional[str],
        api_key: Optional[str],
        use_cache: bool,
        cache_key: Optional[Any],
    ) -> AsyncIterator[str]:
        """统一流式回答：超大 Context 自动分页（不一次性塞入单个 prompt）、拼接流、按需缓存。"""
        llm = self._resolve_llm(model, api_key)
        pages = self._split_context(context)
        npages = len(pages)
        full_parts: List[str] = []
        for i, page in enumerate(pages):
            hint = (
                f"\n[系统注：以上是完整数据的第 {i + 1}/{npages} 页，"
                f"请仅把本页内容格式化为列表/条目，不要总结、不要声称已覆盖全部数据。]"
            ) if npages > 1 else ""
            messages = (
                self._build_messages_with_history(query, page + hint, history)
                if history else self._build_messages(query, page + hint)
            )
            async for chunk in llm.chat_completion(messages, stream=True):
                full_parts.append(chunk)
                yield chunk
        if use_cache and full_parts:
            full_response = ''.join(full_parts)
            if len(full_response) > 10 and '抱歉，查询过程中出现错误' not in full_response:
                await self.cache.set(cache_key, full_response, ttl=600)

    async def _stream_structured_answer(
        self,
        query: str,
        struct_chunk: Dict[str, Any],
        model: Optional[str],
        api_key: Optional[str],
        use_cache: bool,
        cache_key: Optional[Any],
    ) -> AsyncIterator[str]:
        """结构化访问结果输出路由。

        - lookup：事实文本由 Python 直接生成（已在 _resolve_structured_access 中精确定位并提取目标列），
          **LLM 不参与事实数据/列选择**，仅做自然语言包装（此处直接输出 Python 文本，零幻觉风险）。
        - 全量枚举/前N/后N/中间范围/多列（结果可能很大）：
          **由 Python 直接格式化并流式输出**，LLM 不参与事实数据生成，
          因此既不会编造、也绝不会被 max_tokens 截断（输入/输出分页在此分离）。
        """
        meta = struct_chunk.get('metadata', {})
        op = meta.get('operation')
        block = struct_chunk.get('content', '')

        if op == 'lookup':
            # Python 已直接生成事实文本（含目标列值），不再把整行交给 LLM 选列
            yield block
            if use_cache and cache_key:
                await self.cache.set(cache_key, block, ttl=600)
            return

        count = meta.get('count')
        header = (
            f"以下是根据表格结构化读取的结果（共 {count} 条，"
            f"已按原始顺序完整列出，非 top-k 抽样）：\n\n"
        )
        full = header + block
        yield header
        for line in block.split('\n'):
            yield line + '\n'
        if use_cache and cache_key:
            await self.cache.set(cache_key, full, ttl=600)

    def _split_context(self, context: str) -> List[str]:
        """Context 超过预算时按行切分为多页（页内尽量不截断数据行），避免单个超大 prompt。"""
        if len(context) <= MAX_ENUM_CONTEXT_CHARS:
            return [context]
        lines = context.split('\n')
        pages: List[str] = []
        cur: List[str] = []
        cur_len = 0
        for ln in lines:
            if cur and cur_len + len(ln) + 1 > MAX_ENUM_CONTEXT_CHARS:
                pages.append('\n'.join(cur))
                cur = []
                cur_len = 0
            cur.append(ln)
            cur_len += len(ln) + 1
        if cur:
            pages.append('\n'.join(cur))
        return pages or [context]

    # ----------------- 统一结构化表格访问层（Structured Table Access） -----------------
    # 设计目标：自然语言 -> 结构化访问请求 -> Structured Representation 精确读取 ->
    # Python 负责读取/切片/计数/多列/格式化（保证完整性），LLM 仅负责自然语言说明，
    # 绝不重新生成事实数据；大数据枚举由程序直接输出，不受 max_tokens 截断。
    @dataclass
    class StructuredTableRequest:
        operation: str                 # 'full_column' | 'first_n' | 'last_n' | 'range' | 'lookup'
        columns: List[str] = field(default_factory=list)  # 枚举类目标列 technical_name（full_column/first_n/last_n/range）
        limit: Optional[int] = None
        start: Optional[int] = None   # 1-based 含
        end: Optional[int] = None     # 1-based 含
        lookup_value: Optional[str] = None          # 精确查找值
        lookup_column: Optional[str] = None         # 精确查找的匹配列 technical_name（如 Order ID）
        return_columns: List[str] = field(default_factory=list)  # 精确查找要返回的列 technical_name（如 [Variation]）

    @staticmethod
    def _detect_structured_intent(query) -> Optional['RagService.StructuredTableRequest']:
        """将自然语言统一识别为结构化访问请求（不在此做列名解析，列名解析在拿到 rep 后进行）。"""
        q = query or ''
        # 1) 精确值查找（Order ID / 6+数字 / 引号值 + 查找意图词）
        lv = RagService._is_precise_lookup(q)
        if lv is not None:
            return RagService.StructuredTableRequest(operation='lookup', lookup_value=lv)
        # 2) 中间范围：第A到B（支持 到 / ~ / - / 至）
        m = re.search(r'第\s*(\d+)\s*(?:[到~至\-—]|~)\s*(\d+)\s*(?:条|行|个)?', q)
        if m:
            return RagService.StructuredTableRequest(operation='range', start=int(m.group(1)), end=int(m.group(2)))
        # 3) 前N
        m = re.search(r'前\s*(\d+)\s*(?:条|行|个)', q)
        if m:
            return RagService.StructuredTableRequest(operation='first_n', limit=int(m.group(1)))
        # 4) 最后N / 后N
        m = re.search(r'(?:最后|后)\s*(\d+)\s*(?:条|行|个)', q)
        if m:
            return RagService.StructuredTableRequest(operation='last_n', limit=int(m.group(1)))
        # 5) 全量枚举
        if RagService._is_full_enumeration_query(q):
            return RagService.StructuredTableRequest(operation='full_column')
        return None

    @staticmethod
    def _match_all_columns(query, columns) -> List[str]:
        """返回 query 中出现的全部真实列 technical_name（支持空格/大小写/中文/特殊字符）。"""
        found = []
        q = query or ''
        for c in columns:
            for nm in (c.get('technical_name'), c.get('display_name'), c.get('description')):
                if nm and nm in q:
                    found.append(c.get('technical_name'))
                    break
        return found

    @staticmethod
    def _strip_quantity_tokens(query: str) -> str:
        """前N/后N/中间范围 中的数字是“数量/范围”，不是列名。

        剥离这些数字片段，避免把数字列名（如真实存在的「50」列）误当作列引用。
        显式列引用（如“把 50 列出来”）不含 前/后/第…条 结构，不会被误删。
        """
        q = query or ''
        q = re.sub(r'前\s*\d+\s*(?:条|行|个)', '', q)
        q = re.sub(r'(?:最后|后)\s*\d+\s*(?:条|行|个)', '', q)
        q = re.sub(r'第\s*\d+\s*(?:[到~至\-—]|~)\s*\d+\s*(?:条|行|个)?', '', q)
        return q

    def _resolve_structured_access(self, doc_id, sheet_name, table_id, user_id, query, intent):
        """执行结构化访问请求，返回 (block: str, meta: dict) 或 None（rep 缺失/参数无效时退回）。

        Python 精确读取：切片/计数/多列/精确匹配全部在此完成，结果完整且按原始顺序。
        """
        rep = load_representation(doc_id)
        if rep is None:
            return None
        table = self._locate_table(rep, sheet_name, table_id)
        if table is None:
            return None
        columns = table.get('columns', [])
        rows = table.get('rows', [])
        if not rows:
            return None
        op = intent.operation

        # ---- 精确值查找：Python 精确定位 + 提取目标列（LLM 不参与事实列选择，杜绝列误归属） ----
        if op == 'lookup':
            value = intent.lookup_value
            columns_by_name = {c['technical_name']: c for c in columns}
            # 1) 解析匹配列与返回列（统一在拿到真实列后做，避免凭列序号/字母误判）
            lookup_col, return_cols = self._resolve_lookup_targets(query, columns, value)
            # 2) 扫描定位匹配行（指定 lookup_col 时仅扫该列；否则全表扫描）
            matched = []
            if lookup_col and lookup_col in columns_by_name:
                ci = columns_by_name[lookup_col]['col_index'] - 1
                for r in rows:
                    cells = r.get('cells', [])
                    v = cells[ci].get('value') if ci < len(cells) else None
                    if v is not None and self._norm(str(v)) == self._norm(value):
                        matched.append(r)
            else:
                for r in rows:
                    for cell in r.get('cells', []):
                        v = cell.get('value')
                        if v is not None and self._norm(str(v)) == self._norm(value):
                            matched.append(r)
                            break
            # 3) Python 直接读取目标列值（绝不交 LLM 从整行自选列）
            out_cols = return_cols if return_cols else [c['technical_name'] for c in columns]
            result_rows = []
            for r in matched:
                d = {}
                for cn in out_cols:
                    co = columns_by_name.get(cn)
                    if co is None:
                        d[cn] = ''
                        continue
                    ci = co['col_index'] - 1
                    cells = r.get('cells', [])
                    v = cells[ci].get('value') if ci < len(cells) else None
                    d[cn] = RagService._cell_text(v)
                result_rows.append({'row_index': r['row_index'], 'values': d})
            # 4) Python 直接生成事实文本（含目标列值），自然语言也由 Python 组装
            block = self._format_lookup(result_rows, lookup_col, value, out_cols,
                                        sheet_name, table, len(rows))
            meta = {'operation': 'lookup', 'lookup_value': value,
                    'lookup_column': lookup_col, 'return_columns': ','.join(out_cols),
                    'matched': len(matched), 'count': len(matched), 'total_rows': len(rows),
                    'mode': 'lookup', 'row_start': matched[0]['row_index'] if matched else None,
                    'row_end': matched[-1]['row_index'] if matched else None}
            return block, meta

        # ---- 列解析：从真实列名匹配 query 中出现的列（多列 -> 多列读取） ----
        # 前N/后N/中间范围 中的数字是“数量/范围”，不是列名，先剥离避免误把数字列名当列引用
        col_query = self._strip_quantity_tokens(query) if intent.operation in ('first_n', 'last_n', 'range') else query
        cols_mentioned = self._match_all_columns(col_query, columns)
        if op == 'full_column':
            target_cols = cols_mentioned if cols_mentioned else []
        else:  # first_n / last_n / range：未指定列时退化为整表切片
            target_cols = cols_mentioned

        n = len(rows)
        if op == 'first_n':
            limit = max(0, intent.limit or 0)
            sliced = rows[:limit] if limit > 0 else []
        elif op == 'last_n':
            limit = max(0, intent.limit or 0)
            sliced = rows[-limit:] if limit > 0 else []
        elif op == 'range':
            s, e = intent.start, intent.end
            if s is None or e is None:
                return None
            if s > e:
                s, e = e, s
            sliced = rows[s - 1:e]
        else:
            sliced = rows

        block = self._format_structured(sliced, columns, target_cols, op, sheet_name, table, intent)
        meta = {
            'operation': op,
            'columns': ','.join(target_cols) if target_cols else 'ALL',
            'count': len(sliced),
            'total_rows': n,
            'mode': 'structured_access',
            'row_start': sliced[0]['row_index'] if sliced else None,
            'row_end': sliced[-1]['row_index'] if sliced else None,
        }
        return block, meta

    @staticmethod
    def _format_structured(rows, columns, target_cols, op, sheet_name, table, intent) -> str:
        """把切片后的行格式化为可读块（多列保持行级对应）。"""
        tid = table.get('table_id')
        label = {
            'full_column': '全量',
            'first_n': f"前{intent.limit}条",
            'last_n': f"最后{intent.limit}条",
            'range': f"第{intent.start}到{intent.end}条",
        }.get(op, op)

        if not target_cols:
            header = (
                f"工作表「{sheet_name}」表 {tid} 整表 {len(rows)} 条数据行"
                f"（已按原始顺序完整读取，非 top-k 抽样）：\n"
            )
            return header + RagService._format_rows(rows, columns)

        if len(target_cols) == 1:
            cname = target_cols[0]
            ci = next(c['col_index'] for c in columns if c['technical_name'] == cname) - 1
            lines = []
            for r in rows:
                cells = r.get('cells', [])
                v = cells[ci].get('value') if ci < len(cells) else None
                lines.append(f"第{r['row_index']}行: {RagService._cell_text(v)}")
            header = (
                f"工作表「{sheet_name}」表 {tid} 字段「{cname}」的{label}共 {len(rows)} 个值"
                f"（按行顺序，含空值，未去重，非 top-k 抽样）：\n"
            )
            return header + "\n".join(lines)

        # 多列：行级对应
        col_objs = [next(c for c in columns if c['technical_name'] == name) for name in target_cols]
        lines = []
        for r in rows:
            cells = r.get('cells', [])
            parts = []
            for c in col_objs:
                ci = c['col_index'] - 1
                v = cells[ci].get('value') if ci < len(cells) else None
                parts.append(f"{c['technical_name']}={RagService._cell_text(v)}")
            lines.append(f"第{r['row_index']}行: " + " | ".join(parts))
        header = (
            f"工作表「{sheet_name}」表 {tid} 多字段{label}（" + ", ".join(target_cols) +
            f"）共 {len(rows)} 行（按行级对应，非 top-k 抽样）：\n"
        )
        return header + "\n".join(lines)

    @staticmethod
    def _locate_table(rep, sheet_name, table_id):
        for s in rep.get('workbook', {}).get('sheets', []):
            if sheet_name and s.get('sheet_name') != sheet_name:
                continue
            for t in s.get('tables', []):
                if table_id and t.get('table_id') != table_id:
                    continue
                return t
        return None

    @staticmethod
    def _first_table(rep):
        """返回文档第一个表的 (sheet_name, table_id)，用于跨文档兜底查找。"""
        for s in rep.get('workbook', {}).get('sheets', []):
            if s.get('tables'):
                t = s['tables'][0]
                return (s.get('sheet_name'), t.get('table_id'))
        return None

    @staticmethod
    def _cell_text(v) -> str:
        """单元格文本化：合并换行/制表为空格，保证一条逻辑记录 = 一行物理文本（避免行解析错位）。"""
        if v is None:
            return ''
        return re.sub(r'[\r\n\t]+', ' ', str(v)).strip()

    @staticmethod
    def _format_rows(rows, columns):
        if not columns:
            return "\n".join(str(r) for r in rows)
        header = "行号 | " + " | ".join(c.get('col_letter') for c in columns)
        out = [header]
        for r in rows:
            cells = r.get('cells', [])
            vals = []
            for i in range(len(columns)):
                cell = cells[i] if i < len(cells) else None
                v = cell.get('value') if cell else None
                vals.append(RagService._cell_text(v))
            out.append(f"{r.get('row_index')} | " + " | ".join(vals))
        return "\n".join(out)

    @staticmethod
    def _norm(s):
        return re.sub(r'\s+', '', str(s)).lower()

    def _resolve_lookup_targets(self, query, columns, value):
        """解析精确查找的匹配列与返回列（仅依赖真实 Representation.columns 的列名，不写死列序号/字母）。

        返回 (lookup_column, return_columns)：
        - lookup_column：query 中带匹配词（为/是/=/：/等于）的列，即“X 为 value”里的 X。
        - return_columns：query 中被提及、且非 lookup_column 的列（如“X 为 v 的 Variation”→['Variation']）。
          “X 为 v 的 Variation 和 SKU ID”→['Variation','SKU ID']。未指定则返回空（退化为返回全部列）。
        """
        q = query or ''
        mentioned = self._match_all_columns(q, columns)  # query 中出现的真实列 technical_name
        lookup_col = None
        for cn in mentioned:
            if re.search(re.escape(cn) + r'\s*(?:为|=|:|：|等于|是)\s', q):
                lookup_col = cn
                break
        return_cols = [cn for cn in mentioned if cn != lookup_col]
        return lookup_col, return_cols

    @staticmethod
    def _format_lookup(result_rows, lookup_col, value, out_cols, sheet_name, table, total_rows):
        """把 Python 已确定的 lookup 结果格式化为最终文本（事实值全部来自 Python，LLM 不参与）。"""
        tid = table.get('table_id')
        if not result_rows:
            return (f"已在工作表「{sheet_name}」表 {tid} 中精确扫描全部 {total_rows} 条数据行，"
                    f"未找到值「{value}」。（已完整覆盖，非 top-k 抽样）")
        subj = f"{lookup_col} 为 {value}" if lookup_col else f"值「{value}」"
        if len(result_rows) == 1:
            r = result_rows[0]
            if len(out_cols) == 1:
                col = out_cols[0]
                val = r['values'][col]
                # Python 组装的自然语言 + 明确事实块（值来自 Python 已确定的结构化结果）
                return (f"{subj} 对应的 {col} 是「{val}」。\n\n"
                        f"{col}：{val}\n行号：{r['row_index']}")
            lines = [f"已为您查到 {subj} 的信息（行{r['row_index']}）：", ""]
            for col in out_cols:
                lines.append(f"{col}：{r['values'][col]}")
            return "\n".join(lines)
        # 多匹配：逐条列出，全部由 Python 提取
        lines = [f"共找到 {len(result_rows)} 条与「{value}」匹配的记录"
                 f"（已完整扫描 {total_rows} 行）：", ""]
        for i, r in enumerate(result_rows, 1):
            lines.append(f"记录 {i}（行{r['row_index']}）：")
            for col in out_cols:
                lines.append(f"  {col}：{r['values'][col]}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _extract_lookup_value(query):
        q = query or ""
        m = re.search(r'["\']([0-9A-Za-z_\-]{4,})["\']', q)
        if m:
            return m.group(1)
        for pat in (r'Order\s*ID\s*(?:为|=|:|：)?\s*([0-9A-Za-z_\-]{4,})',
                    r'订单号\s*(?:为|=)?\s*([0-9A-Za-z_\-]{4,})',
                    r'订单\s*(?:ID|id)?\s*(?:为|=)?\s*([0-9A-Za-z_\-]{4,})'):
            m = re.search(pat, q, re.IGNORECASE)
            if m:
                return m.group(1)
        m = re.search(r'\d{6,}', q)
        if m:
            return m.group(0)
        return None

    @staticmethod
    def _is_precise_lookup(query):
        val = RagService._extract_lookup_value(query)
        if val is None:
            return None
        ql = (query or "").lower()
        return val if any(k.lower() in ql for k in _LOOKUP_INTENT) else None

    @staticmethod
    def _build_context(search_results: List[Dict[str, Any]]) -> str:
        """构建上下文"""
        if not search_results:
            return '没有找到相关文档内容'

        context_part = []
        for i, result in enumerate(search_results, 1):
            content = result['content']
            meta = result['metadata']
            # 真实来源文件名优先（filename 干净无 range 后缀；退化用 source）
            fname = meta.get('filename') or meta.get('source') or '未知来源'
            context_part.append(f'[来源：{fname}]:\n{content}\n')

        return '\n---\n'.join(context_part)

    # ----------------- Table-aware Retrieval（Phase 1） -----------------
    @staticmethod
    def _is_full_enumeration_query(query: str) -> bool:
        """判断是否为“全量枚举/全量提取”类查询（方案 A 关键词规则，纯本地、零成本）。

        双闸门之一：仅当本函数返回 True（或命中精确值查找）且 semantic top-k 命中 table chunk 时才扩展。
        """
        q = query or ""
        for p in _ENUM_PHRASES:
            if p in q:
                return True
        # 全量修饰词 + 枚举动词 同时出现（覆盖“把所有 X 列出来”等句式）
        if any(f in q for f in _ENUM_FULL) and any(v in q for v in _ENUM_VERBS):
            return True
        for b in _ENUM_BARE:
            idx = q.find(b)
            while idx != -1:
                window = q[idx: idx + 8]
                if any(n in window for n in _ENUM_NOUNS):
                    return True
                idx = q.find(b, idx + 1)
        return False

    async def _retrieve_and_expand(
        self,
        query: str,
        k: int,
        filter_dict: Optional[Dict[str, Any]],
        user_id: Optional[int],
        embedding_model: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """语义检索 + Table-aware 扩展（共用入口，rag_query 与 query_with_history 均经此）。

        普通语义查询：返回 Chroma semantic top-k（原行为）。
        结构化表格访问（全量枚举/前N/后N/中间范围/多列/精确值查找）：统一经 StructuredTableRequest
        从 Structured Representation 精确读取，保证不丢行/不丢列/按原始顺序，与 row_group 数量无关；
        rep 缺失时退回完整 row_group（不再截断）。
        """
        search_results = await self.vector_store.search(query=query, k=k, filter_dict=filter_dict, embedding_model=embedding_model)
        # 统一结构化访问意图识别：全量枚举 / 前N / 后N / 中间范围 / 精确值查找
        intent = self._detect_structured_intent(query)
        if intent is None:
            return search_results
        # 闸门2：top-k 必须命中 table chunk，否则不触发（避免影响普通 TXT/MD/DOCX/PDF RAG）
        table_chunks = [
            r for r in search_results
            if r.get('metadata', {}).get('chunk_type') in ('workbook_summary', 'sheet_schema', 'row_group')
        ]
        if not table_chunks:
            return search_results
        # 目标表识别：取 semantic score 最高的 table chunk 的 (document_id, sheet_name, table_id)
        # —— 多表场景下只扩展该表，避免把 A+B+C 的 row_group 全拼入 Context
        best = max(table_chunks, key=lambda r: r.get('score', 0)).get('metadata', {})
        doc_id = best.get('document_id')
        sheet_name = best.get('sheet_name')
        table_id = best.get('table_id')
        if not doc_id:
            return search_results

        # 统一结构化访问层：从 Structured Representation 精确读取（与 row_group 数量无关）
        if intent.operation == 'lookup':
            # 全局精确查找：语义检索打分最高的表未必包含该精确值（可能选错文档，
            # 例如“的 Variation”检索把 19 行的一店表排在 448 行的五店表之前），
            # 因此遍历检索候选文档并在未命中时兜底扫描用户全部文档，命中即返回，确保不漏检。
            cand_docs = []
            seen = set()
            for r in sorted(table_chunks, key=lambda r: r.get('score', 0), reverse=True):
                m = r.get('metadata', {})
                did = m.get('document_id')
                key = (did, m.get('sheet_name'), m.get('table_id'))
                if did and key not in seen:
                    seen.add(key)
                    cand_docs.append((did, m.get('sheet_name'), m.get('table_id'), m))

            def _try_doc(did, sn, tid, meta_base):
                rr = self._resolve_structured_access(did, sn, tid, user_id, query, intent)
                if rr is not None and rr[1].get('matched', 0) > 0:
                    block, meta = rr
                    meta.update({
                        'document_id': did,
                        'chunk_type': 'table_structured_access',
                        'sheet_name': sn,
                        'table_id': tid,
                        'filename': (meta_base or {}).get('filename') or '',
                        'source': (meta_base or {}).get('filename') or '',
                        'chunk_index': 0,
                    })
                    logger.info(f'Table-aware 结构化访问(lookup跨文档命中)：doc={did} '
                                f'matched={meta.get("matched")}')
                    return [{'id': f'{did}_struct_0', 'content': block, 'metadata': meta}]
                return None

            for (did, sn, tid, m) in cand_docs:
                hit = _try_doc(did, sn, tid, m)
                if hit:
                    return hit
            # 兜底：候选文档均未命中 → 扫描用户全部文档（精确值可能落在未被检索命中的文档）
            try:
                all_docs = self.vector_store.list_user_documents(user_id) or []
            except Exception:
                all_docs = []
            tried_dids = {c[0] for c in cand_docs}
            for d in all_docs:
                did = d.get('document_id')
                if not did or did in tried_dids:
                    continue
                tried_dids.add(did)
                try:
                    rep = load_representation(did)
                except Exception:
                    continue
                if not rep:
                    continue
                tgt = RagService._first_table(rep)
                if not tgt:
                    continue
                hit = _try_doc(did, tgt[0], tgt[1], {'filename': d.get('filename')})
                if hit:
                    return hit
            # 全未命中：返回最佳文档的诚实未找到（沿用原路径）
            res = self._resolve_structured_access(doc_id, sheet_name, table_id, user_id, query, intent)
        else:
            res = self._resolve_structured_access(doc_id, sheet_name, table_id, user_id, query, intent)
        if res is not None:
            block, meta = res
            meta.update({
                'document_id': doc_id,
                'chunk_type': 'table_structured_access',
                'sheet_name': sheet_name,
                'table_id': table_id,
                'filename': best.get('filename') or '',
                'source': best.get('filename') or '',
                'chunk_index': 0,
            })
            logger.info(f'Table-aware 结构化访问：operation={meta.get("operation")} '
                        f'columns={meta.get("columns")} count={meta.get("count")}')
            return [{
                'id': f'{doc_id}_struct_0',
                'content': block,
                'metadata': meta,
            }]

        # rep 不可用：退回完整 row_group（不再因数量 > 阈值而截断，避免“部分召回却声称全部”）
        row_groups = self.vector_store.get_table_row_groups(
            document_id=doc_id, sheet_name=sheet_name, table_id=table_id, user_id=user_id)
        if len(row_groups) > settings.TABLE_FULL_LOAD_MAX_ROW_GROUPS:
            logger.warning(
                f'Table-aware：rep 缺失且 row_group 数 {len(row_groups)} 超过阈值 '
                f'{settings.TABLE_FULL_LOAD_MAX_ROW_GROUPS}，仍完整加载（不截断）'
            )
        if not row_groups:
            return search_results
        aux = self.vector_store.get_table_aux_chunks(
            document_id=doc_id, sheet_name=sheet_name, table_id=table_id, user_id=user_id)
        merged: Dict[Any, Dict[str, Any]] = {}
        for c in list(row_groups) + aux:
            cid = c.get('id') or c.get('metadata', {}).get('chunk_index')
            if cid not in merged:
                merged[cid] = c
        ordered = sorted(merged.values(),
                         key=lambda c: (c.get('metadata', {}).get('row_start', 10 ** 9), 0))
        logger.info(
            f'Table-aware 扩展生效(rep缺失)：document_id={doc_id} sheet={sheet_name} '
            f'row_group={len(row_groups)} -> Context 行覆盖 '
            f'{sum((c["metadata"].get("row_end", 0) - c["metadata"].get("row_start", 0) + 1) for c in row_groups)}'
        )
        return ordered

    @staticmethod
    def _build_messages(
        query: str,
        context: str
    ) -> List[Dict[str, str]]:
        """构建消息列表"""

        system_prompt = """
        你是一个专业的AI助手，基于提供的文档内容回答问题。
        
        请遵守以下规则：
        1. 仅基于提供的上下文回答问题
        2. 如果上下文不包含相关信息，请如实说明你不知道
        3. 保持回答准确、简洁、有用
        4. 可以引用上下文中的具体内容
        5. 引用知识库内容时，请使用上下文中标注的实际来源文件名（例如《xxx.xlsx》），不要使用“文档1”这类编号
        6. 当用户要求列出全部/所有/每一行/每个条目时，请逐行完整列出，保留每一行（即使内容重复也不要去重或省略）
        
        上下文内容：
        {context}
        """

        return [
            {
                'role': 'system',
                'content': system_prompt.format(context=context)
            },
            {
                'role': 'user',
                'content': query
            }
        ]

    async def query_with_history(
        self,
        query: str,
        history: List[Dict[str, str]],
        context_count: int = 3,
        stream: bool = False,
        user_id: Optional[int] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        document_id: Optional[str] = None,
        embedding_model: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """带历史记录的查询（按归属用户过滤向量检索）

        model/api_key: 角色级配置（两者都填才生效），用于覆盖全局默认模型。
        document_id: 可选，限定只在该文档内检索。
        """
        try:
            # === 结构化访问快捷路径（document_id 已知 + 结构化意图）===
            direct = self._structured_direct_chunks(query, document_id, user_id, model, api_key)
            if direct is not None:
                async for chunk in direct:
                    yield chunk
                return

            # 1. 检索相关文档（按归属用户 + 可选 document_id 过滤）
            # Chroma where 仅支持单操作符；多条件用 $and 包裹
            conds: List[Dict[str, Any]] = []
            if user_id is not None:
                conds.append({'user_id': user_id})
            if document_id:
                conds.append({'document_id': document_id})
            filter_dict: Optional[Dict[str, Any]] = (
                {'$and': conds} if len(conds) > 1 else (conds[0] if conds else None)
            )
            # 1. 检索 + Table-aware 扩展（普通语义查询保持 top-k；结构化表格访问精确读取）
            search_results = await self._retrieve_and_expand(
                query=query,
                k=context_count,
                filter_dict=filter_dict,
                user_id=user_id,
                embedding_model=embedding_model,
            )

            # 结构化表格访问结果：由程序直接输出（大数据不被 max_tokens 截断），lookup 交 LLM 叙述
            struct = next((c for c in search_results
                          if c.get('metadata', {}).get('chunk_type') == 'table_structured_access'), None)
            if struct:
                async for chunk in self._stream_structured_answer(query, struct, model, api_key, False, None):
                    yield chunk
                return

            # 2. 构建上下文
            context = self._build_context(search_results)

            # 3. 构建带历史的消息
            messages = self._build_messages_with_history(query, context, history)

            # 4. 选择 LLM 并流式回答（含超大 Context 自动分页）
            async for chunk in self._answer_stream(query, context, history, model, api_key, False, None):
                yield chunk

        except Exception as e:
            logger.error(f'带历史查询失败：{e}')
            yield f'抱歉，查询过程中出现错误：{str(e)}'

    @staticmethod
    def _build_messages_with_history(
        query: str,
        context: str,
        history: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        """构建带历史记录的消息"""

        system_prompt = """
        你是一个专业的AI助手，基于提供的文档内容和对话历史回答问题
        
        请遵守以下规则：
        1. 基于提供的上下文和对话历史回答问题
        2. 保持对话的连贯性
        3. 如果上下文不包含相关信息，请如实说明你不知道
        4. 保持回答准确、简洁、有用
        5. 引用知识库内容时，请使用上下文中标注的实际来源文件名（例如《xxx.xlsx》），不要使用“文档1”这类编号
        6. 当用户要求列出全部/所有/每一行/每个条目时，请逐行完整列出，保留每一行（即使内容重复也不要去重或省略）
        
        文档上下文：
        {context}
        
        对话历史：
        """

        # 添加历史记录（限制最后5轮）
        history_prompt = ''
        recent_history = history[-10:]  # 最近5轮对话
        for msg in recent_history:
            role = '用户' if msg['role'] == 'user' else '助手'
            history_prompt += f'{role}: {msg["content"]}\n'

        full_system_prompt = system_prompt.format(context=context) + history_prompt

        messages = [
            {
                'role': 'system',
                'content': full_system_prompt
            },
            {
                'role': 'user',
                'content': query
            }
        ]

        return messages


# 单例实例
_rag_service = None


def get_rag_service() -> RagService:
    """获取RAG服务单例"""
    global _rag_service
    if _rag_service is None:
        _rag_service = RagService()
    return _rag_service
