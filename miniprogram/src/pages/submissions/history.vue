<template>
  <view class="page">
    <view v-if="loading" class="panel state-panel"><text>正在加载提交历史…</text></view>
    <view v-else-if="errorMessage" class="panel state-panel">
      <text class="error-text">{{ errorMessage }}</text>
      <button class="secondary-button" @tap="loadHistory">重新加载</button>
    </view>
    <view v-else-if="submissions.length === 0" class="panel state-panel">
      <text class="empty-title">还没有提交记录</text>
      <text class="muted">完成首次正式提交后，修订链会显示在这里。</text>
    </view>

    <template v-else>
      <text class="page-hint">共 {{ submissions.length }} 次提交 · 每次提交均为不可变版本快照</text>
      <view
        v-for="(item, index) in submissions"
        :key="item.id"
        class="panel chain-card"
        :class="`border-${stateTone(item)}`"
      >
        <view class="card-head">
          <text class="number">{{ item.submission_number }}</text>
          <text class="state" :class="`tone-${stateTone(item)}`">{{ stateLabel(item) }}</text>
        </view>
        <text class="meta">第 {{ item.review_cycle }} 轮 · {{ formatTime(item.submitted_at) }}</text>
        <text v-if="itemNote(item).note" class="note">修改说明：{{ itemNote(item).note }}</text>
        <text v-if="itemNote(item).openBlocking > 0" class="blocking">上轮未解决阻断 {{ itemNote(item).openBlocking }} 条</text>
        <view class="actions">
          <text class="link" @tap="goDetail(item)">查看详情 →</text>
          <text
            v-if="index < submissions.length - 1 && item.project_version_id && submissions[index + 1].project_version_id"
            class="link"
            @tap="goDiff(item, submissions[index + 1])"
          >与 {{ submissions[index + 1].submission_number }} 差异 →</text>
        </view>
      </view>

      <view v-if="diffSummary" class="panel diff-card">
        <text class="panel-title">章节级差异（{{ diffLabel }}）</text>
        <view v-for="(line, idx) in diffSummary" :key="idx" class="diff-row">
          <text class="diff-badge" :class="line.tone">{{ line.mark }}</text>
          <text class="diff-text">{{ line.text }}</text>
        </view>
        <text class="muted">差异基于不可变版本快照，历史不会被覆盖。</text>
      </view>
    </template>
  </view>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { describeError } from '../../api/request'
import { compareVersions, fetchSubmissions, type Submission, type VersionDiff } from '../../api/submissions'
import { formatDateTime, submissionStateLabel, submissionStateTone } from '../../utils/format'
import { flattenVersionDiff, historyItemNote } from '../../utils/student-flow'
import { applySharePolicy } from '../../utils/privacy'

const submissions = ref<Submission[]>([])
const loading = ref(false)
const errorMessage = ref('')
const projectId = ref('')
const diffSummary = ref<Array<{ mark: string; text: string; tone: string }> | null>(null)
const diffLabel = ref('')

onLoad((params) => {
  applySharePolicy('/pages/submissions/history')
  projectId.value = (params as Record<string, string>)?.project_id || ''
  void loadHistory()
})

async function loadHistory() {
  loading.value = true
  errorMessage.value = ''
  try {
    const items = await fetchSubmissions()
    const scoped = projectId.value
      ? items.filter((item) => String(item.project_id || '') === projectId.value)
      : items
    submissions.value = [...scoped].sort((a, b) => String(b.submitted_at || '').localeCompare(String(a.submitted_at || '')))
  } catch (error) {
    errorMessage.value = describeError(error)
  } finally {
    loading.value = false
  }
}

function stateLabel(item: Submission) {
  return submissionStateLabel(item.state)
}
function stateTone(item: Submission) {
  return submissionStateTone(item.state)
}
function formatTime(value: unknown) {
  return formatDateTime(value) || '—'
}
function itemNote(item: Submission) {
  return historyItemNote(item)
}

async function goDiff(current: Submission, prior: Submission) {
  uni.showLoading({ title: '正在比较…' })
  try {
    if (!projectId.value) {
      const { fetchSubmission } = await import('../../api/submissions')
      const detail = await fetchSubmission(current.id)
      projectId.value = String((detail.participant as Record<string, unknown>)?.project_id || '')
    }
    const diff = await compareVersions(projectId.value, prior.project_version_id as string, current.project_version_id as string)
    renderDiff(diff, prior, current)
  } catch (error) {
    uni.hideLoading()
    uni.showToast({ title: describeError(error), icon: 'none' })
  }
}

function renderDiff(diff: VersionDiff, prior: Submission, current: Submission) {
  diffSummary.value = flattenVersionDiff(diff)
  diffLabel.value = `${prior.submission_number} → ${current.submission_number}`
  uni.hideLoading()
}

function goDetail(item: Submission) {
  uni.navigateTo({ url: `/pages/submissions/detail?id=${item.id}` })
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 24rpx 36rpx 80rpx; }
.page-hint { display: block; margin: 10rpx 6rpx 0; color: #9ca3af; font-size: 22rpx; }
.panel { margin-top: 24rpx; padding: 30rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 20rpx; }
.state-panel { display: flex; flex-direction: column; min-height: 200rpx; justify-content: center; }
.empty-title { display: block; color: #111827; font-size: 30rpx; font-weight: 750; }
.muted { display: block; margin-top: 12rpx; color: #6b7280; font-size: 22rpx; }
.chain-card { border-left: 8rpx solid #004ac6; }
.chain-card.border-red { border-left-color: #ba1a1a; }
.chain-card.border-green { border-left-color: #16a34a; }
.chain-card.border-gray { border-left-color: #9ca3af; }
.card-head { display: flex; align-items: center; justify-content: space-between; }
.number { color: #004ac6; font-size: 34rpx; font-weight: 800; }
.state { padding: 6rpx 18rpx; border-radius: 999rpx; font-size: 22rpx; font-weight: 700; }
.tone-blue { background: #e5efff; color: #004ac6; }
.tone-red { background: #fdeaea; color: #ba1a1a; }
.tone-green { background: #e8f7ee; color: #16a34a; }
.tone-gray { background: #f3f4f6; color: #6b7280; }
.meta { display: block; margin-top: 12rpx; color: #6b7280; font-size: 23rpx; }
.note { display: block; margin-top: 12rpx; color: #111827; font-size: 24rpx; line-height: 1.5; }
.blocking { display: block; margin-top: 8rpx; color: #ba1a1a; font-size: 22rpx; font-weight: 700; }
.actions { display: flex; gap: 34rpx; margin-top: 20rpx; }
.link { color: #004ac6; font-size: 24rpx; font-weight: 700; }
.panel-title { display: block; color: #111827; font-size: 29rpx; font-weight: 750; }
.diff-row { display: flex; align-items: flex-start; gap: 16rpx; margin-top: 18rpx; }
.diff-badge { box-sizing: border-box; width: 40rpx; height: 40rpx; border-radius: 8rpx; color: #fff; font-size: 26rpx; font-weight: 800; text-align: center; line-height: 40rpx; }
.diff-badge.green { background: #16a34a; }
.diff-badge.red { background: #ba1a1a; }
.diff-badge.orange { background: #d97706; }
.diff-badge.gray { background: #9ca3af; }
.diff-text { flex: 1; color: #111827; font-size: 25rpx; line-height: 40rpx; }
.error-text { color: #ba1a1a; font-size: 25rpx; line-height: 1.5; }
.secondary-button { margin: 24rpx 0 0; height: 76rpx; line-height: 76rpx; border: 1rpx solid #004ac6; border-radius: 12rpx; background: #fff; color: #004ac6; font-size: 25rpx; }
</style>
