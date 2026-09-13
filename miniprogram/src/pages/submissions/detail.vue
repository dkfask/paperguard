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
        <view class="hash-row">
          <text class="hash">版本快照 {{ shortHash }}</text>
          <text class="hash-note">只读 · 提交后不可变</text>
        </view>
      </view>

      <view v-if="deadlineText" class="deadline-bar" :class="{ late: isLate }">
        <text>{{ isLate ? '已逾期' : '修订截止' }} · {{ deadlineText }}</text>
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

      <view class="panel">
        <text class="panel-title">流转记录</text>
        <view class="timeline">
          <view class="timeline-item">
            <view class="dot" :class="`tone-bg-${stateTone}`" />
            <view class="timeline-body">
              <text class="timeline-title">{{ stateLabel }}（{{ detail.submission.submission_number }}）</text>
              <text class="timeline-time">{{ formatTime(detail.submission.submitted_at) }}</text>
            </view>
          </view>
          <view class="timeline-item">
            <view class="dot tone-bg-blue" />
            <view class="timeline-body">
              <text class="timeline-title">正式提交，冻结版本快照</text>
              <text class="timeline-time">门禁 {{ checkPassed ? '全部通过' : '以提交时结果为准' }}</text>
            </view>
          </view>
        </view>
      </view>

      <view class="bottom-bar">
        <button class="bar-button primary" @tap="goComments">查看审阅意见</button>
        <button class="bar-button ghost" @tap="goHistory">提交历史</button>
      </view>
    </template>
  </view>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { describeError } from '../../api/request'
import { fetchSubmission, type SubmissionDetail } from '../../api/submissions'

const detail = ref<SubmissionDetail | null>(null)
const loading = ref(false)
const errorMessage = ref('')

const stateLabel = computed(() => {
  const state = String(detail.value?.submission.state || '')
  return ({ submitted: '待审核', returned: '已退回', approved: '已通过', withdrawn: '已撤回' } as Record<string, string>)[state] || state
})
const stateTone = computed(() => {
  const state = String(detail.value?.submission.state || '')
  return ({ submitted: 'blue', returned: 'red', approved: 'green', withdrawn: 'gray' } as Record<string, string>)[state] || 'blue'
})
const stageLabel = computed(() => String(detail.value?.stage?.name || '阶段任务'))
const checkPassed = computed(() => detail.value?.candidate?.check_status === 'succeeded' && (detail.value?.candidate?.status === 'ready' || detail.value?.candidate === null))
const blockingOpen = computed(() => (detail.value?.comments || []).filter((item) => item.severity === 'blocking' && item.status !== 'resolved').length)
const deadlineText = computed(() => {
  const value = detail.value?.participant?.revision_deadline_at || (stateTone.value === 'red' ? detail.value?.participant?.revision_deadline_at : '')
  return value ? String(value).replace('T', ' ').slice(0, 16) : ''
})
const isLate = computed(() => deadlineText.value.includes('已'))

onLoad((params) => {
  submissionId.value = (params as Record<string, string>)?.id || ''
  if (!submissionId.value) {
    errorMessage.value = '缺少提交参数，请从任务列表进入'
    return
  }
  void loadDetail()
})

const submissionId = ref('')

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
  uni.navigateTo({ url: '/pages/submissions/history' })
}
function formatTime(value: unknown) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '—'
}
function shortHash(hash: unknown) {
  const text = String(hash || '')
  return text.length > 12 ? `${text.slice(0, 6)}…${text.slice(-4)}` : text || '—'
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
.hash-row { display: flex; align-items: center; justify-content: space-between; margin-top: 20rpx; padding: 14rpx 18rpx; border-radius: 10rpx; background: #f5f7fb; }
.hash { color: #434655; font-size: 21rpx; }
.hash-note { color: #9ca3af; font-size: 20rpx; }
.deadline-bar { display: flex; align-items: center; justify-content: space-between; margin-top: 24rpx; padding: 20rpx 24rpx; border-radius: 14rpx; background: #fef3c7; color: #b45309; font-size: 25rpx; font-weight: 700; }
.deadline-bar.late { background: #fdeaea; color: #ba1a1a; }
.deadline-hint { color: inherit; font-size: 21rpx; font-weight: 400; }
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
