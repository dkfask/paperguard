<template>
  <view class="page">
    <view v-if="loading" class="panel state-panel"><text>正在加载审阅意见…</text></view>
    <view v-else-if="errorMessage" class="panel state-panel">
      <text class="error-text">{{ errorMessage }}</text>
      <button class="secondary-button" @tap="loadDetail">重新加载</button>
    </view>

    <template v-else-if="detail">
      <view class="panel context-card">
        <text class="context-title">{{ detail.submission.submission_number }} · {{ stageLabel }}</text>
        <view class="pills">
          <text class="pill tone-red">阻断 {{ blockingCount }}</text>
          <text class="pill tone-orange">待我回复 {{ pendingReply }}</text>
          <text class="pill tone-green">已解决 {{ resolvedCount }}</text>
        </view>
      </view>

      <view v-if="detail.comments.length === 0" class="panel state-panel">
        <text class="empty-title">本轮暂无批注</text>
        <text class="muted">教师批注会绑定原文段落并在此处显示。</text>
      </view>

      <view
        v-for="comment in sortedComments"
        :key="comment.id"
        class="panel comment-card"
        :class="`border-${commentTone(comment)}`"
      >
        <view class="card-head">
          <text class="card-kicker" :class="`text-${commentTone(comment)}`">{{ severityLabel(comment) }}</text>
          <text class="status" :class="`text-${statusTone(comment)}`">{{ statusLabel(comment) }}</text>
        </view>
        <text class="anchor">{{ anchorLabel(comment) }}</text>
        <view v-if="comment.quoted_text" class="quote-block">
          <text class="quote-text">“{{ comment.quoted_text }}”</text>
        </view>
        <text class="comment-body">{{ comment.body }}</text>

        <view v-for="response in comment.responses" :key="response.id" class="reply-bubble">
          <text class="reply-label">我的回复</text>
          <text class="reply-body">{{ response.body }}</text>
          <text class="reply-time">{{ formatTime(response.created_at) }}</text>
        </view>

        <view v-if="replyTarget !== comment.id && comment.status !== 'resolved'" class="reply-entry">
          <text class="reply-link" @tap="replyTarget = comment.id">回复批注 →</text>
        </view>
        <view v-if="replyTarget === comment.id" class="reply-editor">
          <textarea v-model="replyBody" class="reply-area" :maxlength="300" placeholder="说明修改情况；重新提交时会随候选版本一并存档" />
          <view class="editor-actions">
            <button class="mini-button ghost" @tap="cancelReply">取消</button>
            <button class="mini-button primary" :loading="replying" @tap="submitReply(comment)">提交回复并标记待复核</button>
          </view>
        </view>
        <text v-if="comment.status === 'resolved'" class="resolved-note">教师已确认解决，本轮无需再处理。</text>
      </view>
      <button v-if="canResubmitNow" class="bar-button primary" @tap="goResubmit">回复完成后重新预检提交</button>
    </template>
  </view>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { describeError } from '../../api/request'
import { applySharePolicy } from '../../utils/privacy'
import { fetchSubmission, respondComment, type Comment, type SubmissionDetail } from '../../api/submissions'
import { formatDateTime } from '../../utils/format'
import { canResubmit, resubmitUrl } from '../../utils/student-flow'

const detail = ref<SubmissionDetail | null>(null)
const loading = ref(false)
const errorMessage = ref('')
const replyTarget = ref('')
const replyBody = ref('')
const replying = ref(false)

const sortedComments = computed(() => {
  const items = [...(detail.value?.comments || [])]
  const weight = (comment: Comment) => (comment.severity === 'blocking' ? 0 : 1) * 10 + (comment.status === 'resolved' ? 1 : 0)
  return items.sort((a, b) => weight(a) - weight(b))
})
const blockingCount = computed(() => (detail.value?.comments || []).filter((item) => item.severity === 'blocking' && item.status !== 'resolved').length)
const pendingReply = computed(() => (detail.value?.comments || []).filter((item) => item.status === 'open').length)
const resolvedCount = computed(() => (detail.value?.comments || []).filter((item) => item.status === 'resolved').length)
const stageLabel = computed(() => String(detail.value?.stage?.name || ''))
const canResubmitNow = computed(() => canResubmit(detail.value?.participant?.state))
const submissionId = ref('')

onLoad((params) => {
  applySharePolicy('/pages/submissions/comments')
  submissionId.value = (params as Record<string, string>)?.id || ''
  if (!submissionId.value) {
    errorMessage.value = '缺少提交参数，请从任务或提交详情进入'
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

async function submitReply(comment: Comment) {
  if (!replyBody.value.trim()) {
    uni.showToast({ title: '请先填写回复内容', icon: 'none' })
    return
  }
  replying.value = true
  try {
    await respondComment(comment.id, replyBody.value.trim())
    replyBody.value = ''
    replyTarget.value = ''
    uni.showToast({ title: '已标记「已修改待复核」', icon: 'none' })
    await loadDetail()
  } catch (error) {
    uni.showToast({ title: describeError(error), icon: 'none' })
  } finally {
    replying.value = false
  }
}

function cancelReply() {
  replyTarget.value = ''
  replyBody.value = ''
}

function severityLabel(comment: Comment) {
  const anchor = comment.block_id ? ` · ${comment.block_id}` : ''
  return `${comment.severity === 'blocking' ? '阻断' : '建议'}${anchor}`
}
function statusLabel(comment: Comment) {
  return ({ open: '待处理', addressed: '已回复 · 待教师确认', resolved: '已解决' } as Record<string, string>)[comment.status] || comment.status
}
function commentTone(comment: Comment) {
  if (comment.status === 'resolved') return 'green'
  return comment.severity === 'blocking' ? 'red' : 'blue'
}
function statusTone(comment: Comment) {
  if (comment.status === 'resolved') return 'green'
  if (comment.status === 'addressed') return 'orange'
  return 'gray'
}
function anchorLabel(comment: Comment) {
  return comment.block_id ? `定位：${comment.block_id}` : '整段批注'
}
function formatTime(value: unknown) {
  return formatDateTime(value)
}

function goResubmit() {
  const participantId = String(detail.value?.participant?.id || '')
  if (!participantId) {
    uni.showToast({ title: '缺少阶段任务参数', icon: 'none' })
    return
  }
  uni.navigateTo({ url: resubmitUrl(participantId) })
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 24rpx 36rpx 80rpx; }
.panel { margin-top: 24rpx; padding: 30rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 20rpx; }
.state-panel { display: flex; flex-direction: column; min-height: 200rpx; justify-content: center; }
.empty-title { display: block; color: #111827; font-size: 30rpx; font-weight: 750; }
.muted { display: block; margin-top: 12rpx; color: #6b7280; font-size: 24rpx; line-height: 1.55; }
.context-title { display: block; color: #111827; font-size: 29rpx; font-weight: 750; }
.pills { display: flex; gap: 16rpx; margin-top: 20rpx; }
.pill { padding: 8rpx 20rpx; border-radius: 999rpx; font-size: 22rpx; font-weight: 700; }
.tone-red { color: #ba1a1a; }
.pill.tone-red { background: #fdeaea; }
.tone-orange { color: #b45309; }
.pill.tone-orange { background: #fef3c7; }
.tone-green { color: #16a34a; }
.pill.tone-green { background: #e8f7ee; }
.tone-blue { color: #004ac6; }
.tone-gray { color: #6b7280; }
.comment-card { border-left: 8rpx solid #004ac6; }
.comment-card.border-red { border-left-color: #ba1a1a; }
.comment-card.border-green { border-left-color: #16a34a; }
.card-head { display: flex; align-items: center; justify-content: space-between; }
.card-kicker { font-size: 26rpx; font-weight: 750; }
.status { font-size: 22rpx; font-weight: 700; }
.text-orange { color: #b45309; }
.anchor { display: block; margin-top: 12rpx; color: #9ca3af; font-size: 21rpx; }
.quote-block { margin-top: 16rpx; padding: 18rpx 20rpx; border-left: 4rpx solid #eab308; border-radius: 10rpx; background: #fefce8; }
.quote-text { color: #434655; font-size: 24rpx; line-height: 1.6; }
.comment-body { display: block; margin-top: 18rpx; color: #111827; font-size: 27rpx; line-height: 1.6; }
.reply-bubble { margin-top: 18rpx; padding: 18rpx 20rpx; border-radius: 14rpx; background: #f5f7fb; }
.reply-label { display: block; color: #004ac6; font-size: 21rpx; font-weight: 700; }
.reply-body { display: block; margin-top: 8rpx; color: #111827; font-size: 25rpx; line-height: 1.55; }
.reply-time { display: block; margin-top: 8rpx; color: #9ca3af; font-size: 20rpx; }
.reply-entry { margin-top: 18rpx; }
.reply-link { color: #004ac6; font-size: 24rpx; font-weight: 700; }
.reply-area { box-sizing: border-box; width: 100%; min-height: 130rpx; margin-top: 18rpx; padding: 20rpx; background: #f5f7fb; border: 1rpx solid #e5e7eb; border-radius: 12rpx; color: #111827; font-size: 25rpx; }
.editor-actions { display: flex; justify-content: flex-end; gap: 18rpx; margin-top: 16rpx; }
.mini-button { box-sizing: border-box; height: 68rpx; line-height: 68rpx; margin: 0; padding: 0 28rpx; border-radius: 12rpx; font-size: 24rpx; font-weight: 700; }
.mini-button.primary { background: #004ac6; color: #fff; }
.mini-button.ghost { border: 1rpx solid #cbd5e1; background: #fff; color: #434655; }
.resolved-note { display: block; margin-top: 18rpx; color: #16a34a; font-size: 23rpx; }
.bar-button { height: 84rpx; line-height: 84rpx; margin-top: 28rpx; border-radius: 14rpx; font-size: 28rpx; font-weight: 700; }
.bar-button.primary { background: #004ac6; color: #fff; }
.error-text { color: #ba1a1a; font-size: 25rpx; line-height: 1.5; }
.secondary-button { margin: 24rpx 0 0; height: 76rpx; line-height: 76rpx; border: 1rpx solid #004ac6; border-radius: 12rpx; background: #fff; color: #004ac6; font-size: 25rpx; }
</style>
