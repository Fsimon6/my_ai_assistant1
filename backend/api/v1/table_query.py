# -*- coding: utf-8 -*-
"""
Phase 2 独立精确查询入口（POST /api/v1/table-query）。

仅作为第二阶段"精确查询与计算"的独立通道；第三阶段统一 Table QA 可调用本接口。
不耦合 Phase 1 Chroma / 不改动认证 / 不改动前端。
"""
import json
import logging
from datetime import datetime
from typing import Optional, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.utils.auth import get_current_active_user
from backend.services.table_query_service import TableQueryService

logger = logging.getLogger(__name__)
router = APIRouter()


class TableQueryRequest(BaseModel):
    query: str
    document_id: Optional[str] = None
    stream: bool = False


@router.post("/table-query")
async def table_query(
    req: TableQueryRequest,
    current_user=Depends(get_current_active_user),
):
    """Phase 2 精确查询（需登录；按 user_id 隔离）。"""
    # 每次请求使用独立的 TableQueryService（独立 DuckDB 连接），用后关闭。
    tq = TableQueryService()
    try:
        if req.stream:
            return StreamingResponse(
                _stream(req, current_user.id),
                media_type="application/x-ndjson",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        result = await tq.run_query(
            user_id=current_user.id, question=req.query, document_id=req.document_id
        )
        return {
            "success": True,
            "sql": result.get("sql"),
            "match_mode": result.get("match_mode"),
            "columns": result.get("columns"),
            "rows": result.get("rows"),
            "row_count": len(result.get("rows") or []),
            "explanation": result.get("explanation"),
            "sources": result.get("sources"),
            "timestamp": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"精确查询失败：{e}")
        raise HTTPException(status_code=500, detail=f"精确查询失败：{str(e)}")
    finally:
        tq.close()


async def _stream(req: TableQueryRequest, user_id: int) -> AsyncGenerator[str, None]:
    # 每次请求使用独立的 TableQueryService（独立 DuckDB 连接），用后关闭。
    tq = TableQueryService()
    try:
        async for frame in tq.stream_query(
            user_id=user_id, question=req.query, document_id=req.document_id
        ):
            yield frame
    finally:
        tq.close()
