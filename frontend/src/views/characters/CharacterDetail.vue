<template>
  <div class="character-detail-container">
    <!-- 页面标题与操作 -->
    <div class="page-header">
      <div class="header-left">
        <el-button text @click="goBack">
          <el-icon><ArrowLeft /></el-icon>
          返回
        </el-button>
        <h1 class="page-title">角色详情</h1>
      </div>
      <div class="header-right">
        <el-button
          type="primary"
          :disabled="!character"
          @click="startConversation"
        >
          <el-icon><ChatDotRound /></el-icon>
          开始对话
        </el-button>
      </div>
    </div>

    <!-- 加载状态 -->
    <div v-if="isLoading" class="loading-state">
      <el-skeleton :rows="6" animated />
    </div>

    <!-- 角色详情 -->
    <el-card v-else-if="character" class="detail-card" shadow="never">
      <div class="detail-header">
        <div class="character-icon"><span class="icon">🤖</span></div>
        <div class="character-meta">
          <h2 class="character-name">{{ character.name }}</h2>
          <el-tag size="small" class="character-model">{{ character.model }}</el-tag>
        </div>
      </div>

      <el-divider />

      <div class="detail-section">
        <h3 class="section-title">系统提示词</h3>
        <p class="character-prompt">{{ character.system_prompt }}</p>
      </div>

      <div class="detail-stats">
        <div class="stat-item">
          <el-icon><ChatDotRound /></el-icon>
          <span>{{ character.conversation_count }} 对话</span>
        </div>
        <div class="stat-item">
          <el-icon><Clock /></el-icon>
          <span>创建于 {{ formatDate(character.created_at) }}</span>
        </div>
      </div>
    </el-card>

    <!-- 不存在 -->
    <el-empty v-else description="角色不存在或已被删除" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ChatDotRound, Clock } from '@element-plus/icons-vue'
import { useCharacterStore } from '@/stores/character'
import type { Character } from '@/types/character'

const route = useRoute()
const router = useRouter()
const characterStore = useCharacterStore()

const character = ref<Character | null>(null)
const isLoading = ref(false)

const characterId = computed(() => route.params.id as string)

onMounted(async () => {
  await loadCharacter()
})

const loadCharacter = async () => {
  isLoading.value = true
  try {
    await characterStore.fetchCharacter(characterId.value)
    const c = characterStore.currentCharacter as any
    // 校验返回的确实是有效角色对象（拦截器异常时可能为 error 对象）
    character.value = (c && c.id && c.name) ? (c as Character) : null
  } catch {
    character.value = null
  } finally {
    isLoading.value = false
  }
}

const goBack = () => router.push('/characters')
const startConversation = () => router.push(`/chat/${characterId.value}`)

const formatDate = (dateString?: string) => {
  if (!dateString) return '未知'
  return new Date(dateString).toLocaleDateString('zh-CN')
}
</script>

<style scoped lang="scss">
.character-detail-container {
  padding: 20px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;

  .header-left {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .page-title {
    font-size: 22px;
    font-weight: 600;
    color: #303133;
    margin: 0;
  }
}

.detail-card {
  max-width: 720px;
  border-radius: 12px;

  .detail-header {
    display: flex;
    align-items: center;
    gap: 16px;

    .character-icon {
      width: 56px;
      height: 56px;
      border-radius: 50%;
      background: #f5f7fa;
      display: flex;
      align-items: center;
      justify-content: center;

      .icon {
        font-size: 32px;
      }
    }

    .character-meta {
      .character-name {
        margin: 0 0 8px;
        font-size: 20px;
        font-weight: 600;
        color: #303133;
      }
    }
  }

  .detail-section {
    .section-title {
      font-size: 15px;
      color: #606266;
      margin: 0 0 10px;
    }

    .character-prompt {
      color: #303133;
      font-size: 14px;
      line-height: 1.7;
      white-space: pre-wrap;
      background: #f5f7fa;
      padding: 14px 16px;
      border-radius: 8px;
      margin: 0;
    }
  }

  .detail-stats {
    display: flex;
    gap: 24px;
    margin-top: 20px;

    .stat-item {
      display: flex;
      align-items: center;
      gap: 6px;
      color: #909399;
      font-size: 13px;
    }
  }
}

.loading-state {
  max-width: 720px;
}
</style>
