<template>
  <view class="page">
    <view class="page-header">
      <view>
        <text class="eyebrow">{{ greeting }}</text>
        <text class="title">{{ pageTitle }}</text>
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
      <view v-for="task in tasks" :key="task.id" class="panel task-card" :class="`state-${stateTone(task.state)}`">
        <view class="task-head">
          <text class="task-kicker">{{ task.cohort_name || '论文批次' }} · {{ task.stage_name || '阶段任务' }}</text>
          <text class="status" :class="`tone-${stateTone(task.state)}`">{{ stateLabel(task.state) }}</text>
        </view>
        <text class="task-title">{{ task.title || '论文阶段任务' }}</text>
        <view class="deadline-row">
          <text class="muted">截止：{{ formatDeadline(task) }}</text>
          <text v-if="task.is_late" class="late-tag">已逾期</text>
        </view>
        <button class="secondary-button" @tap="primaryAction(task)">{{ primaryLabel(task) }}</button>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { describeError, fetchTasks, type Task } from '../../api/request'
import { useSessionStore } from '../../stores/session'
import { ensureSessionReady } from '../../utils/session-guard'

const session = useSessionStore()
const tasks = ref<Task[]>([])
const loading = ref(false)
const errorMessage = ref('')
const isTeacher = computed(() => session.user?.role === 'teacher')
const greeting = computed(() => session.user?.display_name || session.user?.username || '你好')
const pageTitle = computed(() => (isTeacher.value ? '待我审核' : '我的任务'))
const roleLabel = computed(() => (isTeacher.value ? '指导教师' : '学生'))

onShow(() => {
  if (!ensureSessionReady()) return
  // 角色自适应首屏标签(设计方案 §5.1): 教师首屏为待审核队列
  uni.setTabBarItem({ index: 0, text: isTeacher.value ? '待审核' : '任务' })
  void loadTasks()
})

async function loadTasks() {
  loading.value = true
  errorMessage.value = ''
  try {
    tasks.value = await fetchTasks()
  } catch (error) {
    errorMessage.value = describeError(error)
  } finally {
    loading.value = false
  }
}

// 状态三表达(文字+颜色+卡片色条): 蓝=进行/待审, 橙=需处理, 绿=完成, 灰=未开始/退出
const STATE_TONES: Record<string, { label: string; tone: 'blue' | 'orange' | 'green' | 'gray' }> = {
  not_started: { label: '未开始', tone: 'gray' },
  in_progress: { label: '进行中', tone: 'blue' },
  submitted: { label: '待审核', tone: 'blue' },
  needs_revision: { label: '需修改', tone: 'orange' },
  approved: { label: '已通过', tone: 'green' },
  pending_archive: { label: '待归档', tone: 'blue' },
  archived: { label: '已归档', tone: 'green' },
  exited: { label: '已退出', tone: 'gray' },
}

function stateMeta(state: unknown) {
  return STATE_TONES[String(state)] || { label: '进行中', tone: 'blue' as const }
}
function stateLabel(state: unknown) {
  return stateMeta(state).label
}
function stateTone(state: unknown) {
  return stateMeta(state).tone
}

function formatDeadline(task: Task) {
  const value = task.revision_deadline_at || task.effective_deadline_at
  if (!value) return '以电脑端任务要求为准'
  return String(value).replace('T', ' ').slice(0, 16)
}

function primaryLabel(task: Task) {
  const state = String(task.state || '')
  if (isTeacher.value) return '去审阅'
  if (state === 'needs_revision') return '查看审阅意见'
  if (state === 'in_progress') return '预检提交'
  return '在电脑端处理'
}

function primaryAction(task: Task) {
  const submissionId = task.current_submission?.id || ''
  if (isTeacher.value) {
    if (String(task.state) === 'submitted' && submissionId) {
      uni.navigateTo({ url: `/pages/review/index?id=${submissionId}` })
      return
    }
    uni.showToast({ title: '该任务暂无可审阅的提交', icon: 'none' })
    return
  }
  const state = String(task.state || '')
  if (state === 'in_progress') {
    const stage = encodeURIComponent(task.stage_name || '')
    uni.navigateTo({ url: `/pages/submit/index?participant_id=${task.id}&stage=${stage}` })
    return
  }
  if (state === 'needs_revision' && submissionId) {
    uni.navigateTo({ url: `/pages/submissions/comments?id=${submissionId}` })
    return
  }
  if (submissionId) {
    uni.navigateTo({ url: `/pages/submissions/detail?id=${submissionId}` })
    return
  }
  uni.showToast({ title: '该操作请使用电脑端完成', icon: 'none' })
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
.task-card { border-left: 8rpx solid #004ac6; }
.task-card.state-orange { border-left-color: #d97706; }
.task-card.state-green { border-left-color: #16a34a; }
.task-card.state-gray { border-left-color: #9ca3af; }
.task-head { display: flex; justify-content: space-between; align-items: center; }
.task-kicker { color: #6b7280; font-size: 23rpx; }
.status { font-size: 23rpx; font-weight: 700; }
.tone-blue { color: #004ac6; }
.tone-orange { color: #d97706; }
.tone-green { color: #16a34a; }
.tone-gray { color: #6b7280; }
.task-title { display: block; margin-top: 20rpx; color: #111827; font-size: 34rpx; font-weight: 750; }
.deadline-row { display: flex; align-items: center; gap: 16rpx; }
.deadline-row .muted { margin-top: 12rpx; }
.late-tag { margin-top: 12rpx; padding: 4rpx 14rpx; border-radius: 999rpx; background: #fef3c7; color: #b45309; font-size: 21rpx; font-weight: 700; }
.secondary-button { margin: 28rpx 0 0; height: 78rpx; line-height: 78rpx; border: 1rpx solid #004ac6; border-radius: 12rpx; background: #fff; color: #004ac6; font-size: 26rpx; }
.error-text { color: #ba1a1a; font-size: 25rpx; line-height: 1.5; }
</style>
