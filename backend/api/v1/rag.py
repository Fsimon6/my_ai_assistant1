from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import json
import os
import logging
from datetime import datetime

from backend.services.rag_service import get_rag_service
from backend.services.document_service import save_uploaded_file
from backend.utils.auth import get_current_active_user
from backend.models.user import User


router = APIRouter(prefix='/api/v1/rag', tags=['RAG'])
logger = logging.getLogger(__name__)


class QueryRequest(BaseModel):
    """查询请求体（与前端 ragApi.queryDocument POST 体一致）"""
    query: str
    stream: bool = False
    context_count: int = 3
    character_id: Optional[str] = None  # 可选：传入则使用角色专属 model+api_key（与 /speak/stream 一致）
    document_id: Optional[str] = None  # 可选：限定只在该文档内检索（按文档查询/预览）


class QueryWithHistoryRequest(BaseModel):
    """带历史查询请求体（与前端 ragApi.queryWithHistory POST 体一致）"""
    query: str
    history: List[dict] = []
    stream: bool = False
    context_count: int = 3
    character_id: Optional[str] = None  # 可选：传入则使用角色专属 model+api_key
    document_id: Optional[str] = None  # 可选：限定只在该文档内检索


@router.post('/upload')
async def upload_document(
    file: UploadFile = File(...),
    metadata: Optional[str] = None,
    current_user: User = Depends(get_current_active_user)
):
    """上传并处理文档（需登录；文档归属当前用户）"""
    try:
        rag_service = get_rag_service()

        # 验证文件类型（文本 + 表格）
        allowed_types = ['.pdf', '.txt', '.docx', '.md', '.xlsx', '.xls', '.csv', '.tsv']
        file_ext = '.' + file.filename.split('.')[-1] if '.' in file.filename else ''

        if file_ext.lower() not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f'不支持的文件类型。支持的类型：{",".join(allowed_types)}'
            )

        # 保存文件
        file_path = await save_uploaded_file(file)

        # 原始文件名与大小（用于文档列表展示，服务端强制写入 metadata，避免只存临时名）
        original_filename = file.filename
        file_size = 0
        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            file_size = 0

        # 解析元数据
        parsed_metadata = {}
        if metadata:
            try:
                parsed_metadata = json.loads(metadata)
            except:
                parsed_metadata = {'custom_metadata': metadata}

        # 处理文档（归属当前用户，用于向量隔离）
        result = await rag_service.process_and_store_document(
            file_path,
            parsed_metadata,
            user_id=current_user.id,
            original_filename=original_filename,
            file_size=file_size
        )

        if result['success']:
            return {
                'success': True,
                'message': '文档处理成功',
                'document_id': result['document_id'],
                'chunk_ids': result.get('chunk_ids'),
                'filename': result['filename'],
                'total_chunks': result['total_chunks'],
            }
        else:
            raise HTTPException(
                status_code=500,
                detail=f'文档处理失败：{result.get("error")}'
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'上传文档失败：{e}')
        raise HTTPException(
            status_code=500,
            detail=f'上传文档失败：{str(e)}'
        )


@router.post('/query')
async def query_document(
    req: QueryRequest,
    current_user: User = Depends(get_current_active_user)
):
    """查询文档（需登录；按当前用户隔离向量检索与缓存）"""
    try:
        rag_service = get_rag_service()

        # 解析角色级模型（可选）：传入 character_id 时按归属用户取出 model+api_key 覆盖全局默认
        char_model, char_api_key = None, None
        if req.character_id:
            from backend.services.character_service import character_service
            character = character_service.get_character(req.character_id, current_user.id)
            if character is None:
                raise HTTPException(status_code=404, detail='角色不存在')
            char_model = character.model
            char_api_key = character.api_key

        if req.stream:
            async def generate():
                full_response = ''
                async for chunk in rag_service.rag_query(
                    query=req.query,
                    context_count=req.context_count,
                    stream=True,
                    user_id=current_user.id,
                    model=char_model,
                    api_key=char_api_key,
                    document_id=req.document_id
                ):
                    full_response += chunk
                    yield json.dumps({
                        'type': 'chunk',
                        'content': chunk,
                        'timestamp': datetime.now().isoformat()
                    }) + '\n'

                # 与普通 Chat 流式协议保持一致：末尾补 complete 帧
                yield json.dumps({
                    'type': 'complete',
                    'content': full_response,
                    'timestamp': datetime.now().isoformat()
                }) + '\n'

            return StreamingResponse(
                generate(),
                media_type='application/x-ndjson',
                headers={
                    'Cache-Control': 'no-cache',
                    'X-Accel-Buffering': 'no'
                }
            )
        else:
            response_text = ''
            async for chunk in rag_service.rag_query(
                query=req.query,
                context_count=req.context_count,
                stream=False,
                user_id=current_user.id,
                model=char_model,
                api_key=char_api_key,
                document_id=req.document_id
            ):
                response_text += chunk

            return {
                'success': True,
                'response': response_text,
                'query': req.query,
                'timestamp': datetime.now().isoformat()
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'查询失败：{e}')
        raise HTTPException(
            status_code=500,
            detail=f'查询失败：{str(e)}'
        )


@router.post('/query-with-history')
async def query_with_history(
        req: QueryWithHistoryRequest,
        current_user: User = Depends(get_current_active_user)
):
    """带历史记录的查询（需登录；按当前用户隔离向量检索）"""
    try:
        rag_service = get_rag_service()

        # 解析角色级模型（可选）：传入 character_id 时按归属用户取出 model+api_key 覆盖全局默认
        char_model, char_api_key = None, None
        if req.character_id:
            from backend.services.character_service import character_service
            character = character_service.get_character(req.character_id, current_user.id)
            if character is None:
                raise HTTPException(status_code=404, detail='角色不存在')
            char_model = character.model
            char_api_key = character.api_key

        if req.stream:
            async def generate():
                full_response = ''
                async for chunk in rag_service.query_with_history(
                    query=req.query,
                    history=req.history,
                    context_count=req.context_count,
                    user_id=current_user.id,
                    model=char_model,
                    api_key=char_api_key,
                    document_id=req.document_id
                ):
                    full_response += chunk
                    yield json.dumps({
                        'type': 'chunk',
                        'content': chunk,
                        'timestamp': datetime.now().isoformat()
                    }) + '\n'

                # 与普通 Chat 流式协议保持一致：末尾补 complete 帧（携带完整内容）
                yield json.dumps({
                    'type': 'complete',
                    'content': full_response,
                    'timestamp': datetime.now().isoformat()
                }) + '\n'

            return StreamingResponse(
                generate(),
                media_type='application/x-ndjson',
                headers={
                    'Cache-Control': 'no-cache',
                    'X-Accel-Buffering': 'no'
                }
            )
        else:
            response_text = ''
            async for chunk in rag_service.query_with_history(
                query=req.query,
                history=req.history,
                context_count=req.context_count,
                user_id=current_user.id,
                model=char_model,
                api_key=char_api_key,
                document_id=req.document_id
            ):
                response_text += chunk

            return {
                'success': True,
                'response': response_text,
                'query': req.query,
                'timestamp': datetime.now().isoformat()
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'带历史查询失败：{e}')
        raise HTTPException(
            status_code=500,
            detail=f'查询失败：{str(e)}'
        )


@router.get('/collection-info')
async def get_collection_info(
    current_user: User = Depends(get_current_active_user)
):
    """获取向量数据库信息（需登录）"""
    try:
        from backend.services.vector_service import get_vector_store_manager
        vector_store = get_vector_store_manager()
        info = vector_store.get_collection_info()

        return {
            'success': True,
            'collection_info': info,
            'timestamp': datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f'获取集合信息失败：{e}')
        raise HTTPException(
            status_code=500,
            detail=f'获取集合信息失败：{str(e)}'
        )


class DeleteDocumentsRequest(BaseModel):
    """删除文档请求体（DELETE 携带 JSON 体）"""
    document_ids: List[str]


@router.delete('/documents')
async def delete_documents(
    req: DeleteDocumentsRequest,
    current_user: User = Depends(get_current_active_user)
):
    """删除文档（需登录；仅删除归属当前用户的文档）"""
    try:
        from backend.services.vector_service import get_vector_store_manager
        vector_store = get_vector_store_manager()
        deleted_count = await vector_store.delete_documents(req.document_ids, user_id=current_user.id)

        return {
            'success': True,
            'message': '文档删除成功' if deleted_count else '未删除任何归属于当前用户的文档',
            'delete_ids': req.document_ids,
            'deleted_count': deleted_count,
            'timestamp': datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f'删除文档失败：{e}')
        raise HTTPException(
            status_code=500,
            detail=f'删除文档失败“：{str(e)}'
        )


@router.get('/documents')
async def list_documents(
    current_user: User = Depends(get_current_active_user)
):
    """列出当前用户的文档（需登录；按 user_id 隔离，聚合自 Chroma 向量 metadata）

    返回字段（仅 UI 实际需要的）：document_id / filename / type / size / chunks / created_at
    """
    try:
        from backend.services.vector_service import get_vector_store_manager
        vector_store = get_vector_store_manager()
        docs = vector_store.list_user_documents(user_id=current_user.id)
        return {
            'success': True,
            'documents': docs,
            'total': len(docs),
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f'列出文档失败：{e}')
        raise HTTPException(
            status_code=500,
            detail=f'列出文档失败：{str(e)}'
        )


@router.get('/documents/{document_id}')
async def get_document(
    document_id: str,
    current_user: User = Depends(get_current_active_user)
):
    """获取单个文档的全部分块内容（需登录；仅返回归属当前用户的文档，供预览/查看原文）

    无需新增数据库表，直接基于 Chroma 向量（已含 document_id + user_id + chunk_index）。
    """
    try:
        from backend.services.vector_service import get_vector_store_manager
        vector_store = get_vector_store_manager()
        chunks = await vector_store.get_document_chunks(document_id, user_id=current_user.id)
        if not chunks:
            raise HTTPException(status_code=404, detail='文档不存在或无权访问')
        return {
            'success': True,
            'document_id': document_id,
            'chunks': chunks,
            'total': len(chunks),
            'timestamp': datetime.now().isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'获取文档内容失败：{e}')
        raise HTTPException(
            status_code=500,
            detail=f'获取文档内容失败：{str(e)}'
        )


@router.get('/documents/{document_id}/table-structure')
async def get_table_structure(
    document_id: str,
    current_user: User = Depends(get_current_active_user)
):
    """获取表格文档的 Unified Table Representation（需登录；按 user_id 隔离）。

    供前端预览 Workbook / Sheet / Columns / Rows；仅返回归属当前用户且已成功解析的表格文档。
    """
    try:
        from backend.services.vector_service import get_vector_store_manager
        vector_store = get_vector_store_manager()
        # 用户隔离：确认该 document_id 下存在归属当前用户的向量，否则 404
        owned = await vector_store.get_document_chunks(document_id, user_id=current_user.id)
        if not owned:
            raise HTTPException(status_code=404, detail='文档不存在或无权访问')
        rag_service = get_rag_service()
        rep = rag_service.get_table_structure(document_id)
        if not rep:
            raise HTTPException(status_code=404, detail='该文档不是表格文档或结构文件缺失')
        return {
            'success': True,
            'document_id': document_id,
            'structure': rep,
            'timestamp': datetime.now().isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'获取表格结构失败：{e}')
        raise HTTPException(
            status_code=500,
            detail=f'获取表格结构失败：{str(e)}'
        )

