<template>
  <view class="page">
    <view v-if="loading" class="panel state-panel"><text>正在加载提交…</text></view>
    <view v-else-if="errorMessage" class="panel state-panel">
      <text class="error-text">{{ errorMessage }}</text>
      <button class="secondary-button" @tap="loadAll">重新加载</button>
    </view>

    <template v-else-if="detail">
      <view class="panel head-card">
        <view class="head-row">
          <text class="number">{{ detail.submission.submission_number }}</text>
          <text class="readonly-badge">🔒 只读快照</text>
        </view>
        <text class="meta">{{ studentLabel }} · {{ stageLabel }} · {{ formatTime(detail.submission.submitted_at) }}</text>
        <text class="meta-sub">学生草稿修改不影响此处版本</text>
      </view>

      <view class="tab-bar">
        <view class="tab" :class="{ active: tab === 'content' }" @tap="tab = 'content'">正文</view>
        <view class="tab" :class="{ active: tab === 'comments' }" @tap="tab = 'comments'">批注 ({{ detail.comments.length }})</view>
        <view class="tab" :class="{ active: tab === 'checks' }" @tap="tab = 'checks'">检查</view>
      </view>

      <!-- 正文 -->
      <view v-if="tab === 'content'" class="panel content-card">
        <view v-if="contentLoading" class="muted">正在加载快照正文…</view>
        <view v-else-if="sections.length === 0" class="muted">该版本无可读文本(或正文为空)。</view>
        <view v-for="section in sections" :key="section.key" class="section">
          <text class="section-title">{{ section.title || section.key }}</text>
          <view v-for="paragraph in section.paragraphs || []" :key="paragraph.id">
            <text v-if="paragraph.type === 'paragraph'" class="paragraph">{{ paragraph.text }}</text>
            <text v-else class="paragraph muted">[{{ blockTypeLabel(paragraph.type) }}]</text>
          </view>
        </view>
      </view>

      <!-- 批注 -->
      <view v-if="tab === 'comments'">
        <view class="panel add-comment">
          <text class="panel-title">新增批注</text>
          <view class="severity-row">
            <view class="severity-pill" :class="{ active: newSeverity === 'blocking' }" @tap="newSeverity = 'blocking'">必须处理的阻断项</view>
            <view class="severity-pill" :class="{ active: newSeverity === 'info' }" @tap="newSeverity = 'info'">普通建议</view>
          </view>
          <input v-model="newAnchor" class="field" placeholder="定位段落(如 3.2 实验分析 第2段)" maxlength="120" />
          <textarea v-model="newBody" class="area" :maxlength="2000" placeholder="批注内容:具体、可执行,绑定原文片段更易定位" />
          <button class="bar-button primary" :loading="adding" @tap="submitComment">添加批注</button>
        </view>

        <view v-for="comment in detail.comments" :key="comment.id" class="panel comment-card" :class="`border-${commentTone(comment)}`">
          <view class="card-head">
            <text class="card-kicker" :class="`text-${commentTone(comment)}`">{{ severityLabel(comment) }}</text>
            <text class="status" :class="`text-${statusTone(comment)}`">{{ statusLabel(comment) }}</text>
          </view>
          <view v-if="comment.quoted_text" class="quote-block"><text class="quote-text">“{{ comment.quoted_text }}”</text></view>
          <text class="comment-body">{{ comment.body }}</text>
          <view v-for="response in comment.responses" :key="response.id" class="reply-bubble">
            <text class="reply-label">学生回复</text>
            <text class="reply-body">{{ response.body }}</text>
            <text class="reply-time">{{ formatTime(response.created_at) }}</text>
          </view>
          <view v-if="comment.status !== 'resolved'" class="comment-actions">
            <button class="mini-button primary" @tap="resolveComment(comment)">确认解决</button>
            <button class="mini-button ghost" @tap="reopenComment(comment)">重新打开</button>
          </view>
        </view>
      </view>

      <!-- 检查 -->
      <view v-if="tab === 'checks'" class="panel checks-card" :class="{ 'checks-ok': canApprove }">
        <text class="panel-title">自动检查</text>
        <view class="summary-row">
          <text class="summary-name">检查状态</text>
          <text class="gate-badge" :class="candidateReady ? 'pass' : 'warn'">{{ candidateReady ? '全部通过' : '未通过/未运行' }}</text>
        </view>
        <view class="summary-row">
          <text class="summary-name">警告</text>
          <text class="summary-value">{{ candidate?.warning_count ?? 0 }} 条</text>
        </view>

        <text class="panel-title confirm-title">通过前人工确认</text>
        <view v-for="checkName in manualChecks" :key="checkName" class="check-row" @tap="toggleConfirm(checkName)">
          <view class="checkbox" :class="{ checked: confirmations[checkName] }">{{ confirmations[checkName] ? '✓' : '' }}</view>
          <text class="check-text">{{ checkName }}</text>
        </view>
        <text class="muted">人工确认不可由自动检查替代;确认将记录操作者与时间。</text>
      </view>

      <view class="bottom-bar">
        <button class="bar-button danger" @tap="openReturn">退回</button>
        <button class="bar-button primary" :disabled="!canApprove || deciding" :loading="deciding" @tap="approve">通过</button>
      </view>
      <text class="bar-hint">{{ approveHint }}</text>

      <!-- 退回弹窗(视觉稿 11) -->
      <view v-if="returnOpen" class="mask" @tap="returnOpen = false">
        <view class="sheet" @tap.stop>
          <view class="sheet-head">
            <text class="sheet-title">退回提交 {{ detail.submission.submission_number }}</text>
            <text class="sheet-close" @tap="returnOpen = false">×</text>
          </view>
          <view class="sheet-warn"><text>退回将结束本轮审核，学生将收到修订任务与截止时间通知。</text></view>
          <text class="field-label">退回原因 <text class="required">*</text></text>
          <textarea v-model="returnReason" class="area" :maxlength="300" placeholder="例如：3.2 节缺少基线对照说明与消融实验指标…" />
          <text class="field-label">修改要求与执行事项</text>
          <textarea v-model="returnRequirements" class="area" :maxlength="500" placeholder="逐项列出学生需要完成的事项" />
          <view class="deadline-row">
            <picker mode="date" :value="returnDeadline" @change="(event: unknown) => returnDeadline = (event as { detail: { value: string } }).detail.value">
              <view class="deadline-picker"><text class="deadline-value">修订截止 {{ returnDeadline || '未选择' }}</text><text class="deadline-edit">修改 ›</text></view>
            </picker>
            <text class="deadline-note">留空则按「现有截止与今天+{{ revisionDays }} 天」中较晚者</text>
          </view>
          <view class="sheet-actions">
            <button class="bar-button ghost" @tap="returnOpen = false">取消</button>
            <button class="bar-button danger-solid" :disabled="!returnReason.trim() || deciding" :loading="deciding" @tap="confirmReturn">确认退回</button>
          </view>
          <text v-if="!returnReason.trim()" class="sheet-hint">填写退回原因后可提交</text>
        </view>
      </view>
    </template>
  </view>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { describeError } from '../../api/request'
import { fetchSubmission, type Comment, type SubmissionDetail } from '../../api/submissions'
import { addComment, decideSubmission, fetchVersionContent, setCommentStatus, type VersionContent } from '../../api/reviews'

const detail = ref<SubmissionDetail | null>(null)
const content = ref<VersionContent | null>(null)
const loading = ref(false)
const contentLoading = ref(false)
const errorMessage = ref('')
const tab = ref<'content' | 'comments' | 'checks'>('content')

const newSeverity = ref<'blocking' | 'info'>('blocking')
const newAnchor = ref('')
const newBody = ref('')
const adding = ref(false)

const confirmations = ref<Record<string, boolean>>({})
const deciding = ref(false)
const returnOpen = ref(false)
const returnReason = ref('')
const returnRequirements = ref('')
const returnDeadline = ref('')

const sections = computed(() => content.value?.content_snapshot?.sections || [])
const manualChecks = computed(() => {
  const config = (detail.value?.config || {}) as Record<string, unknown>
  return (config.required_manual_checks as string[] | undefined) || []
})
const candidate = computed(() => detail.value?.candidate || null)
const candidateReady = computed(() => {
  const value = candidate.value
  return Boolean(value) && value?.check_status === 'succeeded' && value?.status === 'ready'
})
const blockingOpen = computed(() => (detail.value?.comments || []).filter((item) => item.severity === 'blocking' && item.status !== 'resolved').length)
const canApprove = computed(() => {
  if (!detail.value) return false
  if (String(detail.value.submission.state) !== 'submitted') return false
  if (blockingOpen.value > 0) return false
  return manualChecks.value.every((name) => confirmations.value[name] === true)
})
const approveHint = computed(() => {
  if (!detail.value) return ''
  if (String(detail.value.submission.state) !== 'submitted') return '该提交已有最终决定'
  if (blockingOpen.value > 0) return `存在 ${blockingOpen.value} 条阻断项待确认解决，暂不可通过`
  const pending = manualChecks.value.filter((name) => confirmations.value[name] !== true)
  if (pending.length) return `完成 ${pending.length} 项人工确认后可通过`
  return '通过后初稿阶段解锁，终稿阶段进入归档流程'
})
const studentLabel = computed(() => {
  const participant = (detail.value?.participant || {}) as Record<string, unknown>
  return String(participant.student_name || participant.student_id || '学生')
})
const stageLabel = computed(() => String(detail.value?.stage?.name || ''))
const revisionDays = computed(() => {
  const config = (detail.value?.config || {}) as Record<string, unknown>
  return Number(config.revision_days ?? 7)
})

onLoad((params) => {
  submissionId.value = (params as Record<string, string>)?.id || ''
  if (!submissionId.value) {
    errorMessage.value = '缺少提交参数，请从待审核列表进入'
    return
  }
  void loadAll()
})

const submissionId = ref('')

async function loadAll() {
  loading.value = true
  errorMessage.value = ''
  try {
    detail.value = await fetchSubmission(submissionId.value)
    for (const name of manualChecks.value) {
      if (confirmations.value[name] === undefined) confirmations.value[name] = false
    }
    void loadContent()
  } catch (error) {
    errorMessage.value = describeError(error)
  } finally {
    loading.value = false
  }
}

async function loadContent() {
  const versionId = String(detail.value?.submission.project_version_id || '')
  if (!versionId) return
  contentLoading.value = true
  try {
    content.value = await fetchVersionContent(versionId)
  } catch {
    content.value = null
  } finally {
    contentLoading.value = false
  }
}

async function submitComment() {
  if (!newBody.value.trim()) {
    uni.showToast({ title: '请填写批注内容', icon: 'none' })
    return
  }
  adding.value = true
  try {
    await addComment(submissionId.value, {
      body: newBody.value.trim(),
      severity: newSeverity.value,
      block_id: newAnchor.value.trim(),
      quoted_text: '',
    })
    newBody.value = ''
    newAnchor.value = ''
    uni.showToast({ title: '批注已添加', icon: 'none' })
    detail.value = await fetchSubmission(submissionId.value)
  } catch (error) {
    uni.showToast({ title: describeError(error), icon: 'none' })
  } finally {
    adding.value = false
  }
}

async function resolveComment(comment: Comment) {
  try {
    await setCommentStatus(comment.id, 'resolved')
    detail.value = await fetchSubmission(submissionId.value)
  } catch (error) {
    uni.showToast({ title: describeError(error), icon: 'none' })
  }
}

async function reopenComment(comment: Comment) {
  try {
    await setCommentStatus(comment.id, 'open')
    detail.value = await fetchSubmission(submissionId.value)
  } catch (error) {
    uni.showToast({ title: describeError(error), icon: 'none' })
  }
}

function toggleConfirm(name: string) {
  confirmations.value[name] = !confirmations.value[name]
}

function openReturn() {
  returnReason.value = ''
  returnRequirements.value = ''
  returnDeadline.value = ''
  returnOpen.value = true
}

async function confirmReturn() {
  if (!returnReason.value.trim()) return
  deciding.value = true
  try {
    await decideSubmission(submissionId.value, {
      decision: 'returned',
      reason: [returnReason.value.trim(), returnRequirements.value.trim()].filter(Boolean).join('\n修改要求：'),
      revision_deadline_at: returnDeadline.value ? `${returnDeadline.value}T23:59:00+00:00` : '',
    })
    uni.showToast({ title: '已退回并通知学生', icon: 'none' })
    backToTasks()
  } catch (error) {
    uni.showToast({ title: describeError(error), icon: 'none' })
  } finally {
    deciding.value = false
  }
}

async function approve() {
  if (!canApprove.value) return
  deciding.value = true
  try {
    await decideSubmission(submissionId.value, {
      decision: 'approved',
      reason: '',
      manual_confirmations: Object.fromEntries(manualChecks.value.map((name) => [name, confirmations.value[name] === true])),
    })
    uni.showToast({ title: '审核通过', icon: 'none' })
    backToTasks()
  } catch (error) {
    uni.showToast({ title: describeError(error), icon: 'none' })
  } finally {
    deciding.value = false
  }
}

function backToTasks() {
  setTimeout(() => uni.switchTab({ url: '/pages/tasks/index' }), 600)
}

function severityLabel(comment: Comment) {
  const anchor = comment.block_id ? ` · ${comment.block_id}` : ''
  return `${comment.severity === 'blocking' ? '阻断' : '建议'}${anchor}`
}
function statusLabel(comment: Comment) {
  return ({ open: '待处理', addressed: '已回复 · 待确认', resolved: '已解决' } as Record<string, string>)[comment.status] || comment.status
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
function blockTypeLabel(type: string) {
  return ({ image: '图片', table: '表格', equation: '公式' } as Record<string, string>)[type] || type
}
function formatTime(value: unknown) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '—'
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 24rpx 36rpx 220rpx; }
.panel { margin-top: 24rpx; padding: 30rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 20rpx; }
.state-panel { display: flex; flex-direction: column; min-height: 200rpx; justify-content: center; }
.panel-title { display: block; color: #111827; font-size: 29rpx; font-weight: 750; }
.head-card { border-left: 8rpx solid #004ac6; }
.head-row { display: flex; align-items: center; justify-content: space-between; }
.number { color: #004ac6; font-size: 40rpx; font-weight: 800; }
.readonly-badge { padding: 6rpx 18rpx; border-radius: 999rpx; background: #f3f4f6; color: #6b7280; font-size: 22rpx; }
.meta { display: block; margin-top: 12rpx; color: #6b7280; font-size: 24rpx; }
.meta-sub { display: block; margin-top: 6rpx; color: #9ca3af; font-size: 21rpx; }
.tab-bar { display: flex; gap: 16rpx; margin-top: 24rpx; padding: 10rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 999rpx; }
.tab { flex: 1; height: 68rpx; line-height: 68rpx; text-align: center; border-radius: 999rpx; color: #434655; font-size: 26rpx; }
.tab.active { background: #004ac6; color: #fff; font-weight: 700; }
.content-card { padding-bottom: 44rpx; }
.section { margin-top: 26rpx; }
.section-title { display: block; color: #111827; font-size: 30rpx; font-weight: 750; }
.paragraph { display: block; margin-top: 16rpx; color: #1f2937; font-size: 27rpx; line-height: 1.75; }
.add-comment .field { box-sizing: border-box; height: 80rpx; margin-top: 18rpx; padding: 0 20rpx; background: #f5f7fb; border: 1rpx solid #e5e7eb; border-radius: 12rpx; color: #111827; font-size: 24rpx; }
.severity-row { display: flex; gap: 16rpx; margin-top: 18rpx; }
.severity-pill { padding: 10rpx 24rpx; border-radius: 999rpx; border: 1rpx solid #e5e7eb; color: #6b7280; font-size: 23rpx; }
.severity-pill.active { border-color: #004ac6; background: #e5efff; color: #004ac6; font-weight: 700; }
.area { box-sizing: border-box; width: 100%; min-height: 150rpx; margin-top: 18rpx; padding: 20rpx; background: #f5f7fb; border: 1rpx solid #e5e7eb; border-radius: 12rpx; color: #111827; font-size: 25rpx; }
.bar-button { height: 80rpx; line-height: 80rpx; border-radius: 14rpx; font-size: 27rpx; font-weight: 700; margin-top: 22rpx; }
.bar-button.primary { background: #004ac6; color: #fff; }
.bar-button.primary[disabled] { background: #e5e7eb; color: #9ca3af; }
.bar-button.ghost { border: 1rpx solid #cbd5e1; background: #fff; color: #434655; }
.bar-button.danger { border: 1rpx solid #ba1a1a; background: #fff; color: #ba1a1a; }
.bar-button.danger-solid { background: #ba1a1a; color: #fff; }
.comment-card { border-left: 8rpx solid #004ac6; }
.comment-card.border-red { border-left-color: #ba1a1a; }
.comment-card.border-green { border-left-color: #16a34a; }
.card-head { display: flex; align-items: center; justify-content: space-between; }
.card-kicker { font-size: 26rpx; font-weight: 750; }
.text-red { color: #ba1a1a; }
.text-blue { color: #004ac6; }
.text-green { color: #16a34a; }
.text-orange { color: #b45309; }
.text-gray { color: #6b7280; }
.status { font-size: 22rpx; font-weight: 700; }
.quote-block { margin-top: 16rpx; padding: 18rpx 20rpx; border-left: 4rpx solid #eab308; border-radius: 10rpx; background: #fefce8; }
.quote-text { color: #434655; font-size: 24rpx; line-height: 1.6; }
.comment-body { display: block; margin-top: 18rpx; color: #111827; font-size: 27rpx; line-height: 1.6; }
.reply-bubble { margin-top: 18rpx; padding: 18rpx 20rpx; border-radius: 14rpx; background: #f5f7fb; }
.reply-label { display: block; color: #004ac6; font-size: 21rpx; font-weight: 700; }
.reply-body { display: block; margin-top: 8rpx; color: #111827; font-size: 25rpx; line-height: 1.55; }
.reply-time { display: block; margin-top: 8rpx; color: #9ca3af; font-size: 20rpx; }
.comment-actions { display: flex; gap: 18rpx; margin-top: 20rpx; }
.mini-button { box-sizing: border-box; height: 68rpx; line-height: 68rpx; margin: 0; padding: 0 26rpx; border-radius: 12rpx; font-size: 23rpx; font-weight: 700; }
.mini-button.primary { background: #004ac6; color: #fff; }
.mini-button.ghost { border: 1rpx solid #cbd5e1; background: #fff; color: #434655; }
.checks-card.checks-ok { border-color: #16a34a; }
.confirm-title { margin-top: 30rpx; }
.summary-row { display: flex; justify-content: space-between; align-items: center; margin-top: 20rpx; }
.summary-name { color: #111827; font-size: 26rpx; }
.summary-value { color: #434655; font-size: 25rpx; }
.gate-badge { padding: 6rpx 18rpx; border-radius: 999rpx; font-size: 22rpx; font-weight: 700; }
.gate-badge.pass { background: #e8f7ee; color: #16a34a; }
.gate-badge.warn { background: #fef3c7; color: #b45309; }
.check-row { display: flex; align-items: center; gap: 16rpx; margin-top: 22rpx; }
.checkbox { box-sizing: border-box; width: 36rpx; height: 36rpx; border: 2rpx solid #cbd5e1; border-radius: 8rpx; background: #fff; color: #fff; text-align: center; font-size: 24rpx; line-height: 32rpx; }
.checkbox.checked { border-color: #16a34a; background: #16a34a; }
.check-text { color: #111827; font-size: 26rpx; }
.muted { display: block; margin-top: 14rpx; color: #6b7280; font-size: 22rpx; line-height: 1.55; }
.bottom-bar { position: fixed; left: 0; right: 0; bottom: 0; box-sizing: border-box; display: flex; gap: 20rpx; padding: 20rpx 36rpx calc(20rpx + env(safe-area-inset-bottom)); background: #fff; border-top: 1rpx solid #e5e7eb; }
.bottom-bar .bar-button { flex: 1; margin-top: 0; }
.bar-hint { display: block; position: fixed; left: 0; right: 0; bottom: calc(140rpx + env(safe-area-inset-bottom)); color: #9ca3af; font-size: 21rpx; text-align: center; }
.mask { position: fixed; inset: 0; background: rgba(11, 28, 48, .55); z-index: 30; display: flex; align-items: flex-end; }
.sheet { box-sizing: border-box; width: 100%; max-height: 82vh; overflow-y: auto; padding: 30rpx 36rpx calc(30rpx + env(safe-area-inset-bottom)); background: #fff; border-radius: 24rpx 24rpx 0 0; }
.sheet-head { display: flex; align-items: center; justify-content: space-between; }
.sheet-title { color: #111827; font-size: 32rpx; font-weight: 800; }
.sheet-close { color: #9ca3af; font-size: 40rpx; padding: 0 10rpx; }
.sheet-warn { margin-top: 20rpx; padding: 18rpx 20rpx; border-radius: 12rpx; background: #fff1f0; color: #ba1a1a; font-size: 24rpx; }
.field-label { display: block; margin-top: 26rpx; color: #111827; font-size: 25rpx; font-weight: 700; }
.required { color: #ba1a1a; }
.deadline-row { margin-top: 22rpx; padding: 20rpx; border: 1rpx solid #e5e7eb; border-radius: 14rpx; background: #f8f9ff; }
.deadline-picker { display: flex; align-items: center; justify-content: space-between; }
.deadline-value { color: #004ac6; font-size: 26rpx; font-weight: 750; }
.deadline-edit { color: #004ac6; font-size: 23rpx; }
.deadline-note { display: block; margin-top: 10rpx; color: #9ca3af; font-size: 21rpx; }
.sheet-actions { display: flex; gap: 20rpx; margin-top: 30rpx; }
.sheet-actions .bar-button { flex: 1; }
.sheet-hint { display: block; margin-top: 14rpx; color: #9ca3af; font-size: 21rpx; text-align: center; }
.error-text { color: #ba1a1a; font-size: 25rpx; line-height: 1.5; }
.secondary-button { margin: 24rpx 0 0; height: 76rpx; line-height: 76rpx; border: 1rpx solid #004ac6; border-radius: 12rpx; background: #fff; color: #004ac6; font-size: 25rpx; }
</style>
