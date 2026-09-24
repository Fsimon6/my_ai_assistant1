<template>
  <div class="table-qa">
    <div class="page-header">
      <h1>表格问答</h1>
      <p>针对已上传的表格文档（xlsx / xls / csv / tsv）用自然语言提问，获取答案与查询明细</p>
    </div>

    <el-card class="query-card">
      <template #header>
        <div class="card-header">
          <span class="header-title">提问</span>
        </div>
      </template>

      <div class="query-form">
        <el-form label-position="top">
          <el-form-item label="文档范围（可选）">
            <el-select
              v-model="selectedDocId"
              placeholder="全部表格文档"
              clearable
              style="width: 100%"
            >
              <el-option
                v-for="doc in tableDocuments"
                :key="doc.document_id"
                :label="doc.filename"
                :value="doc.document_id"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="问题">
            <el-input
              v-model="question"
              type="textarea"
              :rows="3"
              placeholder="例如：2023 年各季度的销售额总和分别是多少？"
              @keyup.ctrl.enter="submitQuery"
            />
          </el-form-item>

          <div class="query-actions">
            <el-button
              type="primary"
              :loading="loading"
              @click="submitQuery"
            >
              查询
            </el-button>
            <el-button @click="clearAll">清空</el-button>
          </div>
        </el-form>
      </div>
    </el-card>

    <!-- 结果区 -->
    <el-card v-if="hasResult" class="result-card">
      <template #header>
        <div class="card-header">
          <span class="header-title">回答</span>
          <div class="result-tags">
            <el-tag v-if="result?.intent" size="small" type="info">{{ result.intent }}</el-tag>
            <el-tag v-if="result?.chain" size="small" type="info">{{ result.chain }}</el-tag>
          </div>
        </div>
      </template>

      <div class="result-body">
        <!-- 回答正文 -->
        <div class="answer-block">
          <pre class="answer-text">{{ result?.answer || '（无回答内容）' }}</pre>
        </div>

        <!-- SQL + 结果表（仅 Phase2 精确查询有 sql） -->
        <template v-if="result?.sql">
          <el-divider content-position="left">执行的 SQL</el-divider>
          <pre class="sql-block">{{ result.sql }}</pre>

          <el-divider content-position="left">查询结果</el-divider>
          <el-table
            v-if="hasTableData"
            :data="tableData"
            border
            stripe
            style="width: 100%"
            :max-height="420"
          >
            <el-table-column
              v-for="col in result.columns"
              :key="col"
              :prop="col"
              :label="col"
              :formatter="cellFormatter"
              show-overflow-tooltip
            />
          </el-table>
          <el-empty v-else description="查询无返回行" :image-size="60" />
        </template>

        <!-- 引用来源 -->
        <template v-if="hasSources">
          <el-divider content-position="left">引用来源</el-divider>
          <div class="sources-list">
            <el-tag
              v-for="(src, idx) in result?.sources"
              :key="idx"
              class="source-tag"
              type="warning"
              effect="light"
            >
              {{ formatSource(src) }}
            </el-tag>
          </div>
        </template>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { tableQaApi, type TableQAResult } from '@/services/api'

const TABLE_TYPES = ['xlsx', 'xls', 'csv', 'tsv']

// 文档范围（Table QA 专用数据源：GET /table-qa/documents，来自 Table Representation，按 user_id 隔离）
const allDocuments = ref<any[]>([])
const tableDocuments = computed(() =>
  allDocuments.value.filter((d) => TABLE_TYPES.includes((d.type || '').toLowerCase()))
)
const selectedDocId = ref<string>('')

// 提问与结果
const question = ref('')
const loading = ref(false)
const result = ref<TableQAResult | null>(null)

const hasResult = computed(() => result.value !== null)
const hasSources = computed(
  () => Array.isArray(result.value?.sources) && result.value!.sources.length > 0
)

// 由 columns(string[]) + rows(any[][]) 构造 el-table 数据
// 做好空值与字段数量不一致保护：列多补 null，列少回退通用列名
const tableData = computed<Record<string, any>[]>(() => {
  const cols: string[] = result.value?.columns || []
  const rows: any[][] = result.value?.rows || []
  return rows.map((row: any[]) => {
    const safeRow = Array.isArray(row) ? row : []
    const obj: Record<string, any> = {}
    if (cols.length > 0) {
      cols.forEach((col, i) => {
        obj[col] = i < safeRow.length ? safeRow[i] : null
      })
    } else {
      safeRow.forEach((v, i) => {
        obj[`列${i + 1}`] = v
      })
    }
    return obj
  })
})

const hasTableData = computed(() => tableData.value.length > 0)

const cellFormatter = (_row: any, _col: any, value: any) => {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

const formatSource = (src: any): string => {
  if (!src) return '未知来源'
  const name = src.filename || src.document_id || '文档'
  const sheet = src.sheet_name ? `・${src.sheet_name}` : ''
  const range = src.range ? `・${src.range}` : ''
  return `${name}${sheet}${range}`
}

const loadDocuments = async () => {
  try {
    const res = await tableQaApi.getDocuments()
    allDocuments.value = (res.documents || []).map((d: any) => ({
      document_id: d.document_id,
      filename: d.filename,
      type: d.type
    }))
  } catch (error) {
    console.error('加载文档失败:', error)
    ElMessage.error('加载文档列表失败')
  }
}

const submitQuery = async () => {
  const q = question.value.trim()
  if (!q) {
    ElMessage.warning('请输入问题')
    return
  }
  if (tableDocuments.value.length === 0 && !selectedDocId.value) {
    ElMessage.warning('当前没有可查询的表格文档，请先在知识库上传表格文件')
    return
  }
  loading.value = true
  result.value = null
  try {
    const res = await tableQaApi.query(q, selectedDocId.value || undefined, false, [])
    result.value = res
  } catch (error: any) {
    const detail = error?.response?.data?.detail || error?.message || '查询失败'
    ElMessage.error('查询失败：' + detail)
  } finally {
    loading.value = false
  }
}

const clearAll = () => {
  question.value = ''
  result.value = null
  selectedDocId.value = ''
}

onMounted(() => {
  loadDocuments()
})
</script>

<style scoped lang="scss">
.table-qa {
  padding: 20px;
}

.page-header {
  margin-bottom: 24px;

  h1 {
    font-size: 28px;
    color: var(--text-primary);
    margin-bottom: 8px;
  }

  p {
    color: var(--text-secondary);
    font-size: 15px;
  }
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;

  .header-title {
    font-size: 18px;
    font-weight: 600;
  }

  .result-tags {
    display: flex;
    gap: 8px;
  }
}

.query-actions {
  display: flex;
  gap: 12px;
  margin-top: 4px;
}

.result-card {
  margin-top: 20px;
}

.answer-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.7;
  font-family: inherit;
  color: #303133;
  font-size: 14px;
}

.sql-block {
  margin: 0;
  padding: 12px 14px;
  background: #1e1e1e;
  color: #d4d4d4;
  border-radius: 6px;
  font-family: "Courier New", Courier, monospace;
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
}

.sources-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.source-tag {
  max-width: 100%;
}
</style>
