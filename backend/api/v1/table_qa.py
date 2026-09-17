# -*- coding: utf-8 -*-
"""
Phase 3 统一 Table QA 入口（POST /api/v1/table-qa/query）。

只做统一调度：调用 TableQAService（内部路由到 Phase 1 或 Phase 2）。
不耦合 Chroma / DuckDB / NL2SQL / 认证；复用现有 get_current_active_user。
旧端点 /api/v1/rag/query 与 /api/v1/table-query 保持不变，可独立回归。
"""
import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.utils.auth import get_current_active_user
from backend.models.user import User
from backend.services.table_qa_service import TableQAService
from backend.services.conversation_service import conversation_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/table-qa", tags=["TableQA"])

# 复用单例（DuckDB 引擎为进程内单例；RagService 亦为单例）
_service = TableQAService()


class TableQARequest(BaseModel):
    """统一查询请求体。"""
    query: str
    document_id: Optional[str] = None   # 可选：限定单文档（Phase 1/2 均按用户隔离）
    stream: bool = False
    history: List[dict] = []            # 多轮上下文（仅 Phase 1 使用；Phase 2 无状态）
    character_id: Optional[str] = None  # 可选：传入则把问答存入该角色的对话历史（复用现有 Conversation/Message，不新建独立历史）


@router.post("/query")
async def table_qa_query(
    req: TableQARequest,
    current_user: User = Depends(get_current_active_user),
):
    """Phase 3 统一 Table QA（需登录；按当前用户隔离）。

    内部由 TableQAService 判定 SCHEMA / SEMANTIC_RETRIEVAL / STRUCTURED_ACCESS /
    PRECISE_QUERY / AMBIGUOUS / UNSUPPORTED，并路由到 Phase 1 或 Phase 2。
    """
    try:
        # P3: 若传入 character_id，把问答存入该角色的现有对话历史（复用 Conversation/Message，不新建独立历史）
        conv_id = None
        if req.character_id:
            try:
                conv_id = conversation_service.get_or_create_conversation_id(
                    current_user.id, int(req.character_id))
                conversation_service.append_message(conv_id, 'user', req.query)
            except Exception as e:
                logger.warning(f'保存 Table QA 用户消息失败：{e}')

        if req.stream:
            async def generate():
                full_response = ''
                async for frame in _service.stream(
                    user_id=current_user.id,
                    question=req.query,
                    document_id=req.document_id,
                    history=req.history,
                ):
                    try:
                        data = json.loads(frame)
                        if data.get('type') == 'chunk':
                            full_response += data.get('content', '')
                    except Exception:
                        pass
                    yield frame
                if conv_id is not None:
                    try:
                        conversation_service.append_message(conv_id, 'assistant', full_response)
                    except Exception as e:
                        logger.warning(f'保存 Table QA 助手消息失败：{e}')
            return StreamingResponse(
                generate(),
                media_type="application/x-ndjson",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        result = await _service.run(
            user_id=current_user.id,
            question=req.query,
            document_id=req.document_id,
            history=req.history,
        )
        if conv_id is not None:
            try:
                conversation_service.append_message(conv_id, 'assistant', result.get('answer', ''))
            except Exception as e:
                logger.warning(f'保存 Table QA 助手消息失败：{e}')
        return {
            "success": True,
            **result,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"统一 Table QA 失败：{e}")
        raise
