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
    <view v-else-if="isTeacher">
      <view v-if="pendingTasks.length === 0 && recheckTasks.length === 0 && restTasks.length === 0" class="panel state-panel">
        <text class="empty-title">当前没有待审任务</text>
        <text class="muted">学生正式提交后，待审核与待复核会显示在这里。</text>
      </view>
      <template v-else>
        <text v-if="pendingTasks.length" class="section-label">待审核 · {{ pendingTasks.length }}</text>
        <view
          v-for="task in pendingTasks"
          :key="task.id"
          class="panel task-card"
          :class="`state-${stateTone(task.state)}`"
        >
          <view class="task-head">
            <text class="task-kicker">{{ task.cohort_name || '论文批次' }} · {{ task.stage_name || '阶段任务' }}</text>
            <text class="status" :class="`tone-${stateTone(task.state)}`">{{ stateLabel(task.state) }}</text>
          </view>
          <text class="task-title">{{ task.student_name || task.student_id || '学生' }}</text>
          <text class="muted">{{ task.project_title || task.title || '论文阶段任务' }}</text>
          <view class="deadline-row">
            <text class="muted">{{ waitLabel(task) }} · {{ task.current_submission?.number || '待审提交' }}</text>
            <text v-if="task.is_late" class="late-tag">迟交</text>
          </view>
          <button class="secondary-button" @tap="openTask(task)">去审阅</button>
        </view>

        <text v-if="recheckTasks.length" class="section-label">待复核 · {{ recheckTasks.length }}</text>
        <view
          v-for="task in recheckTasks"
          :key="task.id"
          class="panel task-card state-orange"
        >
          <view class="task-head">
            <text class="task-kicker">{{ task.cohort_name || '论文批次' }} · {{ task.stage_name || '阶段任务' }}</text>
            <text class="status tone-orange">待复核</text>
          </view>
          <text class="task-title">{{ task.student_name || task.student_id || '学生' }}</text>
          <text class="muted">{{ task.project_title || task.title || '论文阶段任务' }} · 第 {{ task.current_submission?.review_cycle || 2 }} 轮</text>
          <view class="deadline-row">
            <text class="muted">{{ waitLabel(task) }} · {{ task.current_submission?.number || '待审提交' }}</text>
            <text v-if="task.is_late" class="late-tag">迟交</text>
          </view>
          <button class="secondary-button" @tap="openTask(task)">去复核</button>
        </view>

        <text v-if="restTasks.length" class="section-label">其他任务 · {{ restTasks.length }}</text>
        <view
          v-for="task in restTasks"
          :key="task.id"
          class="panel task-card"
          :class="`state-${stateTone(task.state)}`"
        >
          <view class="task-head">
            <text class="task-kicker">{{ task.student_name || task.student_id || '学生' }} · {{ task.stage_name || '阶段任务' }}</text>
            <text class="status" :class="`tone-${stateTone(task.state)}`">{{ stateLabel(task.state) }}</text>
          </view>
          <text class="task-title">{{ task.project_title || task.title || '论文阶段任务' }}</text>
          <button class="ghost-button" @tap="openTask(task)">{{ primaryLabel(task) }}</button>
        </view>
      </template>
    </view>
    <view v-else-if="tasks.length === 0" class="panel state-panel">
      <text class="empty-title">当前没有待办任务</text>
      <text class="muted">阶段发布后，任务会显示在这里。</text>
    </view>
    <view v-else>
      <view v-if="currentTasks.length === 0 && historyTasks.length" class="panel state-panel">
        <text class="empty-title">当前没有进行中的阶段</text>
        <text class="muted">已通过或已归档的阶段折叠在下方历史里。</text>
      </view>
      <view v-for="task in currentTasks" :key="task.id" class="panel task-card" :class="`state-${stateTone(task.state)}`">
        <view class="task-head">
          <text class="task-kicker">{{ task.cohort_name || '论文批次' }} · {{ task.stage_name || '阶段任务' }}</text>
          <text class="status" :class="`tone-${stateTone(task.state)}`">{{ stateLabel(task.state) }}</text>
        </view>
        <text class="task-title">{{ task.project_title || task.title || '论文阶段任务' }}</text>
        <view class="deadline-row">
          <text class="muted">截止：{{ formatDeadline(task) }}{{ countdownSuffix(task) }}</text>
          <text v-if="task.is_late" class="late-tag">已逾期</text>
        </view>
        <button class="secondary-button" :loading="actingId === task.id" @tap="primaryAction(task)">{{ primaryLabel(task) }}</button>
        <button
          v-if="canShowResubmit(task)"
          class="ghost-button"
          @tap="goResubmit(task)"
        >重新预检提交</button>
        <button
          v-if="canShowDesktop(task)"
          class="ghost-button"
          @tap="copyDesktopHint"
        >继续写作请用电脑端</button>
      </view>
      <view v-if="historyTasks.length" class="history-toggle" @tap="showHistory = !showHistory">
        <text>{{ showHistory ? '收起历史阶段' : `查看历史阶段 · ${historyTasks.length}` }}</text>
      </view>
      <view v-if="showHistory" v-for="task in historyTasks" :key="task.id" class="panel task-card" :class="`state-${stateTone(task.state)}`">
        <view class="task-head">
          <text class="task-kicker">{{ task.cohort_name || '论文批次' }} · {{ task.stage_name || '阶段任务' }}</text>
          <text class="status" :class="`tone-${stateTone(task.state)}`">{{ stateLabel(task.state) }}</text>
        </view>
        <text class="task-title">{{ task.project_title || task.title || '论文阶段任务' }}</text>
        <button class="ghost-button" @tap="primaryAction(task)">{{ primaryLabel(task) }}</button>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { onShow, onShareAppMessage } from '@dcloudio/uni-app'
import { describeError, fetchNotifications, fetchTasks, type Task } from '../../api/request'
import { useSessionStore } from '../../stores/session'
import { ensureSessionReady } from '../../utils/session-guard'
import { deadlineCountdownLabel, formatDateTime, participantStateLabel, participantStateTone } from '../../utils/format'
import { effectiveDeadline, resubmitUrl, splitStudentTasks, studentPrimaryAction } from '../../utils/student-flow'
import { teacherQueue, teacherTaskAction, waitDurationLabel } from '../../utils/teacher-flow'
import { applySharePolicy, homeSharePayload } from '../../utils/privacy'
import { applyMessageTabBadge } from '../../utils/tab-badge'

const session = useSessionStore()
const tasks = ref<Task[]>([])
const loading = ref(false)
const errorMessage = ref('')
const actingId = ref('')
const showHistory = ref(false)
const isTeacher = computed(() => session.user?.role === 'teacher')
const greeting = computed(() => session.user?.display_name || session.user?.username || '你好')
const pageTitle = computed(() => (isTeacher.value ? '待我审核' : '我的任务'))
const roleLabel = computed(() => (isTeacher.value ? '指导教师' : '学生'))
const queue = computed(() => teacherQueue(tasks.value))
const pendingTasks = computed(() => queue.value.pending)
const recheckTasks = computed(() => queue.value.recheck)
const restTasks = computed(() => queue.value.rest)
const studentGroups = computed(() => splitStudentTasks(tasks.value))
const currentTasks = computed(() => studentGroups.value.current)
const historyTasks = computed(() => studentGroups.value.history)

onShareAppMessage(() => homeSharePayload())

onShow(() => {
  applySharePolicy('/pages/tasks/index')
  if (!ensureSessionReady()) return
  uni.setTabBarItem({ index: 0, text: isTeacher.value ? '待审核' : '任务' })
  void loadTasks()
  void refreshMessageBadge()
})

async function refreshMessageBadge() {
  try {
    const result = await fetchNotifications()
    applyMessageTabBadge(result.unread)
  } catch {
    // 角标失败不阻断任务列表
  }
}

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

function stateLabel(state: unknown) {
  return participantStateLabel(state)
}
function stateTone(state: unknown) {
  return participantStateTone(state)
}

function formatDeadline(task: Task) {
  const value = effectiveDeadline(task)
  if (!value) return '以电脑端任务要求为准'
  return formatDateTime(value)
}

function countdownSuffix(task: Task) {
  const label = deadlineCountdownLabel(effectiveDeadline(task))
  return label ? ` · ${label}` : ''
}

function waitLabel(task: { current_submission?: { submitted_at?: string } | null }) {
  return waitDurationLabel(task.current_submission?.submitted_at)
}

function primaryLabel(task: Task) {
  if (isTeacher.value) return teacherTaskAction(task).label
  return studentPrimaryAction(task, session.user?.role).label
}

function canShowResubmit(task: Task) {
  return !isTeacher.value && String(task.state) === 'needs_revision'
}

function canShowDesktop(task: Task) {
  return !isTeacher.value && ['not_started', 'in_progress', 'needs_revision'].includes(String(task.state || ''))
}

function openTask(task: Task) {
  void primaryAction(task)
}

async function primaryAction(task: Task) {
  const action = isTeacher.value ? teacherTaskAction(task) : studentPrimaryAction(task, session.user?.role)
  if (action.url) {
    uni.navigateTo({ url: action.url })
    return
  }
  uni.showToast({ title: isTeacher.value ? '该任务暂无可审阅的提交' : '该操作请使用电脑端完成', icon: 'none' })
}

function goResubmit(task: Task) {
  uni.navigateTo({ url: resubmitUrl(task.id) })
}

function copyDesktopHint() {
  const hint = '请在电脑浏览器打开论文卫士Pro 完成长文写作与修复'
  uni.setClipboardData({
    data: hint,
    success: () => uni.showToast({ title: '已复制电脑端提示', icon: 'none' }),
    fail: () => uni.showToast({ title: hint, icon: 'none' }),
  })
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 48rpx 36rpx 120rpx; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; }
.eyebrow { display: block; color: #6b7280; font-size: 24rpx; }
.title { display: block; margin-top: 8rpx; color: #111827; font-size: 50rpx; font-weight: 800; }
.role-mark { padding: 12rpx 18rpx; border-radius: 999rpx; background: #e5efff; color: #004ac6; font-size: 23rpx; }
.section-label { display: block; margin: 28rpx 8rpx 0; color: #434655; font-size: 24rpx; font-weight: 700; }
.panel { margin-top: 34rpx; padding: 34rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 22rpx; box-shadow: 0 10rpx 30rpx rgba(17, 24, 39, .05); }
.state-panel { display: flex; flex-direction: column; min-height: 180rpx; justify-content: center; }
.empty-title { display: block; color: #111827; font-size: 31rpx; font-weight: 750; }
.muted { display: block; margin-top: 12rpx; color: #6b7280; font-size: 24rpx; line-height: 1.5; }
.task-card { border-left: 8rpx solid #004ac6; }
.task-card.state-orange { border-left-color: #d97706; }
.task-card.state-green { border-left-color: #16a34a; }
.task-card.state-gray { border-left-color: #9ca3af; }
.task-card.state-red { border-left-color: #ba1a1a; }
.task-head { display: flex; justify-content: space-between; align-items: center; }
.task-kicker { color: #6b7280; font-size: 23rpx; }
.status { font-size: 23rpx; font-weight: 700; }
.tone-blue { color: #004ac6; }
.tone-orange { color: #d97706; }
.tone-green { color: #16a34a; }
.tone-gray { color: #6b7280; }
.tone-red { color: #ba1a1a; }
.task-title { display: block; margin-top: 20rpx; color: #111827; font-size: 34rpx; font-weight: 750; }
.deadline-row { display: flex; align-items: center; gap: 16rpx; }
.deadline-row .muted { margin-top: 12rpx; }
.late-tag { margin-top: 12rpx; padding: 4rpx 14rpx; border-radius: 999rpx; background: #fef3c7; color: #b45309; font-size: 21rpx; font-weight: 700; }
.secondary-button { margin: 28rpx 0 0; height: 78rpx; line-height: 78rpx; border: 1rpx solid #004ac6; border-radius: 12rpx; background: #fff; color: #004ac6; font-size: 26rpx; }
.ghost-button { margin: 16rpx 0 0; height: 72rpx; line-height: 72rpx; border: 1rpx solid #e5e7eb; border-radius: 12rpx; background: #fff; color: #434655; font-size: 24rpx; }
.history-toggle { margin: 28rpx 8rpx 0; color: #004ac6; font-size: 24rpx; font-weight: 700; }
.error-text { color: #ba1a1a; font-size: 25rpx; line-height: 1.5; }
</style>
