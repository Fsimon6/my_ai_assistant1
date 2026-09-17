import type { AxiosResponse, AxiosInstance } from 'axios'
import axios from 'axios'
import type { UploadFile } from "element-plus"

// 创建axios实例
// 统一使用 VITE_API_BASE_URL：开发=http://localhost:8000，生产=空（同源 /api，由 nginx 反代）。
// 避免硬编码 localhost:8000 进入生产 bundle。
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
// 生产构建中 DEV 为 false，空 baseURL 时回退为同源相对 /api/v1；仅开发回退到 localhost:8000
const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL ? `${API_BASE_URL}/api/v1` : (import.meta.env.DEV ? 'http://localhost:8000/api/v1' : '/api/v1'),
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器：附加鉴权 token（与 utils/requests.ts 保持一致）
api.interceptors.request.use(
  (config: any) => {
    const token = localStorage.getItem('access_token')
    if (token) {
      config.headers = config.headers || {}
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  (response: AxiosResponse) => {
    return response.data
  },
  (error) => {
    console.error('API请求错误：', error)
    return Promise.reject(error)
  }
)

// RAG 接口返回结构（响应拦截器已解包为 response.data）
export interface RagUploadResult {
  success?: boolean | string
  message?: string
  filename?: string
  total_chunks?: number
  [key: string]: any
}

export interface RagQueryResult {
  success?: boolean | string
  response?: string
  query?: string
  timestamp?: string
  sources?: any[]
  [key: string]: any
}

export interface RagCollectionInfoResult {
  success?: boolean | string
  collection_info?: any
  timestamp?: string
  [key: string]: any
}

// RAG API
export const ragApi = {
  // 上传文档
  uploadDocument: async (file: File, metadata?: any): Promise<RagUploadResult> => {
    const formData = new FormData()
    formData.append('file', file)
    if (metadata) {
      formData.append('metadata', JSON.stringify(metadata))
    }
    const res = await api.post('/rag/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    return res as unknown as RagUploadResult
  },

  // 查询文档（可选 documentId 限定只在该文档内检索）
  queryDocument: async (query: string, stream: boolean = false, contextCount: number = 3, documentId?: string): Promise<RagQueryResult> => {
    const res = await api.post('/rag/query', { query, stream, context_count: contextCount, document_id: documentId })
    return res as unknown as RagQueryResult
  },

  // 获取单个文档全部分块内容（按 document_id，供预览；服务端 user_id 隔离）
  getDocument: async (documentId: string): Promise<{ success?: boolean; document_id?: string; chunks?: any[]; total?: number }> => {
    const res = await api.get(`/rag/documents/${documentId}`)
    return res as unknown as { success?: boolean; document_id?: string; chunks?: any[]; total?: number }
  },

  // 获取表格文档的 Unified Table Representation（按 document_id，供预览 Workbook/Sheet/Columns/Rows）
  getTableStructure: async (documentId: string): Promise<any> => {
    const res = await api.get(`/rag/documents/${documentId}/table-structure`)
    return res as unknown as any
  },

  // 带历史查询
  queryWithHistory: async (query: string, history: any[], stream: boolean = false): Promise<RagQueryResult> => {
    const res = await api.post('/rag/query-with-history', { query, history, stream })
    return res as unknown as RagQueryResult
  },

  // 获取集合信息
  getCollectionInfo: async (): Promise<RagCollectionInfoResult> => {
    const res = await api.get('/rag/collection-info')
    return res as unknown as RagCollectionInfoResult
  },

  // 删除文档（按 document_id 精确删除，自动携带 JWT）
  deleteDocument: async (documentIds: string[]): Promise<any> => {
    const res = await api.delete('/rag/documents', {
      data: { document_ids: documentIds }
    })
    return res as unknown as any
  },

  // 获取当前用户的真实文档列表（按 user_id 隔离，聚合自向量库 metadata）
  getDocuments: async (): Promise<{ success?: boolean; documents?: any[]; total?: number }> => {
    const res = await api.get('/rag/documents')
    return res as unknown as { success?: boolean; documents?: any[]; total?: number }
  }
}

// Characters API
export const charactersApi = {
  // 与角色对话
  speakToCharacter: async (characterId: string, message: string, stream: boolean = false) => {
    return api.post(`/characters/${characterId}/speak`, {
      message,
      stream
    })
  },

  // 流式对话
  speakToCharacterStream: async (characterId: string, message: string) => {
    return api.post(`/characters/${characterId}/speak/stream`, {
      message,
      stream: true
    })
  }
}

// 系统API
export const systemApi = {
  // 健康检查
  healthCheck: async () => {
    return api.get('/health')
  },

  // 系统信息
  getSystemInfo: async () => {
    return api.get('/')
  }
}

export default api
