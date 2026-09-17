<template>
  <div class="chat-container">
    <!-- 顶部工具栏 -->
    <div class="chat-toolbar">
      <div class="toolbar-left">
        <el-button type="text" @click="goBack">
          <el-icon>
            <ArrowLeft/>
          </el-icon>

          返回
        </el-button>
        <div class="character-info">
          <span class="character-icon">🤖</span>
          <div>
            <h3>{{ character?.name || 'AI助手' }}</h3>
            <p class="character-model">{{ character?.model || '默认模型' }}</p>
          </div>
        </div>
      </div>
      <div class="toolbar-right">
        <el-button type="text" @click="clearConversation">
          <el-icon>
            <Delete/>
          </el-icon>
          清空对话
        </el-button>
        <el-button type="text" @click="exportConversation">
          <el-icon>
            <Download/>
          </el-icon>
          导出
        </el-button>
        <el-button type="text" @click="showKnowledgeBase">
          <el-icon>
            <Folder/>
          </el-icon>
          知识库
        </el-button>
        <el-dropdown @command="handleToolCommand">
          <el-button type="text">
            <el-icon>
              <More/>
            </el-icon>
            更多
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="copy">复制对话</el-dropdown-item>
              <el-dropdown-item command="save">保存模板</el-dropdown-item>
              <el-dropdown-item command="settings">对话设置</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>

    <!-- RAG开关 -->
      <div class="rag-controls" v-if="character?.supportsRAG !== false">
        <div class="rag-controls-inner">
          <el-switch
            v-model="enableRAG"
            active-text="启用知识库"
            inactive-text="关闭知识库"
            @change="toggleRAG"
          />

          <div class="rag-info" v-if="enableRAG">
            <el-tag typr="success" size="small">
              <el-icon><Check /></el-icon>
              知识库已启用
            </el-tag>

            <el-button
              type="text"
              size="small"
              @click="showKnowledgeBase"
              class="menage-btn"
            >
              <el-idcon><Folder /></el-idcon>
              管理知识库
            </el-button>

            <el-tooltip content="当前对话将基于您上传的文档进行智能回答" placeholder="top">
              <el-icon class="info-icon"><InfoFilled /></el-icon>
            </el-tooltip>
          </div>
        </div>
      </div>

    <!-- 对话区域 -->
    <div class="chat-messages" ref="messagesContainer">
      <!-- 历史加载中 -->
      <div v-if="isLoadingHistory" class="history-loading">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>正在加载历史消息…</span>
      </div>

      <!-- 欢迎消息 -->
      <div v-if="!isLoadingHistory && messages.length === 0" class="welcome-message">
        <div class="welcome-icon"></div>
        <h2>开始与{{ character?.name || 'AI助手' }}对话</h2>
        <p>输入您的问题，{{ character?.name || 'AI助手' }}将为您提供帮助</p>

        <div class="quick-questions">
          <h4>快速提问：</h4>
          <div class="quick-chips">
            <el-tag
              v-for="(question, index) in quickQuestions"
              :key="index"
              class="question-chips"

              @click="sendQuickQuestion(question)"
            >
              {{ question }}
            </el-tag>
          </div>
        </div>
      </div>

      <!-- 消息列表 -->
      <div v-else class="messages-list">
        <div
            v-for="(message, index) in messages"
            :key="index"
            :class="['message-item', message.role]"
          >
            <div class="message-avatar">
              <span v-if="message.role === 'user'">👤</span>
              <span v-else>🤖</span>
            </div>
          <div class="message-content">
              <div class="message-header">
                <span class="sender">
                  {{ message.role === 'user' ? '你' : character?.name || 'AI助手' }}</span>
                <span class="timestamp">{{ formatTime(message.timestamp) }}</span>
              </div>
            <div class="message-body">
                <!-- 用户消息 -->
                <div v-if="message.role === 'user'" class="user-message">
                  {{ message.content }}
                </div>

              <!-- AI消息 -->
              <div v-else class="ai-message">
                  <!-- 流式阶段：首包到达前显示打字指示；首包后实时显示已累积文本 + 光标（逐 chunk 更新） -->
                  <div v-if="message.isStreaming && !message.content" class="streaming-indicator">
                    <span class="typing-dots">
                      <span></span><span></span><span></span>
                    </span>
                  </div>
                  <div v-else-if="message.isStreaming" class="markdown-content streaming-text">
                    {{ message.content }}<span class="stream-cursor">▋</span>
                  </div>
                  <div v-else class="markdown-content" v-html="renderMarkdown(message.content)">
                  </div>

                <!-- 消息操作 -->
                <div class="message-actions">
                  <el-button
                    type="text"
                    size="small"

                    @click="copyMessage(message.content)"
                  >
                    <el-icon><CopyDocument /></el-icon>
                    复制
                  </el-button>
                  <el-button
                    type="text"
                    size="small"

                    @click="regenerateMessage(index)"
                  >
                    <el-icon><Refresh /></el-icon>
                    重新生成
                  </el-button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 加载指示器 -->
        <div v-if="isLoading" class="loading-indicator">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>AI正在思考...</span>
        </div>
      </div>
    </div>

    <!-- 输入区域 -->
    <div class="chat-input-area">
      <div class="input-tools">
        <el-tooltip content="上传文件" placement="top">
          <el-button type="text"
                     @click="toggleFileUpload">
            <el-icon><Paperclip /></el-icon>
          </el-button>
        </el-tooltip>

        <el-tooltip content="表情符号" placement="top">
          <el-button type="text"
                     @click="showPromptTemplates">
            <el-icon><MagicStick /></el-icon>
          </el-button>
        </el-tooltip>
      </div>

      <!-- 文件上传区域 -->
      <div v-if="showFileUpload"
           class="file-upload-area">
        <FileUploader @file-uploaded="handleFileUploaded" />
      </div>

      <!-- 输入框 -->
      <div class="input-wrapper">
        <el-input
          v-model="inputMessage"
          type="textarea"
          :rows="3"
          :maxlength="2000"
          placeholder="输入消息...（Shift+Enter换行，Enter发送）"

          @keydown.enter.exact.prevent="sendMessage"

          @keydown.shift.enter.exact.prevent="inputMessage += '\n'"
          resize="none"
          :disabled="isLoading"
        />
        <div class="input-actions">
          <span class="char-count">{{ inputMessage.length }}/2000</span>
          <el-button
            type="primary"
            :loading="isLoading"
            :disabled="!inputMessage.trim()"
            @click="sendMessage"
          >
            <template #loading>
              <el-icon class="is-loading"><Loading /></el-icon>
              发送中
            </template>
            <template #default>
              <el-icon><Promotion /></el-icon>
              发送
            </template>
          </el-button>
        </div>
      </div>

      <!-- 快捷操作 -->
      <div class="quick-actions">
        <el-button
          v-for="action in quickActions"
          :key="action.label"
          size="small"
          :type="action.type"
          plain
          @click="action.handler"
        >
          <el-icon v-if="action.icon"><component :is="action.icon" /></el-icon>
          {{ action.label }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch} from "vue"
import { useRoute, useRouter } from "vue-router"
import { ElMessage, ElMessageBox } from "element-plus";
import {
  ArrowLeft,
  Delete,
  More,
  CopyDocument,
  Download,
  Paperclip,
  Star,
  View,
  Edit,
  Loading,
  MagicStick,
  Promotion,
  Refresh,
  ChatLineRound, InfoFilled
} from "@element-plus/icons-vue";
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useCharacterStore } from "@/stores/character"
import { getCharacterConversations, clearCharacterConversation } from "@/api/character"
import { ragApi } from "@/services/api"
import FileUploader from '@/components/chat/FileUploader.vue'
import type { Character, SpeakResponse } from "@/types/character"
import { useStreamingChat } from '@/composables/useStreamingChat'

const route = useRoute()
const router = useRouter()
const characterStore = useCharacterStore()

// 路由参数
const characterId = computed(() => route.params.id as string)
const character = ref<Character | null>(null)
const messages = ref<any[]>([
  // 可以添加初始欢迎消息
  {
    role:'assistant',
    content: `你好！我是${character.value?.name || 'AI助手'}，很高兴为您服务！`,
    timestamp: new Date().toISOString()
  }
])

// 在loadCharacter 成功后更新
if (messages.value[0]?.role === 'assistant') {
  messages.value[0].content = `你好！我是${character.value?.name || 'AI助手'}，很高兴为您服务！`
}
const inputMessage = ref('')
const isLoading = ref(false)
const showFileUpload = ref(false)
const enableRAG = ref(false)
const ragSources = ref<any[]>([])   // 存储查询来源

// 历史消息加载状态（Stage 21）
const isLoadingHistory = ref(false)
const historyLoadedFor = ref<string | null>(null)  // 已加载历史的角色 id，防止重复加载
const hasUserInteracted = ref(false)               // 用户是否已发送/交互，避免历史覆盖实时消息

// DOM引用
const messagesContainer = ref<HTMLElement>()

// 快速问题示例
const quickQuestions = ref([
  '帮我写一个Python函数',
  '解释一下什么是RAG',
  '如何优化数据库查询？',
  '写一个关于人工智能的简短故事'
])

// 快捷操作
const quickActions = computed(() => [
  {
    label: '优化表达',
    type: 'primary',
    icon: ChatLineRound,
    handler: () => optimizeExpression(),
  },
  {
    label: '总结对话',
    type: 'success',
    icon: View,
    handler: () => summarizeConversation()
  },
  {
    label: '翻译成英文',
    type: 'warning',
    icon: Edit,
    handler: () => translateToEnglish()
  }
])

// 组件挂载时加载数据（含历史恢复，Stage 21）
onMounted(async () => {
  await loadCharacter()
  scrollToBottom()

  // 如果角色支持RAG，默认启用
  if (character.value?.supportsRAG) {
    enableRAG.value = true
  }

  // 监听窗口变化
  window.addEventListener('resize', scrollToBottom)
})

onUnmounted(() => {
  window.removeEventListener('resize', scrollToBottom)
})

// 生成欢迎消息（使用已加载的角色名）
const createWelcomeMessage = () => ({
  role: 'assistant',
  content: `你好！我是${character.value?.name || 'AI助手'}，很高兴为您服务！`,
  timestamp: new Date().toISOString()
})

// 加载对话历史（Stage 21：进入聊天页或切换角色后从后端恢复历史）
const loadHistory = async () => {
  // 防重复：同一角色只加载一次；若用户已开始交互，则不再用历史覆盖实时消息
  if (historyLoadedFor.value === characterId.value) return
  if (hasUserInteracted.value) {
    historyLoadedFor.value = characterId.value
    return
  }
  historyLoadedFor.value = characterId.value
  isLoadingHistory.value = true
  try {
    const res = await getCharacterConversations(characterId.value, 100) as any
    const list: any[] = (res?.conversations || [])
    if (list.length > 0) {
      // 映射为 Chat.vue 使用的 message 结构，保持数据库时间顺序（role/content/created_at）
      messages.value = list
        .filter((m: any) => m.role === 'user' || m.role === 'assistant')
        .map((m: any) => ({
          role: m.role,
          content: m.content,
          timestamp: m.created_at || new Date().toISOString()
        }))
    } else {
      // 无历史：显示欢迎语（不重复）
      messages.value = [createWelcomeMessage()]
    }
  } catch (error: any) {
    // 历史加载失败：不白屏，保留欢迎语，提示用户，异常不吞掉
    console.error('加载对话历史失败:', error)
    messages.value = [createWelcomeMessage()]
    const status = error?.response?.status
    if (status !== 401) {
      ElMessage.warning('历史消息加载失败，已开始新对话')
    }
  } finally {
    isLoadingHistory.value = false
    scrollToBottom()
  }
}

// 加载角色信息
const loadCharacter = async () => {
  // 没有 characterId（如裸 /chat）时不发起 GET /characters/undefined
  if (!characterId.value) {
    ElMessage.warning('未指定角色，无法加载对话')
    router.push('/characters')
    return
  }
  try {
    await characterStore.fetchCharacter(characterId.value)
    character.value = characterStore.currentCharacter
    // 角色加载成功后再加载历史（JWT 由 requests 拦截器自动携带）
    await loadHistory()
  } catch (error) {
    ElMessage.error('加载角色失败')
    router.push('/characters')
  }
}

// 发送消息
const sendMessage = async () => {
  const message = inputMessage.value.trim()
  if (!message || isLoading.value) return
  hasUserInteracted.value = true

  if (enableRAG.value) {
    // 使用RAG查询
    await sendRAGMessage(message)
  } else {
    // 使用普通对话
    await sendNormalMessage(message)
  }
}

// 普通发送方法
const sendNormalMessage = async (message: string) => {
  // 添加到消息列表
  const userMessage = {
    role: 'user',
    content: message,
    timestamp: new Date().toISOString()
  }
  messages.value.push(userMessage)

  // 添加AI回复占位符
  const aiMessageIndex = messages.value.length
  messages.value.push({
    role: 'assistant',
    content: '',
    isStreaming: true,
    timestamp: new Date().toISOString()
  })

  inputMessage.value = ''
  isLoading.value = true
  scrollToBottom()

  try {
    // 根据设置选择使用流式还是普通API
    const useStream = true // 可以从设置中获取这个值

    if (useStream) {
      // 流式调用
      const { sendMessage: sendStreaming } = useStreamingChat({
        characterId: characterId.value,
        onChunk: (_chunk, accumulated) => {
          messages.value[aiMessageIndex].content = accumulated
          scrollToBottom()
        },
        onComplete: (fullResponse) => {
          messages.value[aiMessageIndex] = {
            role: 'assistant',
            content: fullResponse,
            timestamp: new Date().toISOString(),
            isStreaming: false
          }
          isLoading.value = false
          scrollToBottom()
        },
        onError: (error) => {
          console.error('流式响应错误:', error)
          messages.value.splice(aiMessageIndex, 1)
          ElMessage.error('请求失败: ' + error.message)
          isLoading.value = false
        }
      })

      await sendStreaming(message)
    } else {
      // 普通API调用
      const response = await characterStore.speakToCharacter(
        characterId.value,
        message
      )

      messages.value[aiMessageIndex] = {
        role: 'assistant',
        content: response.response,
        timestamp: response.timestamp,
        isStreaming: false
      }
      isLoading.value = false
      scrollToBottom()
    }
  } catch (error) {
    console.error('发送消息失败:', error)
    messages.value.splice(aiMessageIndex, 1)
    ElMessage.error('发送消息失败，请重试')
    isLoading.value = false
  }
}

// RAG发送方法
const sendRAGMessage = async (message: string) => {
  const userMessage = {
    role: 'user',
    content: message,
    timestamp: new Date().toISOString(),
    isRAG: true
  }
  messages.value.push(userMessage)
  const userMsgIndex = messages.value.length - 1

  // 添加AI回复占位符
  const aiMessageIndex = messages.value.length
  messages.value.push({
    role: 'assistant',
    content: '',
    isStreaming: true,
    isRAG: true,
    timestamp: new Date().toISOString(),
    sources: []   // 初始化来源数组
  })

  inputMessage.value = ''
  isLoading.value = true
  scrollToBottom()

  // 构建多轮历史（不含当前用户消息与占位），注入 query-with-history 维持会话连贯
  const history = messages.value
    .slice(0, userMsgIndex)
    .filter((m: any) => (m.role === 'user' || m.role === 'assistant') && m.content && !m.isStreaming)
    .map((m: any) => ({ role: m.role, content: m.content }))

  try {
    // RAG 流式调用：复用与普通 Chat 相同的 NDJSON 解析器，逐 chunk 渲染；
    // 走 query-with-history 以携带对话历史 + 知识库检索，并保持真正 Streaming。
    const { sendMessage: sendRAGStream } = useStreamingChat({
      url: '/api/v1/rag/query-with-history',
      buildBody: (msg: string) => ({ query: msg, history, stream: true, context_count: 3, character_id: characterId.value }),
      onChunk: (_chunk: string, accumulated: string) => {
        messages.value[aiMessageIndex].content = accumulated
        scrollToBottom()
      },
      onComplete: (fullResponse: string) => {
        messages.value[aiMessageIndex] = {
          role: 'assistant',
          content: fullResponse,
          timestamp: new Date().toISOString(),
          isStreaming: false,
          isRAG: true,
          sources: [],
          fromKnowledgeBase: true
        }
        isLoading.value = false
        scrollToBottom()
      },
      onError: (error: Error) => {
        console.error('RAG流式响应错误:', error)
        messages.value[aiMessageIndex] = {
          role: 'assistant',
          content: '知识库查询失败，请检查网络连接或知识库状态。',
          timestamp: new Date().toISOString(),
          isStreaming: false,
          isRAG: true,
          error: true
        }
        isLoading.value = false
        scrollToBottom()
      }
    })

    await sendRAGStream(message)

  } catch (error) {
    console.error('RAG查询失败：', error)

    messages.value[aiMessageIndex] = {
      role: 'assistant',
      content: '知识库查询失败，请检查网络连接或知识库状态。',
      timestamp: new Date().toISOString(),
      isStreaming: false,
      isRAG: true,
      error: true
    }
  } finally {
    isLoading.value = false
    scrollToBottom()
  }
}

// ========添加RAG控制方法========
const toggleRAG = (enabled: boolean) => {
  enableRAG.value = enabled
  if (enabled) {
    ElMessage.success('已启用知识库，对话将基于您的文档内容')
  } else {
    ElMessage.info('已关闭知识库，使用普通对话模式')
  }
}

const showKnowledgeBase = () => {
  // 跳转到知识库管理页面
  router.push('/knowledge')
}

// 切换角色（/chat/:id 参数变化，组件不重新挂载）：清理旧角色内存消息并重新加载
watch(characterId, async (newId: string, oldId: string) => {
  if (newId === oldId) return
  messages.value = [createWelcomeMessage()]
  isLoadingHistory.value = false
  historyLoadedFor.value = null
  hasUserInteracted.value = false
  inputMessage.value = ''
  await loadCharacter()
})

// 发送快速问题
const sendQuickQuestion = (question: string) => {
  inputMessage.value = question
  sendMessage()
}

// 清空对话（前端内存 + 后端持久化，Stage 23）
const clearConversation = async () => {
  // 1. 弹出确认
  try {
    await ElMessageBox.confirm(
      '确定要清空当前对话吗？此操作不可撤销，将删除所有聊天记录。',
      '确认清空',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
  } catch {
    return  // 用户取消，不执行任何清空
  }

  try {
    // 2. 调用后端 DELETE，删除成功后再清理前端状态
    await clearCharacterConversation(characterId.value)
    messages.value = [createWelcomeMessage()]
    historyLoadedFor.value = null
    hasUserInteracted.value = false
    inputMessage.value = ''
    ElMessage.success('对话已清空')
  } catch (error: any) {
    // 后端删除失败：保持现有历史，不假装清空成功
    if (error?.response?.status !== 401) {
      ElMessage.error('清空对话失败，请稍后重试')
    }
  }
}

// 复制消息
const copyMessage = async (content: string) => {
  try {
    await navigator.clipboard.writeText(content)
    ElMessage.success('已复制到剪贴板')
  } catch (error) {
    ElMessage.error('复制失败')
  }
}

// 重新生成消息
const regenerateMessage = async (index: number) => {
  // 获取用户的上一条消息
  const userMessageIndex = index - 1
  if (userMessageIndex < 0 || messages.value[userMessageIndex].role !== 'user') {
    ElMessage.warning('无法重新生成此消息')
    return
  }

  const userMessage = messages.value[userMessageIndex].content

  // 移除当前AI回复
  messages.value.splice(index, 1)

  // 添加流式占位符
  messages.value.push({
    role: 'assistant',
    content: '',
    isStreaming: true,
    timestamp: new Date().toISOString()
  })
  const aiMessageIndex = messages.value.length - 1

  isLoading.value = true
  scrollToBottom()

  try {
    // 复用普通对话流式链路（真实 LLM + 逐 chunk）；服务侧自动加载多轮历史
    const { sendMessage: sendStreaming } = useStreamingChat({
      characterId: characterId.value,
      onChunk: (_chunk: string, accumulated: string) => {
        messages.value[aiMessageIndex].content = accumulated
        scrollToBottom()
      },
      onComplete: (fullResponse: string) => {
        messages.value[aiMessageIndex] = {
          role: 'assistant',
          content: fullResponse,
          timestamp: new Date().toISOString(),
          isStreaming: false
        }
        isLoading.value = false
        scrollToBottom()
      },
      onError: (error: Error) => {
        console.error('重新生成流式响应错误:', error)
        messages.value.splice(aiMessageIndex, 1)
        ElMessage.error('重新生成失败：' + error.message)
        isLoading.value = false
      }
    })
    await sendStreaming(userMessage)
  } catch (error: any) {
    console.error('重新生成失败：', error)
    messages.value.splice(aiMessageIndex, 1)
    ElMessage.error('重新生成失败')
    isLoading.value = false
  }
}

// 导出对话（纯前端下载为 .txt，标注发言角色与时间）
const exportConversation = () => {
  if (messages.value.length === 0) {
    ElMessage.warning('当前没有对话内容')
    return
  }
  const conversationText = messages.value
    .filter(m => !m.isStreaming && m.content)
    .map(m => {
      const role = m.role === 'user' ? '用户' : 'AI助手'
      const time = m.timestamp ? ` [${new Date(m.timestamp).toLocaleString()}]` : ''
      return `${role}${time}：\n${m.content}`
    })
    .join('\n\n')

  const blob = new Blob([conversationText], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `对话记录_${new Date().toISOString().split('T')[0]}.txt`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)

  ElMessage.success('对话已导出')
}

// 工具命令处理
const handleToolCommand = (command: string) => {
  switch (command) {
    case 'copy':
      exportConversation()
      break
    case 'save':
      ElMessage.info('保存模板功能开发中')
      break
    case 'settings':
      ElMessage.info('对话设置功能开发中')
      break
  }
}

// 优化表达
const optimizeExpression = () => {
  inputMessage.value = `请优化这段文字的表达：${inputMessage.value}`
}

// 总结对话
const summarizeConversation = () => {
  if (messages.value.length === 0) {
    ElMessage.warning('当前没有对话内容')
    return
  }

  const conversationText = messages.value
    .slice(-5)
    .map(msg => `${msg.role === 'user' ? '用户' : 'AI'}: ${msg.content}`)
    .join('\n')

  inputMessage.value = `请总结以下对话内容：\n\n${conversationText}`
}

// 翻译成英文
const translateToEnglish = () => {
  if (!inputMessage.value.trim()) {
    ElMessage.warning('请输入要翻译的内容')
    return
  }

  inputMessage.value = `请将以下内容翻译成英文：${inputMessage.value}`
}

// 处理文件上传
const handleFileUploaded = (fileData: any) => {
  inputMessage.value += `[文件：${fileData.name}]\n${fileData.content}`
  showFileUpload.value = false
  ElMessage.success('文件已上传')
}

// 切换文件上传显示
const toggleFileUpload = () => {
  showFileUpload.value = !showFileUpload.value
}

// 切换表情选择器
const toggleEmojiPicker = () => {
  ElMessage.info('表情选择器功能开发中')
}

// 显示提示词模板
const showPromptTemplates = () => {
  ElMessage.info('提示词模板功能开发中')
}

// 返回上一页
const goBack = () => {
  router.push('/characters')
}

// 滚动到底部
const scrollToBottom = () => {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

// 格式化时间
const formatTime = (timestamp: string) => {
  const date = new Date(timestamp)
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit'
  })
}

// 渲染Markdown
const renderMarkdown = (content: string) => {
  const rawHtml = marked.parse(content) as string
  return DOMPurify.sanitize(rawHtml)
}

// 监听消息变化，自动滚动
watch(messages, () => {
  scrollToBottom()
}, {deep: true})
</script>

<style scoped lang="scss">
.chat-container {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: linear-gradient(135deg, #f5f7fa 0%, #e4e7ed 100%);
}

.chat-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  background: white;
  border-bottom: 1px solid var(--border-light);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);

  .toolbar-left {
    display: flex;
    align-items: center;
    gap: 20px;

    .character-info {
      display: flex;
      align-items: center;
      gap: 12px;

      .character-icon {
        font-size: 24px;
      }

      h3 {
        margin: 0;
        font-size: 16px;
        font-weight: 600;
        color: var(--text-primary);
      }

      .character-model {
        margin: 2px 0 0;
        font-size: 12px;
        color: var(--text-secondary);
        background: var(--bg-base);
        padding: 2px 8px;
        border-radius: 10px;
        display: inline-block;
      }
    }
  }

  .toolbar-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  background: var(--bg-base);

  // 自定义滚动条
  &::-webkit-scrollbar {
    width: 6px;
  }

  &::-webkit-scrollbar-track {
    background: transparent;
  }

  &::-webkit-scrollbar-thumb {
    background: var(--border-base);
    border-radius: 3px;

    &:hover {
      background: var(--text-placeholder);
    }
  }
}

.welcome-message {
  text-align: center;
  padding: 60px 20px;
  max-width: 600px;
  margin: 0 auto;

  .welcome-icon {
    font-size: 64px;
    margin-bottom: 20px;
  }

  h3 {
    font-size: 24px;
    color: var(--text-primary);
    margin-bottom: 12px;
  }

  p {
    color: var(--text-secondary);
    margin-bottom: 40px;
    font-size: 16px;
  }

  .quick-questions {
    h4 {
      margin-bottom: 16px;
      color: var(--text-regular);
    }

    .question-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: center;

      .question-chip {
        cursor: pointer;
        transition: all 0.3s;

        &:hover {
          background: var(--primary-color);
          color: white;
          transform: translateY(-2px);
        }
      }
    }
  }
}

.messages-list {
  max-width: 800px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.message-item {
  display: flex;
  gap: 12px;
  animation: fadeIn 0.3s ease;

  &.user {
    flex-direction: row-reverse;

    .message-content {
      align-items: flex-end;

      .message-header {
        flex-direction: row-reverse;
      }

      .user-message {
        background: linear-gradient(135deg, #409eff 0%, #67c23a 100%);
        color: white;
        border-radius: 18px 18px 4px 18px;
      }
    }
  }

  &.assistant {
    .ai-message {
      background: white;
      border-radius: 18px 18px 18px 4px;
      border: 1px solid var(--border-light);
    }
  }

  // 流式输出期间：保留换行/空格，实时呈现已累积文本
  .streaming-text {
    white-space: pre-wrap;
    word-break: break-word;
  }

  // 流式光标（闪烁）
  .stream-cursor {
    display: inline-block;
    margin-left: 2px;
    font-weight: bold;
    color: var(--text-secondary, #909399);
    animation: stream-blink 1s step-start infinite;
  }

  @keyframes stream-blink {
    50% {
      opacity: 0;
    }
  }

  .message-avatar {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: white;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 20px;
    flex-shrink: 0;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  }

  .message-content {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-width: 70%;
  }

  .message-header {
    display: flex;
    align-items: center;
    gap: 8px;

    .sender {
      font-size: 14px;
      font-weight: 500;
      color: var(--text-primary);
    }

    .timestamp {
      font-size: 12px;
      color: var(--text-secondary);
    }
  }

  .message-body {
   .user-message,
   .ai-message {
     padding: 12px 16px;
     line-height: 1.6;
     word-break: break-word;
   }

    .user-message {
      background: var(--primary-color);
      color: white;
    }

    .ai-message {
      .streaming-indicator {
        padding: 12px 16px;

        .typing-dots {
          display: inline-flex;
          gap: 4px;

          span {
            width: 8px;
            height: 8px;
            background: var(--text-secondary);
            border-radius: 50%;
            animation: typing 1.4s infinite ease-in-out;

            &:nth-child(2) {
              animation-delay: 0.2s;
            }

            &:nth-child(3) {
              animation-delay: 0.4s;
            }
          }
        }
      }

      .markdown-content {
        padding: 12px 16px;

        :deep(*) {
          margin: 8px 0;

          &:first-child {
            margin-top: 0;
          }

          &:last-child {
            margin-bottom: 0;
          }
        }

        :deep(code) {
          background: var(--bg-base);
          padding: 2px 6px;
          border-radius: 4px;
          font-family: 'Consolas', monospace;
          font-size: 14px;
        }

        :deep(pre) {
          background: var(--bg-base);
          padding: 12px;
          border-radius: 8px;
          overflow-x: auto;
          margin: 12px 0;

          code {
            background: transparent;
            padding: 0;
          }
        }

        :deep(blockquote) {
          border-left: 4px solid var(--border-light);
          padding-left: 12px;
          color: var(--text-secondary);
          margin-left: 0;
        }
      }

      .message-actions {
        padding: 8px 16px 12px;
        border-top: 1px solid var(--border-lighter);
        display: flex;
        gap: 8px;

        :deep(.el-button) {
          padding: 2px 8px;
          font-size: 12px;
          height: auto;
        }
      }
    }
  }
}

.loading-indicator {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 16px;
  color: var(--text-secondary);

  .el-icon {
    font-size: 18px;
  }
}

.history-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 16px;
  color: var(--text-secondary);

  .el-icon {
    font-size: 18px;
  }
}

.chat-input-area {
  background: white;
  border-top: 1px solid var(--border-light);
  padding: 16px 20px;
  box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.05);

  .input-tools {
    display: flex;
    gap: 8px;
    margin-bottom: 12px;

    :deep(.el-button) {
      padding: 8px;
    }
  }

  .file-upload-area {
    margin-bottom: 12px;
    border: 2px dashed var(--border-light);
    border-radius: 8px;
    padding: 16px;
    background: var(--bg-base);
  }

  .input-wrapper {
    .input-actions {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 8px;

      .char-count {
        font-size: 12px;
        color: var(--text-secondary);
      }

      :deep(.el-button) {
        height: 36px;
        padding: 0 20px;
      }
    }
  }

  .quick-actions {
    display: flex;
    gap: 8px;
    margin-top: 12px;
    justify-content: center;

    :deep(.el-button) {
      font-size: 12px;
      height: 28px;
      padding: 0 12px;
    }
  }
}

// 动画
@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes typing {
  0%, 60%, 100% {
    transform: translateY(0);
  }
  30% {
    transform: translateY(-6px);
  }
}

// 响应式设计
@media (max-width: 768px) {
  .chat-toolbar {
    padding: 8px 12px;

    .toolbar-left {
      gap: 12px;

      .character-info {
        h3 {
          font-size: 14px;
        }

        .character-model {
          font-size: 10px;
        }
      }
    }
  }

  .chat-messages {
    padding: 12px;
  }

  .welcome-message {
    padding: 40px 12px;

    h2 {
      font-size: 20px;
    }

    .question-chips {
      .question-chip {
        font-size: 12px;
      }
    }
  }

  .message-item {
    .message-content {
      max-width: 85%;
    }
  }

  .chat-input-area {
    padding: 12px;
  }
}

//
</style>
