# -*- coding: utf-8 -*-
"""
Phase 3 统一 Table QA 调度层（只读编排，不重写 Phase 1 / Phase 2）。

设计约束（来自需求）：
- 只做统一调度：识别 Intent、选择路径、透传 user_id/document_id/history、
  统一结果格式、统一 Source、统一 Streaming、统一错误类型。
- 不重写：Chroma / Table-aware Retrieval / Structured Access / DuckDB /
  NL2SQL / SQL Validator（这些能力均已冻结）。
- Router 为【确定性规则 + Schema】，不引入 Router LLM（避免一次请求多次模型调用）。
- 保留 Route Confidence（HIGH / MEDIUM / LOW），由规则命中强度计算，不依赖 LLM。

Intent 边界（关键）：
- SCHEMA        : 结构/字段/列/数据类型/Sheet 结构（"有哪些字段"/"多少列"）。
- SEMANTIC_...  : 业务语义/术语含义/数据定位（"Seller SKU 是什么意思"/"在哪里"）。
                  "XX 是什么意思" 归 SEMANTIC，不归 SCHEMA。
- STRUCTURED_.. : 全量枚举/前N/后N/中间范围/精确值查找（复用 Phase 1 现有
                  _detect_structured_intent）。STRUCTURED 优先于 PRECISE，但
                  "多少个/总和/平均/最高" 等【计数问题】明确归 PRECISE（COUNT/聚合），
                  不误判为枚举。
- PRECISE_QUERY : 聚合/计算/筛选/排序/计数（路由到 Phase 2 DuckDB NL2SQL）。
- AMBIGUOUS     : 无法规则判定（LOW），返回澄清，不盲目执行。
- UNSUPPORTED   : 跨文档联合聚合（Phase 2 当前单表，不支持）。
"""
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, AsyncGenerator, Dict, List, Optional

from backend.services.rag_service import get_rag_service, RagService
from backend.services.table_query_service import TableQueryService
from backend.services.table_representation import load_representation

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    SCHEMA = "SCHEMA"
    SEMANTIC_RETRIEVAL = "SEMANTIC_RETRIEVAL"
    STRUCTURED_ACCESS = "STRUCTURED_ACCESS"
    PRECISE_QUERY = "PRECISE_QUERY"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ErrorType(str, Enum):
    NO_DOCUMENT = "NO_DOCUMENT"
    NO_MATCH = "NO_MATCH"
    SQL_GENERATION_FAILED = "SQL_GENERATION_FAILED"
    SQL_VALIDATION_REJECTED = "SQL_VALIDATION_REJECTED"
    DUCKDB_EXECUTION_FAILED = "DUCKDB_EXECUTION_FAILED"
    CHROMA_RETRIEVAL_FAILED = "CHROMA_RETRIEVAL_FAILED"
    AMBIGUOUS_NEED_CLARIFICATION = "AMBIGUOUS_NEED_CLARIFICATION"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass
class RouteDecision:
    intent: Intent
    confidence: Confidence
    reason: str
    execute: bool = True
    error_type: Optional[ErrorType] = None
    message: Optional[str] = None


# ---------------- 确定性意图识别（纯规则，0 次 LLM 调用） ----------------

# SCHEMA：结构/字段/列/数据类型/Sheet 结构（注意：不含裸“类型”，避免与“是什么类型”冲突）
_SCHEMA_PAT = re.compile(
    r"字段|列名|列数|多少列|几列|表头|数据类型|类型是什么|结构|schema|表结构",
    re.IGNORECASE,
)

# SEMANTIC 含义（业务术语/字段含义）：优先于 SCHEMA 判定
_SEM_MEANING = re.compile(
    r"是什么意思|含义|指的是|怎么理解|业务上.{0,6}意思|属于什么|是什么类型",
    re.IGNORECASE,
)

# SEMANTIC 定位（数据在哪里 / 关于什么）
_SEM_LOCATE = re.compile(r"在哪里|相关的数据|主要记录|关于|描述一下", re.IGNORECASE)

# 计数/聚合问题：决定走 PRECISE 而非 STRUCTURED（即使命中枚举词）
_COUNT_PAT = re.compile(
    r"多少个|多少条|多少单|多少行|总数|总和|合计|总计|平均|均值|最高|最低|"
    r"最大|最小|排名|分别有?多少|每个.+分别|卖了多少|卖得最多|卖得最好|销量|"
    r"TOP|前\s*\d+\s*个|大于|超过|不低于|不少于|少于|小于|等于",
    re.IGNORECASE,
)

# 业务默认计数（中置信度）：无显式度量词，但“X 有多少”式
_BIZ_DEFAULT_PAT = re.compile(
    r"(订单|记录|行数|SKU).{0,6}多少|多少.{0,6}(订单|记录|行数|SKU)|"
    r"多少个\s*SKU|多少\s*SKU|多少\s*订单",
    re.IGNORECASE,
)

# 跨文档联合聚合（当前不支持）
_CROSS_DOC_PAT = re.compile(
    r"所有(文件|文档|Excel|表格|表)|全部(文件|文档|Excel|表格)|加起来|"
    r"跨(文件|文档|表)",
    re.IGNORECASE,
)

# 显式度量词（用于区分 PRECISE HIGH vs MEDIUM 业务默认）
_EXPLICIT_MEASURE = re.compile(
    r"总和|平均|最高|最低|最大|最小|排名|销量|大于|超过|每个.+分别|TOP|前\s*\d+\s*个",
    re.IGNORECASE,
)


class TableQAService:
    """统一 Table QA 调度层（Phase 1 / Phase 2 的只读编排者）。"""

    def __init__(self):
        self.rag = get_rag_service()
        self.tq = TableQueryService()

    # =================== 1) Intent Router（确定性规则） ===================
    def classify(self, query: str) -> RouteDecision:
        q = (query or "").strip()
        if not q:
            return RouteDecision(
                Intent.AMBIGUOUS, Confidence.LOW, "空问题", execute=False,
                error_type=ErrorType.AMBIGUOUS_NEED_CLARIFICATION,
                message="问题为空，请描述您想了解的表格内容。",
            )

        # 0) 跨文档联合聚合 -> 不支持（Phase 2 当前单表）
        if _CROSS_DOC_PAT.search(q) and _COUNT_PAT.search(q):
            return RouteDecision(
                Intent.UNSUPPORTED, Confidence.MEDIUM, "跨文档联合聚合未支持",
                execute=False, error_type=ErrorType.UNSUPPORTED_OPERATION,
                message="当前版本不支持跨多个文件的联合聚合查询，请指定单个文件后再提问。",
            )

        # 1) SEMANTIC 含义（优先于 SCHEMA，处理“XX 是什么意思”）
        if _SEM_MEANING.search(q):
            if re.search(r"是什么意思|含义|指的是|怎么理解|业务上.{0,6}意思", q):
                return RouteDecision(
                    Intent.SEMANTIC_RETRIEVAL, Confidence.HIGH,
                    "术语/业务含义 -> Phase 1 semantic retrieval",
                )
            return RouteDecision(
                Intent.SEMANTIC_RETRIEVAL, Confidence.MEDIUM,
                "类型/归属含义 -> Phase 1 semantic retrieval",
            )

        # 2) SCHEMA（结构/字段/列/类型）
        if _SCHEMA_PAT.search(q):
            return RouteDecision(
                Intent.SCHEMA, Confidence.HIGH,
                "结构/字段类问题 -> Phase 1 schema retrieval",
            )

        # 3) STRUCTURED_ACCESS（复用 Phase 1 现有 _detect_structured_intent，
        #    但“计数问题”明确归 PRECISE，避免“一共有多少个 SKU”误判为枚举）
        is_count = bool(_COUNT_PAT.search(q))
        struct_intent = RagService._detect_structured_intent(q)
        if struct_intent is not None and not is_count:
            return RouteDecision(
                Intent.STRUCTURED_ACCESS, Confidence.HIGH,
                f"结构化访问({struct_intent.operation}) -> Phase 1 structured access",
            )

        # 4) PRECISE_QUERY（聚合/计算/筛选/排序/计数）
        if is_count or _BIZ_DEFAULT_PAT.search(q):
            if _BIZ_DEFAULT_PAT.search(q) and not _EXPLICIT_MEASURE.search(q):
                return RouteDecision(
                    Intent.PRECISE_QUERY, Confidence.MEDIUM,
                    "业务默认计数(订单/记录/SKU) -> Phase 2 PRECISE_QUERY",
                )
            return RouteDecision(
                Intent.PRECISE_QUERY, Confidence.HIGH,
                "聚合/计算/筛选/排序/计数 -> Phase 2 DuckDB NL2SQL",
            )

        # 5) SEMANTIC 定位（数据在哪里 / 关于什么）
        if _SEM_LOCATE.search(q):
            return RouteDecision(
                Intent.SEMANTIC_RETRIEVAL, Confidence.MEDIUM,
                "定位/相关数据 -> Phase 1 semantic retrieval",
            )

        # 6) AMBIGUOUS（LOW，需澄清）
        return RouteDecision(
            Intent.AMBIGUOUS, Confidence.LOW, "无法基于规则判定意图", execute=False,
            error_type=ErrorType.AMBIGUOUS_NEED_CLARIFICATION,
            message=("您的问题含义不够明确。请补充：是指某一行的数值、总和、平均、"
                     "还是最大/最小值？或指定具体字段名与文件。"),
        )

    # =================== 2) 统一执行入口 ===================
    async def run(
        self,
        user_id: Any,
        question: str,
        document_id: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """非流式统一执行，返回统一格式字典。"""
        dec = self.classify(question)
        base = {
            "route": dec.intent.value,
            "intent": dec.intent.value,
            "confidence": dec.confidence.value,
            "reason": dec.reason,
        }
        if not dec.execute:
            return {
                **base, "execute": False,
                "error_type": dec.error_type.value if dec.error_type else None,
                "message": dec.message,
                "answer": dec.message,
                "sources": [],
            }

        if dec.intent in (Intent.SCHEMA, Intent.SEMANTIC_RETRIEVAL, Intent.STRUCTURED_ACCESS):
            return await self._run_phase1(dec, user_id, question, document_id, history, base)
        return await self._run_phase2(dec, user_id, question, document_id, base)

    async def stream(
        self,
        user_id: Any,
        question: str,
        document_id: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncGenerator[str, None]:
        """流式统一执行，输出 ndjson 帧（与现有端点协议对齐）。"""
        dec = self.classify(question)
        base = {
            "route": dec.intent.value,
            "intent": dec.intent.value,
            "confidence": dec.confidence.value,
        }
        ts = lambda: datetime.now().isoformat()

        if not dec.execute:
            yield json.dumps({
                **base, "type": "error",
                "error_type": dec.error_type.value if dec.error_type else "CLARIFICATION",
                "message": dec.message, "timestamp": ts(),
            }) + "\n"
            return

        if dec.intent in (Intent.SCHEMA, Intent.SEMANTIC_RETRIEVAL, Intent.STRUCTURED_ACCESS):
            try:
                gen = (
                    self.rag.query_with_history(
                        query=question, history=history or [],
                        user_id=user_id, document_id=document_id,
                    )
                    if history else
                    self.rag.rag_query(
                        query=question, user_id=user_id, document_id=document_id,
                    )
                )
                async for ch in gen:
                    yield json.dumps({**base, "type": "chunk", "content": ch, "timestamp": ts()}) + "\n"
            except Exception as e:  # noqa: BLE001
                logger.error(f"Phase1 TableQA 流式失败：{e}")
                yield json.dumps({
                    **base, "type": "error",
                    "error_type": ErrorType.CHROMA_RETRIEVAL_FAILED.value,
                    "message": str(e), "timestamp": ts(),
                }) + "\n"
                return
            sources = self._phase1_sources(document_id) if document_id else []
            yield json.dumps({**base, "type": "complete", "content": "", "sources": sources,
                              "timestamp": ts()}) + "\n"
            return

        # Phase 2 流式：复用冻结的 stream_query，注入 route/intent/confidence
        try:
            async for frame in self.tq.stream_query(
                user_id=user_id, question=question, document_id=document_id,
            ):
                try:
                    obj = json.loads(frame)
                except Exception:  # noqa: BLE001
                    yield frame
                    continue
                obj.update(base)
                yield json.dumps(obj) + "\n"
        except ValueError as e:
            if "没有可查询的表格文档" in str(e):
                yield json.dumps({**base, "type": "error",
                                  "error_type": ErrorType.NO_DOCUMENT.value,
                                  "message": str(e), "timestamp": ts()}) + "\n"
            else:
                yield json.dumps({**base, "type": "error",
                                  "error_type": ErrorType.INTERNAL_ERROR.value,
                                  "message": str(e), "timestamp": ts()}) + "\n"
        except Exception as e:  # noqa: BLE001
            logger.error(f"Phase2 TableQA 流式失败：{e}")
            yield json.dumps({**base, "type": "error",
                              "error_type": ErrorType.INTERNAL_ERROR.value,
                              "message": str(e), "timestamp": ts()}) + "\n"

    # =================== 3) Phase 1 执行 ===================
    async def _run_phase1(self, dec, user_id, question, document_id, history, base) -> Dict[str, Any]:
        try:
            text = ""
            if history:
                async for ch in self.rag.query_with_history(
                    query=question, history=history, user_id=user_id, document_id=document_id,
                ):
                    text += ch
            else:
                async for ch in self.rag.rag_query(
                    query=question, user_id=user_id, document_id=document_id,
                ):
                    text += ch
            sources = self._phase1_sources(document_id) if document_id else []
            return {
                **base, "execute": True,
                "chain": "Phase1-Retrieval",
                "answer": text,
                "sources": sources,
            }
        except Exception as e:  # noqa: BLE001
            logger.error(f"Phase1 TableQA 执行失败：{e}")
            return {
                **base, "execute": True,
                "chain": "Phase1-Retrieval",
                "error_type": ErrorType.CHROMA_RETRIEVAL_FAILED.value,
                "answer": f"检索失败：{e}",
                "sources": [],
            }

    # =================== 4) Phase 2 执行 ===================
    async def _run_phase2(self, dec, user_id, question, document_id, base) -> Dict[str, Any]:
        try:
            res = await self.tq.run_query(
                user_id=user_id, question=question, document_id=document_id,
            )
        except ValueError as e:
            if "没有可查询的表格文档" in str(e):
                return {**base, "execute": True, "chain": "Phase2-DuckDB",
                        "error_type": ErrorType.NO_DOCUMENT.value,
                        "answer": str(e), "sources": []}
            return {**base, "execute": True, "chain": "Phase2-DuckDB",
                    "error_type": ErrorType.INTERNAL_ERROR.value,
                    "answer": str(e), "sources": []}
        except Exception as e:  # noqa: BLE001
            logger.error(f"Phase2 TableQA 执行失败：{e}")
            return {**base, "execute": True, "chain": "Phase2-DuckDB",
                    "error_type": ErrorType.DUCKDB_EXECUTION_FAILED.value,
                    "answer": f"精确查询失败：{e}", "sources": []}

        return {
            **base, "execute": True,
            "chain": "Phase2-DuckDB",
            "answer": res.get("explanation"),
            "sql": res.get("sql"),
            "match_mode": res.get("match_mode"),
            "columns": res.get("columns"),
            "rows": res.get("rows"),
            "explanation": res.get("explanation"),
            "sources": self._normalize_sources(res.get("sources"), res.get("match_mode")),
        }

    # =================== 5) 统一 Source ===================
    @staticmethod
    def _phase1_sources(document_id: Optional[str]) -> List[Dict[str, Any]]:
        """Phase 1 来源（best-effort，从 Representation 读取，不修改 Phase 1）。"""
        if not document_id:
            return []
        try:
            rep = load_representation(document_id)
            if not rep:
                return []
            sheets = rep.get("workbook", {}).get("sheets", [])
            if not sheets:
                return []
            s = sheets[0]
            t = (s.get("tables") or [{}])[0]
            return [{
                "document_id": document_id,
                "filename": rep.get("filename") or rep.get("original_filename"),
                "sheet_name": s.get("sheet_name"),
                "table_id": t.get("table_id"),
                "range": None,
                "row_index": None,
                "match_mode": "semantic",
            }]
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _normalize_sources(sources: Optional[List[Dict[str, Any]]], match_mode: Optional[str]) -> List[Dict[str, Any]]:
        """Phase 2 sources 对齐到统一 Source Schema（补 table_id / match_mode）。"""
        out = []
        for s in (sources or []):
            out.append({
                "document_id": s.get("document_id"),
                "filename": s.get("filename"),
                "sheet_name": s.get("sheet_name"),
                "table_id": s.get("table_id"),
                "range": s.get("range"),
                "row_index": s.get("row_index"),
                "match_mode": match_mode or s.get("match_mode"),
            })
        return out
