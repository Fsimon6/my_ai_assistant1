<template>
  <div class="knowledge-base">
    <!-- 顶部标题 -->
    <div class="page-header">
      <h1>知识库管理</h1>
      <p>上传和管理您的文档，构建智能知识库</p>
    </div>

    <div class="content-container">
      <!-- 左侧：上传区域 -->
      <div class="upload-section">
        <el-card class="upload-card">
          <template #header>
            <div class="card-header">
              <span class="header-title">上传文档</span>
            </div>
          </template>

          <!-- 上传组件 -->
          <file-uploader @file-uploaded="handleFileUploaded" />

          <!-- 上传历史 -->
          <div class="upload-history" v-if="uploadHistory.length > 0">
            <h3>最近上传</h3>
            <el-timeline>
              <el-timeline-item
                v-for="item in uploadHistory.slice(0, 5)"
                :key="item"
                :timestamp="formatTime(item.timestamp)"
                placement="top"
              >
                <el-card>
                  <div class="history-item">
                    <div class="file-info">
                      <el-icon><Document /></el-icon>
                      <span class="filename">{{ item.filename }}</span>
                    </div>
                    <div class="file-stats">
                      <el-tag size="small" :type="item.status === 'success' ? 'success' : 'danger'">
                        {{ item.status === 'success' ? '成功' : '失败' }}
                      </el-tag>
                      <span class="chunks-count">{{ item.chunks }} chunk</span>
                    </div>
                  </div>
                </el-card>
              </el-timeline-item>
            </el-timeline>
          </div>
        </el-card>
      </div>

      <!-- 右侧：文档列表 -->
      <div class="document-section">
        <el-card class="document-card">
          <template #header>
            <div class="card-header">
              <span class="header-title">文档列表</span>
              <div class="header-actions">
                <el-button type="primary" size="small" @click="refreshDocuments">
                  <el-icon><Refresh /></el-icon>
                  刷新
                </el-button>
              </div>
            </div>
          </template>

          <!-- 操作栏 -->
          <div class="search-bar">
            <el-input
              v-model="searchQuery"
              placeholder="搜索文档..."
              clearable
              @clear="clearSearch"
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>
          </div>

          <!-- 文档列表 -->
          <div class="documents-list">
            <el-table
              :data="filteredDocuments"
              style="width: 100%"
              empty-text="暂无文档，请先上传"
            >
              <el-table-column
                prop="filename"
                label="文件名"
                width="250"
              >
                <template #default="scope">
                  <div class="filename-cell">
                    <el-icon class="file-icon">
                      <component :is="getFileIcon(scope.row.type)" />
                    </el-icon>
                    <span class="filename-text">{{ scope.row.filename }}</span>
                  </div>
                </template>
              </el-table-column>

              <el-table-column
                prop="type"
                label="类型"
                width="100"
              >
                <template #default="scope">
                  <el-tag size="small">{{ scope.row.type.toUpperCase() }}</el-tag>
                </template>
              </el-table-column>

              <el-table-column
                prop="size"
                label="大小"
                width="120"
              >
                <template #default="scope">
                  {{ formatFileSize(scope.row.size) }}
                </template>
              </el-table-column>

              <el-table-column
                prop="chunks"
                label="分数段"
                width="100" />

              <el-table-column
                prop="uploadTime"
                label="上传时间"
                width="180" />

              <el-table-column
                label="操作"
                width="200"
                fixed="right">
                <template #default="scope">
                  <div class="action-buttons">
                    <el-button
                      type="primary"
                      size="small"
                      @click="queryDocument(scope.row)"
                    >
                      查询
                    </el-button>
                    <el-button
                      type="info"
                      size="small"
                      @click="previewDocument(scope.row)"
                    >
                      预览
                    </el-button>
                    <el-button
                      type="danger"
                      size="small"
                      @click="deleteDocument(scope.row)"
                    >
                      删除
                    </el-button>
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <!-- 统计信息 -->
          <div class="stats-info">
            <el-row :gutter="20">
              <el-col :span="6">
                <el-statistic title="文档总数" :value="documents.length" />
              </el-col>
              <el-col :span="6">
                <el-statistic title="总分段数" :value="totalChunks" />
              </el-col>
              <el-col :span="6">
                <div style="display:flex;flex-direction:column;gap:4px;">
                  <span style="font-size:13px;color:#909399;">知识库大小</span>
                  <span style="font-size:20px;font-weight:600;color:#303133;">{{ totalSize }}</span>
                </div>
              </el-col>
              <el-col :span="6">
                <div style="display:flex;flex-direction:column;gap:4px;">
                  <span style="font-size:13px;color:#909399;">最后更新</span>
                  <span style="font-size:20px;font-weight:600;color:#303133;">{{ lastUpdate }}</span>
                </div>
              </el-col>
            </el-row>
          </div>
        </el-card>

        <!-- 快捷查询 -->
        <el-card class="quick-query-card">
          <template #header>
            <div class="card-header">
              <span class="header-title">快速查询</span>
            </div>
          </template>

          <div class="quick-query-form">
            <el-input
              v-model="quickQuery"
              type="textarea"
              :rows="2"
              placeholder="输入您的问题，快速查询知识库..."
            />
            <div class="query-actions">
              <el-button type="primary" @click="handleQuickQuery">
                查询
              </el-button>
              <el-button
                @click="clearQuickQuery">
                清空
              </el-button>
            </div>

            <!-- 查询结果 -->
            <div v-if="queryResult" class="query-result">
              <h4>查询结果：</h4>
              <div class="result-content">
                {{ queryResult }}
              </div>
            </div>
          </div>
        </el-card>
      </div>
    </div>

    <!-- 文档预览对话框 -->
    <el-dialog
      v-model="previewVisible"
      :title="`预览：${previewDoc?.filename || ''}`"
      width="60%"
      :close-on-click-modal="true"
      :close-on-press-escape="true"
      :before-close="handlePreviewClose"
      destroy-on-close>
      <div v-loading="previewLoading" class="preview-body">
        <div v-if="!previewLoading && previewChunks.length === 0" class="empty-tip">
          该文档暂无可预览的分块内容
        </div>
        <div v-if="tableStructure" class="table-structure">
          <h4>表格结构</h4>
          <div v-for="sh in tableStructure.workbook.sheets" :key="sh.sheet_name" class="ts-sheet">
            <p class="ts-sheet-name"><b>工作表：</b>{{ sh.sheet_name }}</p>
            <div v-for="t in sh.tables" :key="t.table_id" class="ts-table-block">
              <p class="ts-table">
                范围 <code>{{ t.range }}</code> ｜ 列数 {{ t.col_count }} ｜ 数据行 {{ t.row_count }} ｜ 表头层数 {{ t.n_header_rows }}
              </p>
              <p class="ts-cols">字段（列字母=字段名）：{{ t.columns.map(c => c.col_letter + '=' + c.technical_name).join('，') }}</p>
            </div>
          </div>
        </div>
        <div v-for="chunk in previewChunks" :key="chunk.index" class="chunk-block">
          <div class="chunk-index">第 {{ chunk.index + 1 }} 段</div>
          <pre class="chunk-content">{{ chunk.content }}</pre>
        </div>
      </div>
    </el-dialog>

    <!-- 按文档查询对话框 -->
    <el-dialog
      v-model="queryVisible"
      :title="`在文档内查询：${queryTargetDoc?.filename || ''}`"
      width="60%">
      <el-input
        v-model="docQueryInput"
        type="textarea"
        :rows="3"
        placeholder="输入要在这篇文档中查询的问题（Ctrl+Enter 发送）"
        @keyup.ctrl.enter="submitDocQuery"
      />
      <div class="query-actions" style="margin-top: 12px;">
        <el-button type="primary" :loading="docQueryLoading" @click="submitDocQuery">
          查询
        </el-button>
      </div>
      <div v-if="docQueryResult" class="query-result" style="margin-top: 16px;">
        <h4>查询结果：</h4>
        <div class="result-content">{{ docQueryResult }}</div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted} from "vue"
import { ElMessage, ElMessageBox } from "element-plus"
import {
  Document,
  Refresh,
  Search,
  Folder,
  Ticket,
  Picture,
  VideoCamera,
  Files
} from "@element-plus/icons-vue"
import FileUploader from "@/components/chat/FileUploader.vue"
import { ragApi } from '@/services/api'

// 数据
const documents = ref<any[]>([])
const uploadHistory = ref<any[]>([])
const searchQuery = ref('')
const quickQuery = ref('')
const queryResult = ref('')

// 文档预览（拉取该文档全部分块原文）
const previewVisible = ref(false)
const previewDoc = ref<any>(null)
const previewChunks = ref<any[]>([])
const previewLoading = ref(false)
// 表格结构（对应后端 get_table_structure 返回：{ structure: { workbook: { sheets: [...] } } }）
interface TableColumn {
  col_letter: string
  technical_name: string
}
interface TableInfo {
  table_id: string
  range: string
  col_count: number
  row_count: number
  n_header_rows: number
  columns: TableColumn[]
}
interface SheetInfo {
  sheet_name: string
  tables: TableInfo[]
}
interface WorkbookInfo {
  sheets: SheetInfo[]
}
interface TableStructure {
  workbook: WorkbookInfo
}
const tableStructure = ref<TableStructure | null>(null)

// 按文档查询（限定只在该文档内检索）
const queryVisible = ref(false)
const queryTargetDoc = ref<any>(null)
const docQueryInput = ref('')
const docQueryResult = ref('')
const docQueryLoading = ref(false)

// 计算属性
const filteredDocuments = computed(() => {
  if (!searchQuery.value) return documents.value
  return documents.value.filter(doc => doc.filename.toLowerCase().includes(searchQuery.value.toLowerCase()))
})

const totalChunks = computed(() => {
  return documents.value.reduce((sum, doc) => sum + (doc.chunks || 0), 0)
})

const totalSize = computed(() => {
  const total = documents.value.reduce((sum, doc) => sum + (doc.size || 0), 0)
  return formatFileSize(total)
})

const lastUpdate = computed(() => {
  if (documents.value.length === 0) return '无'
  const latest = Math.max(...documents.value.map(d => new Date(d.uploadTime).getTime()))
  return new Date(latest).toLocaleDateString()
})

// 文件图标映射
const fileIcons = {
  'pdf': Ticket,
  'txt': Files,
  'docx': Document,
  'doc': Document,
  'md': Files,
  'xlsx': Document,
  'xls': Document,
  'csv': Files,
  'tsv': Files,
  'default': Folder
}

// 生命周期
onMounted(() => {
  loadDocuments()
  loadCollectionInfo()
})

// 方法
const getFileIcon = (fileType: string) => {
  if (fileType.includes('pdf')) return fileIcons.pdf
  if (fileType.includes('text')) return fileIcons.txt
  if (fileType.includes('document')) return fileIcons.docx
  if (fileType.includes('markdown')) return fileIcons.md
  if (['xlsx', 'xls', 'csv', 'tsv'].includes(fileType.toLowerCase())) return fileIcons.xlsx
  return fileIcons.default
}

// 表格类文档类型（用于预览时拉取结构）
const TABLE_TYPES = ['xlsx', 'xls', 'csv', 'tsv']
const isTableType = (t: string) => TABLE_TYPES.includes((t || '').toLowerCase())

const formatFileSize = (bytes: number) => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

const formatTime = (timestamp: string) => {
  return new Date(timestamp).toLocaleDateString()
}

const loadDocuments = async () => {
  try {
    // 真实调用后端：按当前用户隔离的文档列表（不再使用 mock）
    const res = await ragApi.getDocuments()
    documents.value = (res.documents || []).map((d: any) => ({
      id: d.document_id,
      document_id: d.document_id,
      filename: d.filename,
      type: d.type,
      size: d.size || 0,
      chunks: d.chunks || 0,
      uploadTime: d.created_at ? new Date(d.created_at).toLocaleString() : ''
    }))
  } catch (error) {
    console.error('加载文档失败:', error)
    ElMessage.error('加载文档失败')
  }
}

const loadCollectionInfo = async () => {
  try {
    const info = await ragApi.getCollectionInfo()
    console.log('集合信息：', info)
  } catch (error) {
    console.warn('获取集合信息失败：', error)
  }
}

const handleFileUploaded = async (fileData: any) => {
  try {
    // 调用上传API
    const result = await ragApi.uploadDocument(fileData.file, {
      description: fileData.description
    })

    const docId = (result as any).document_id

    // 重新拉取真实文档列表（含后端聚合的 created_at/chunks），保证与删除闭环一致
    await loadDocuments()

    // 添加到上传历史（含 document_id）
    uploadHistory.value.unshift({
      id: docId || Date.now(),
      document_id: docId,
      filename: fileData.name,
      status: 'success',
      chunks: (result as any).total_chunks || 0,
      timestamp: new Date().toISOString()
    })

    ElMessage.success(`文件"${fileData.name}" 上传成功`)
  } catch (error) {
    console.error('文件上传失败：', error)

    uploadHistory.value.unshift({
      id: Date.now(),
      filename: fileData.name,
      status: 'error',
      chunks: 0,
      timestamp: new Date().toISOString()
    })

    ElMessage.error('文件上传失败')
  }
}

const handleQuickQuery = async () => {
  if (!quickQuery.value.trim()) {
    ElMessage.warning('请输入查询内容')
    return
  }

  try {
    const response = await ragApi.queryDocument(quickQuery.value, false)
    queryResult.value = response.response || '无相关结果'
    ElMessage.success('查询完成')
  } catch (error) {
    console.error('查询失败：', error)
    queryResult.value = '查询失败，请重试'
    ElMessage.error('查询失败')
  }
}

// 预览文档：拉取该文档全部分块原文（服务端按 user_id 隔离）
const previewDocument = async (doc: any) => {
  previewDoc.value = doc
  previewChunks.value = []
  tableStructure.value = null
  previewVisible.value = true
  previewLoading.value = true
  try {
    const res = await ragApi.getDocument(doc.document_id)
    previewChunks.value = res.chunks || []
    // 表格类文档额外拉取 Unified Table Representation
    if (isTableType(doc.type)) {
      try {
        const ts = await ragApi.getTableStructure(doc.document_id)
        tableStructure.value = ts.structure || null
      } catch {
        tableStructure.value = null
      }
    }
  } catch (error: any) {
    const detail = error?.response?.data?.detail || error?.message || '获取失败'
    ElMessage.error('预览失败：' + detail)
  } finally {
    previewLoading.value = false
  }
}

// 关闭预览对话框（before-close 兜底，确保 X / 点击蒙层 / ESC 都能关闭）
const handlePreviewClose = (done: () => void) => {
  previewVisible.value = false
  done()
}

// 按文档查询：限定只在该文档内检索（打开对话框）
const queryDocument = (doc: any) => {
  queryTargetDoc.value = doc
  docQueryInput.value = ''
  docQueryResult.value = ''
  queryVisible.value = true
}

// 提交按文档查询
const submitDocQuery = async () => {
  if (!docQueryInput.value.trim()) {
    ElMessage.warning('请输入查询内容')
    return
  }
  docQueryLoading.value = true
  try {
    const res = await ragApi.queryDocument(
      docQueryInput.value,
      false,
      3,
      queryTargetDoc.value.document_id
    )
    docQueryResult.value = res.response || '无相关结果'
  } catch (error: any) {
    const detail = error?.response?.data?.detail || error?.message || '查询失败'
    ElMessage.error('查询失败：' + detail)
    docQueryResult.value = ''
  } finally {
    docQueryLoading.value = false
  }
}

const deleteDocument = async (doc: any) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除文档 "${doc.filename}" 吗?此操作不可撤销。`,
      '确认删除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
  } catch {
    // 用户取消
    return
  }

  try {
    // 真实调用后端删除 API（自动携带 JWT）；无 document_id 的仅为本地占位数据
    if (doc.document_id) {
      await ragApi.deleteDocument([doc.document_id])
    }
    documents.value = documents.value.filter(d => d.id !== doc.id)
    uploadHistory.value = uploadHistory.value.filter(d => d.document_id !== doc.document_id)
    ElMessage.success('文档删除成功')
  } catch (error: any) {
    console.error('删除文档失败：', error)
    const detail = error?.response?.data?.detail || error?.message || '请重试'
    ElMessage.error('删除失败：' + detail)
  }
}

const refreshDocuments = () => {
  loadDocuments()
  ElMessage.info('文档列表已刷新')
}

const clearSearch = () => {
  searchQuery.value = ''
}

const clearQuickQuery = () => {
  quickQuery.value = ''
  queryResult.value = ''
}
</script>

<style scoped lang="scss">
.knowledge-base {
  padding: 20px;
}

.page-header {
  margin-bottom: 30px;

  h1 {
    font-size: 28px;
    color: var(--text-primary);
    margin-bottom: 8px;
  }

  p {
    color: var(--text-secondary);
    font-size: 16px;
  }
}

.content-container {
  display: grid;
  grid-template-columns: 1fr 2fr;
  gap: 20px;

  @media (max-width: 1200px) {
    grid-template-columns: 1fr;
  }
}

.upload-card,
.document-card,
.quick-query-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;

  .header-title {
    font-size: 18px;
    font-weight: 600;
  }
}

.upload-history {
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid var(--border-light);

  h3 {
    margin-bottom: 15px;
    font-size: 16px;
    color: var(--text-primary);
  }

  .history-item {
    display: flex;
    justify-content: space-between;
    align-items: center;

    .file-info {
      display: flex;
      align-items: center;
      gap: 8px;

      .filename {
        font-weight: 500;
      }
    }

    .file-stats {
      display: flex;
      align-items: center;
      gap: 12px
    }
  }
}

.search-bar {
  margin-bottom: 20px;
}

.documents-list {
  min-height: 300px;

  .filename-cell {
    display: flex;
    align-items: center;
    gap: 8px;

    .file-icon {
      color: var(--primary-color);
      font-size: 18px;
    }

    .filename-text {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .action-buttons {
    display: flex;
    gap: 8px;
  }
}

.stats-info {
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid var(--border-light);
}

.quick-query-form {
  .quick-actions {
    display: flex;
    gap: 10px;
    margin-top: 10px;
    margin-bottom: 20px;
  }

  .query-result {
    margin-top: 20px;
    padding: 15px;
    background: var(--bg-base);
    border-radius: 6px;
    border: 1px solid var(--border-light);

    h4 {
      margin-bottom: 10px;
      color: var(--text-primary);
    }

    .result-content {
      line-height: 1.6;
      color: var(--text-regular);
    }
  }
}


.empty-tip {
  padding: 20px;
  text-align: center;
  color: #606266;
}

.preview-body {
  max-height: 60vh;
  overflow-y: auto;
  padding-right: 8px;
  background: #ffffff;
}

.table-structure {
  margin-bottom: 16px;
  padding: 12px 14px;
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;

  h4 {
    margin-bottom: 10px;
    color: #303133;
    font-size: 15px;
  }

  .ts-sheet {
    margin-bottom: 10px;
  }

  .ts-sheet-name {
    color: #303133;
    font-size: 13px;
    margin: 4px 0;
  }

  .ts-table-block {
    margin: 6px 0;
    padding: 8px 10px;
    background: #ffffff;
    border-radius: 4px;
  }

  .ts-table {
    font-size: 13px;
    color: #606266;
    margin: 4px 0;
  }

  .ts-cols {
    font-size: 12px;
    color: #606266;
    line-height: 1.6;
    word-break: break-word;
    margin: 4px 0 0;
  }

  code {
    background: rgba(64, 158, 255, 0.1);
    padding: 1px 5px;
    border-radius: 4px;
  }
}

.chunk-block {
  margin-bottom: 16px;
  padding: 12px 14px;
  background: #ffffff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;

  .chunk-index {
    font-size: 13px;
    font-weight: 600;
    color: #409eff;
    margin-bottom: 8px;
  }

  .chunk-content {
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.7;
    font-family: inherit;
    color: #303133;
    font-size: 14px;
  }
}</style>
