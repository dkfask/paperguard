<template>
  <view class="page">
    <view class="page-header">
      <view>
        <text class="eyebrow">{{ greeting }}</text>
        <text class="title">我的任务</text>
      </view>
      <view class="role-mark">{{ roleLabel }}</view>
    </view>

    <view v-if="loading" class="panel state-panel"><text>正在加载任务…</text></view>
    <view v-else-if="errorMessage" class="panel state-panel">
      <text class="error-text">{{ errorMessage }}</text>
      <button class="secondary-button" @tap="loadTasks">重新加载</button>
    </view>
    <view v-else-if="tasks.length === 0" class="panel state-panel">
      <text class="empty-title">当前没有待办任务</text>
      <text class="muted">阶段发布后，任务会显示在这里。</text>
    </view>
    <view v-else>
      <view v-for="task in tasks" :key="task.id" class="panel task-card">
        <view class="task-head"><text class="task-kicker">当前阶段</text><text class="status">{{ stateLabel(task.state) }}</text></view>
        <text class="task-title">{{ task.title || task.stage_name || '论文阶段任务' }}</text>
        <text class="muted">截止时间：{{ formatDeadline(task.effective_deadline_at) }}</text>
        <button class="secondary-button" @tap="goDesktop">去电脑端处理</button>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { ApiError, fetchTasks, type Task } from '../../api/request'
import { useSessionStore } from '../../stores/session'

const session = useSessionStore()
const tasks = ref<Task[]>([])
const loading = ref(false)
const errorMessage = ref('')
const greeting = computed(() => session.user?.display_name || session.user?.username || '你好')
const roleLabel = computed(() => session.user?.role === 'teacher' ? '指导教师' : '学生')

onShow(() => {
  if (!session.isAuthenticated) {
    uni.redirectTo({ url: '/pages/login/index' })
    return
  }
  void loadTasks()
})

async function loadTasks() {
  loading.value = true
  errorMessage.value = ''
  try {
    tasks.value = await fetchTasks()
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '任务加载失败'
  } finally {
    loading.value = false
  }
}

function stateLabel(state: unknown) {
  return ({ submitted: '待审核', needs_revision: '需修改', approved: '已通过', archived: '已归档' } as Record<string, string>)[String(state)] || '进行中'
}

function formatDeadline(value: unknown) {
  if (!value) return '以桌面端任务要求为准'
  return String(value).replace('T', ' ').replace('+00:00', '')
}

function goDesktop() {
  uni.showToast({ title: '长篇写作请使用电脑端', icon: 'none' })
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 48rpx 36rpx 120rpx; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; }
.eyebrow { display: block; color: #6b7280; font-size: 24rpx; }
.title { display: block; margin-top: 8rpx; color: #111827; font-size: 50rpx; font-weight: 800; }
.role-mark { padding: 12rpx 18rpx; border-radius: 999rpx; background: #e5efff; color: #004ac6; font-size: 23rpx; }
.panel { margin-top: 34rpx; padding: 34rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 22rpx; box-shadow: 0 10rpx 30rpx rgba(17, 24, 39, .05); }
.state-panel { display: flex; flex-direction: column; min-height: 180rpx; justify-content: center; }
.empty-title { display: block; color: #111827; font-size: 31rpx; font-weight: 750; }
.muted { display: block; margin-top: 12rpx; color: #6b7280; font-size: 24rpx; line-height: 1.5; }
.task-head { display: flex; justify-content: space-between; align-items: center; }
.task-kicker { color: #004ac6; font-size: 23rpx; font-weight: 700; }
.status { color: #9a6700; font-size: 23rpx; }
.task-title { display: block; margin-top: 20rpx; color: #111827; font-size: 34rpx; font-weight: 750; }
.secondary-button { margin: 28rpx 0 0; height: 78rpx; line-height: 78rpx; border: 1rpx solid #004ac6; border-radius: 12rpx; background: #fff; color: #004ac6; font-size: 26rpx; }
.error-text { color: #ba1a1a; font-size: 25rpx; line-height: 1.5; }
</style>
