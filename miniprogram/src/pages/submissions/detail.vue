<template>
  <view class="page">
    <view v-if="loading" class="panel state-panel"><text>正在加载提交详情…</text></view>
    <view v-else-if="errorMessage" class="panel state-panel">
      <text class="error-text">{{ errorMessage }}</text>
      <button class="secondary-button" @tap="loadDetail">重新加载</button>
    </view>

    <template v-else-if="detail">
      <view class="panel head-card">
        <view class="head-row">
          <text class="number">{{ detail.submission.submission_number }}</text>
          <text class="state" :class="`tone-${stateTone}`">{{ stateLabel }}</text>
        </view>
        <text class="meta">{{ stageLabel }} · 第 {{ detail.submission.review_cycle }} 轮 · 提交于 {{ formatTime(detail.submission.submitted_at) }}</text>
        <text class="history-link" @tap="goHistory">查看提交历史</text>
        <view class="hash-row">
          <text class="hash">版本快照 {{ snapshotHash }}</text>
          <text class="hash-note">只读 · 提交后不可变</text>
        </view>
      </view>

      <view v-if="returnReason" class="panel return-panel">
        <text class="panel-title">退回理由</text>
        <text class="return-reason">{{ returnReason.reason }}</text>
        <text class="return-meta">{{ formatTime(returnReason.createdAt) }}{{ returnReason.revisionDeadlineAt ? ` · 修订截止 ${formatTime(returnReason.revisionDeadlineAt)}` : '' }}</text>
        <text class="deadline-hint">长文修改请使用电脑端</text>
      </view>

      <view v-else-if="deadlineText" class="deadline-bar" :class="{ late: isLate }">
        <text>{{ isLate ? '已逾期' : '修订截止' }} · {{ deadlineText }}{{ deadlineCountdown ? ` · ${deadlineCountdown}` : '' }}</text>
        <text class="deadline-hint">长文修改请使用电脑端</text>
      </view>

      <view class="panel">
        <text class="panel-title">检查与批注摘要</text>
        <view class="summary-row">
          <text class="summary-name">自动格式检查</text>
          <text class="gate-badge" :class="checkPassed ? 'pass' : 'block'">{{ checkPassed ? '已通过' : '未通过' }}</text>
        </view>
        <view class="summary-row">
          <text class="summary-name">教师批注</text>
          <text class="gate-badge" :class="blockingOpen ? 'block' : 'pass'">
            {{ detail.comments.length }} 条{{ blockingOpen ? `（${blockingOpen} 条阻断待处理）` : '' }}
          </text>
        </view>
      </view>

      <view v-if="materialItems.length" class="panel">
        <text class="panel-title">必交材料</text>
        <view v-for="item in materialItems" :key="item.label" class="summary-row">
          <text class="summary-name">{{ item.label }}</text>
          <text class="gate-badge" :class="item.passed ? 'pass' : 'block'">{{ item.passed ? '已绑定' : '未绑定' }}</text>
        </view>
        <text class="muted">材料绑定请使用电脑端过程材料；此处只展示提交时门禁结果。</text>
      </view>

      <view class="panel">
        <text class="panel-title">流转记录</text>
        <view v-if="timelineItems.length === 0" class="muted">暂无时间线</view>
        <view v-else class="timeline">
          <view v-for="(item, index) in timelineItems" :key="`${item.title}-${index}`" class="timeline-item">
            <view class="dot" :class="`tone-bg-${item.tone}`" />
            <view class="timeline-body">
              <text class="timeline-title">{{ item.title }}</text>
              <text class="timeline-time">{{ formatTime(item.time) }}</text>
            </view>
          </view>
        </view>
      </view>

      <view class="bottom-bar">
        <button class="bar-button primary" @tap="goComments">查看审阅意见</button>
        <button v-if="canWithdrawNow" class="bar-button ghost" :loading="withdrawing" @tap="confirmWithdraw">撤回提交</button>
        <button v-else-if="canResubmitNow" class="bar-button ghost" @tap="goResubmit">重新预检提交</button>
        <button v-else class="bar-button ghost" @tap="goHistory">提交历史</button>
      </view>
    </template>
  </view>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { describeError } from '../../api/request'
import { applySharePolicy } from '../../utils/privacy'
import { fetchSubmission, withdrawSubmission, type SubmissionDetail } from '../../api/submissions'
import { useSessionStore } from '../../stores/session'
import { deadlineCountdownLabel, formatDateTime, isDeadlinePassed, shortHash, submissionStateLabel, submissionStateTone } from '../../utils/format'
import { canResubmit, canWithdrawSubmission, latestReturnReason, materialGateItems, resubmitUrl, submissionTimeline } from '../../utils/student-flow'

const session = useSessionStore()
const detail = ref<SubmissionDetail | null>(null)
const loading = ref(false)
const withdrawing = ref(false)
const errorMessage = ref('')

const stateLabel = computed(() => submissionStateLabel(detail.value?.submission.state))
const stateTone = computed(() => submissionStateTone(detail.value?.submission.state))
const stageLabel = computed(() => String(detail.value?.stage?.name || '阶段任务'))
const checkPassed = computed(() => {
  const candidate = detail.value?.candidate
  if (!candidate) return false
  return candidate.check_status === 'succeeded' && candidate.status === 'ready'
})
const blockingOpen = computed(() => (detail.value?.comments || []).filter((item) => item.severity === 'blocking' && item.status !== 'resolved').length)
const deadlineRaw = computed(() => {
  const participant = detail.value?.participant || {}
  return participant.revision_deadline_at || participant.effective_deadline_at || ''
})
const deadlineText = computed(() => formatDateTime(deadlineRaw.value))
const deadlineCountdown = computed(() => deadlineCountdownLabel(deadlineRaw.value))
const isLate = computed(() => Boolean(detail.value?.participant?.is_late) || isDeadlinePassed(deadlineRaw.value))
const snapshotHash = computed(() => shortHash(detail.value?.version?.content_hash || detail.value?.candidate?.content_hash))
const canResubmitNow = computed(() => canResubmit(detail.value?.participant?.state))
const canWithdrawNow = computed(() => canWithdrawSubmission(detail.value, session.user?.role || 'student'))
const returnReason = computed(() => latestReturnReason(detail.value))
const materialItems = computed(() => materialGateItems(detail.value?.candidate?.gates || []))
const timelineItems = computed(() => submissionTimeline(detail.value))
const submissionId = ref('')

onLoad((params) => {
  applySharePolicy('/pages/submissions/detail')
  submissionId.value = (params as Record<string, string>)?.id || ''
  if (!submissionId.value) {
    errorMessage.value = '缺少提交参数，请从任务列表进入'
    return
  }
  void loadDetail()
})

async function loadDetail() {
  loading.value = true
  errorMessage.value = ''
  try {
    detail.value = await fetchSubmission(submissionId.value)
  } catch (error) {
    errorMessage.value = describeError(error)
  } finally {
    loading.value = false
  }
}

function goComments() {
  uni.navigateTo({ url: `/pages/submissions/comments?id=${submissionId.value}` })
}
function goHistory() {
  const projectId = encodeURIComponent(String(detail.value?.participant?.project_id || ''))
  uni.navigateTo({ url: `/pages/submissions/history?project_id=${projectId}` })
}
function goResubmit() {
  const participantId = String(detail.value?.participant?.id || '')
  if (!participantId) {
    uni.showToast({ title: '缺少阶段任务参数', icon: 'none' })
    return
  }
  uni.navigateTo({ url: resubmitUrl(participantId) })
}

function confirmWithdraw() {
  if (withdrawing.value) return
  uni.showModal({
    title: '撤回提交',
    content: '撤回后教师将无法继续审核该版本，需要重新预检后再提交。',
    confirmText: '确认撤回',
    success: (result) => {
      if (result.confirm) void doWithdraw()
    },
  })
}

async function doWithdraw() {
  withdrawing.value = true
  try {
    await withdrawSubmission(submissionId.value)
    uni.showToast({ title: '已撤回该提交', icon: 'none' })
    await loadDetail()
  } catch (error) {
    uni.showToast({ title: describeError(error), icon: 'none' })
    await loadDetail()
  } finally {
    withdrawing.value = false
  }
}
function formatTime(value: unknown) {
  return formatDateTime(value) || '—'
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 24rpx 36rpx 200rpx; }
.panel { margin-top: 24rpx; padding: 30rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 20rpx; }
.state-panel { display: flex; flex-direction: column; min-height: 200rpx; justify-content: center; }
.panel-title { display: block; color: #111827; font-size: 29rpx; font-weight: 750; }
.head-card { border-left: 8rpx solid #004ac6; }
.head-row { display: flex; align-items: center; justify-content: space-between; }
.number { color: #004ac6; font-size: 44rpx; font-weight: 800; }
.state { padding: 6rpx 18rpx; border-radius: 999rpx; font-size: 23rpx; font-weight: 700; }
.tone-blue { background: #e5efff; color: #004ac6; }
.tone-red { background: #fdeaea; color: #ba1a1a; }
.tone-green { background: #e8f7ee; color: #16a34a; }
.tone-gray { background: #f3f4f6; color: #6b7280; }
.tone-bg-blue { background: #004ac6; }
.tone-bg-red { background: #ba1a1a; }
.tone-bg-green { background: #16a34a; }
.tone-bg-gray { background: #9ca3af; }
.meta { display: block; margin-top: 14rpx; color: #6b7280; font-size: 24rpx; }
.history-link { display: block; margin-top: 12rpx; color: #004ac6; font-size: 23rpx; font-weight: 700; }
.hash-row { display: flex; align-items: center; justify-content: space-between; margin-top: 20rpx; padding: 14rpx 18rpx; border-radius: 10rpx; background: #f5f7fb; }
.hash { color: #434655; font-size: 21rpx; }
.hash-note { color: #9ca3af; font-size: 20rpx; }
.deadline-bar { display: flex; align-items: center; justify-content: space-between; margin-top: 24rpx; padding: 20rpx 24rpx; border-radius: 14rpx; background: #fef3c7; color: #b45309; font-size: 25rpx; font-weight: 700; }
.deadline-bar.late { background: #fdeaea; color: #ba1a1a; }
.deadline-hint { color: inherit; font-size: 21rpx; font-weight: 400; }
.return-panel { border-left: 8rpx solid #ba1a1a; background: #fff8f8; }
.return-reason { display: block; margin-top: 16rpx; color: #111827; font-size: 28rpx; line-height: 1.55; }
.return-meta { display: block; margin-top: 12rpx; color: #6b7280; font-size: 22rpx; }
.muted { display: block; margin-top: 14rpx; color: #6b7280; font-size: 22rpx; line-height: 1.5; }
.summary-row { display: flex; justify-content: space-between; align-items: center; margin-top: 22rpx; }
.summary-name { color: #111827; font-size: 26rpx; }
.gate-badge { padding: 6rpx 18rpx; border-radius: 999rpx; font-size: 22rpx; font-weight: 700; }
.gate-badge.pass { background: #e8f7ee; color: #16a34a; }
.gate-badge.block { background: #fdeaea; color: #ba1a1a; }
.timeline { margin-top: 10rpx; }
.timeline-item { display: flex; gap: 20rpx; padding: 16rpx 0; }
.dot { box-sizing: border-box; width: 20rpx; height: 20rpx; margin-top: 8rpx; border-radius: 50%; }
.timeline-body { display: flex; flex-direction: column; }
.timeline-title { color: #111827; font-size: 26rpx; }
.timeline-time { margin-top: 6rpx; color: #9ca3af; font-size: 22rpx; }
.bottom-bar { position: fixed; left: 0; right: 0; bottom: 0; box-sizing: border-box; display: flex; gap: 20rpx; padding: 20rpx 36rpx calc(20rpx + env(safe-area-inset-bottom)); background: #fff; border-top: 1rpx solid #e5e7eb; }
.bar-button { flex: 1; height: 84rpx; line-height: 84rpx; border-radius: 14rpx; font-size: 28rpx; font-weight: 700; }
.bar-button.primary { background: #004ac6; color: #fff; }
.bar-button.ghost { border: 1rpx solid #004ac6; background: #fff; color: #004ac6; }
.error-text { color: #ba1a1a; font-size: 25rpx; line-height: 1.5; }
.secondary-button { margin: 24rpx 0 0; height: 76rpx; line-height: 76rpx; border: 1rpx solid #004ac6; border-radius: 12rpx; background: #fff; color: #004ac6; font-size: 25rpx; }
</style>
