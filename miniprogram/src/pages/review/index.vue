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
        <text class="meta-sub">学生草稿修改不影响此处版本 · 第 {{ detail.submission.review_cycle || 1 }} 轮</text>
        <text v-if="!canDecide" class="meta-sub">{{ approveHint }}</text>
      </view>

      <view class="tab-bar">
        <view class="tab" :class="{ active: tab === 'content' }" @tap="tab = 'content'">正文</view>
        <view class="tab" :class="{ active: tab === 'comments' }" @tap="tab = 'comments'">批注 ({{ detail.comments.length }})</view>
        <view class="tab" :class="{ active: tab === 'checks' }" @tap="tab = 'checks'">检查</view>
      </view>

      <view v-if="tab === 'content'" class="panel content-card">
        <view v-if="contentLoading" class="muted">正在加载快照正文…</view>
        <view v-else-if="sections.length === 0" class="muted">该版本无可读文本(或正文为空)。</view>
        <view v-for="section in sections" :key="section.key" class="section">
          <text class="section-title">{{ section.title || section.key }}</text>
          <view v-for="paragraph in section.paragraphs || []" :key="paragraph.id">
            <text
              v-if="paragraph.type === 'paragraph'"
              class="paragraph"
              :class="{ selected: selectedParagraph?.id === paragraph.id }"
              @tap="canDecide && selectParagraph(section, paragraph)"
            >{{ paragraph.text }}</text>
            <text v-else class="paragraph muted">[{{ blockTypeLabel(paragraph.type) }}]</text>
          </view>
        </view>
        <text v-if="canDecide && selectedParagraph" class="muted">已选原文片段，可切到批注页引用。</text>
      </view>

      <view v-if="tab === 'comments'">
        <view v-if="canDecide" class="panel add-comment">
          <text class="panel-title">新增批注</text>
          <view class="severity-row">
            <view class="severity-pill" :class="{ active: newSeverity === 'blocking' }" @tap="newSeverity = 'blocking'">必须处理的阻断项</view>
            <view class="severity-pill" :class="{ active: newSeverity === 'info' }" @tap="newSeverity = 'info'">普通建议</view>
          </view>
          <input v-model="newAnchor" class="field" placeholder="定位段落(如 3.2 实验分析 第2段)" maxlength="120" />
          <view v-if="newQuoted" class="quote-block"><text class="quote-text">“{{ newQuoted }}”</text></view>
          <textarea v-model="newBody" class="area" :maxlength="2000" placeholder="批注内容:具体、可执行,绑定原文片段更易定位" />
          <button class="bar-button primary" :loading="adding" @tap="submitComment">添加批注</button>
        </view>

        <text v-if="grouped.blocking.length" class="section-label">必须处理的阻断 · {{ grouped.blocking.length }}</text>
        <view v-for="comment in grouped.blocking" :key="comment.id" class="panel comment-card border-red">
          <view class="card-head">
            <text class="card-kicker text-red">{{ severityLabel(comment) }}</text>
            <text class="status" :class="`text-${statusTone(comment)}`">{{ statusLabel(comment) }}</text>
          </view>
          <view v-if="comment.quoted_text" class="quote-block"><text class="quote-text">“{{ comment.quoted_text }}”</text></view>
          <text class="comment-body">{{ comment.body }}</text>
          <view v-for="response in comment.responses" :key="response.id" class="reply-bubble">
            <text class="reply-label">学生回复</text>
            <text class="reply-body">{{ response.body }}</text>
            <text class="reply-time">{{ formatTime(response.created_at) }}</text>
          </view>
          <view v-if="canDecide && comment.status !== 'resolved'" class="comment-actions">
            <button class="mini-button primary" @tap="resolveComment(comment)">确认解决</button>
            <button class="mini-button ghost" @tap="reopenComment(comment)">重新打开</button>
          </view>
        </view>

        <text v-if="grouped.suggestions.length" class="section-label">普通建议 · {{ grouped.suggestions.length }}</text>
        <view v-for="comment in grouped.suggestions" :key="comment.id" class="panel comment-card">
          <view class="card-head">
            <text class="card-kicker text-blue">{{ severityLabel(comment) }}</text>
            <text class="status" :class="`text-${statusTone(comment)}`">{{ statusLabel(comment) }}</text>
          </view>
          <view v-if="comment.quoted_text" class="quote-block"><text class="quote-text">“{{ comment.quoted_text }}”</text></view>
          <text class="comment-body">{{ comment.body }}</text>
          <view v-for="response in comment.responses" :key="response.id" class="reply-bubble">
            <text class="reply-label">学生回复</text>
            <text class="reply-body">{{ response.body }}</text>
            <text class="reply-time">{{ formatTime(response.created_at) }}</text>
          </view>
          <view v-if="canDecide && comment.status !== 'resolved'" class="comment-actions">
            <button class="mini-button primary" @tap="resolveComment(comment)">确认解决</button>
            <button class="mini-button ghost" @tap="reopenComment(comment)">重新打开</button>
          </view>
        </view>

        <text v-if="grouped.resolved.length" class="section-label">已确认解决 · {{ grouped.resolved.length }}</text>
        <view v-for="comment in grouped.resolved" :key="comment.id" class="panel comment-card border-green">
          <view class="card-head">
            <text class="card-kicker text-green">{{ severityLabel(comment) }}</text>
            <text class="status text-green">{{ statusLabel(comment) }}</text>
          </view>
          <text class="comment-body">{{ comment.body }}</text>
        </view>
      </view>

      <view v-if="tab === 'checks'" class="panel checks-card" :class="{ 'checks-ok': canApprove }">
        <text class="panel-title">自动检查</text>
        <view class="summary-row">
          <text class="summary-name">检查状态</text>
          <text class="gate-badge" :class="candidateReady && blockingGates === 0 ? 'pass' : 'warn'">{{ candidateReady && blockingGates === 0 ? '全部通过' : '未通过/未运行' }}</text>
        </view>
        <view class="summary-row">
          <text class="summary-name">警告</text>
          <text class="summary-value">{{ candidate?.warning_count ?? 0 }} 条</text>
        </view>
        <view v-for="gate in candidate?.gates || []" :key="gate.key" class="summary-row">
          <text class="summary-name">{{ gate.label }}</text>
          <text class="gate-badge" :class="gate.passed ? 'pass' : (gate.blocking ? 'warn' : 'pass')">{{ gate.passed ? '已通过' : '未满足' }}</text>
        </view>

        <text v-if="materialItems.length" class="panel-title confirm-title">必交材料</text>
        <view v-for="item in materialItems" :key="item.label" class="summary-row">
          <text class="summary-name">{{ item.label }}</text>
          <text class="gate-badge" :class="item.passed ? 'pass' : 'warn'">{{ item.passed ? '已绑定' : '未绑定' }}</text>
        </view>
        <text v-if="materialItems.length" class="muted">材料绑定由学生在电脑端完成；此处只展示提交时门禁结果。</text>

        <text v-if="canDecide" class="panel-title confirm-title">通过前人工确认</text>
        <view v-if="canDecide" v-for="checkName in manualChecks" :key="checkName" class="check-row" @tap="toggleConfirm(checkName)">
          <view class="checkbox" :class="{ checked: confirmations[checkName] }">{{ confirmations[checkName] ? '✓' : '' }}</view>
          <text class="check-text">{{ checkName }}</text>
        </view>
        <text v-if="canDecide" class="muted">人工确认不可由自动检查替代;确认将记录操作者与时间。</text>
        <text v-else class="muted">该提交已有最终决定，此处仅展示当时检查与材料门禁，不可再改。</text>
      </view>

      <view v-if="canDecide" class="bottom-bar">
        <button class="bar-button danger" @tap="openReturn">退回</button>
        <button class="bar-button primary" :disabled="!canApprove || deciding" :loading="deciding" @tap="approve">通过</button>
      </view>
      <text v-if="canDecide" class="bar-hint">{{ approveHint }}</text>

      <view v-if="returnOpen" class="mask" @tap="returnOpen = false">
        <view class="sheet" @tap.stop>
          <view class="sheet-head">
            <text class="sheet-title">退回提交 {{ detail.submission.submission_number }}</text>
            <text class="sheet-close" @tap="returnOpen = false">×</text>
          </view>
          <view class="sheet-warn"><text>退回将结束本轮审核，学生将收到修订任务与截止时间通知。</text></view>
          <text class="field-label">退回原因 <text class="required">*</text></text>
          <textarea v-model="returnReason" class="area" :maxlength="300" placeholder="例如：3.2 节缺少基线对照说明与消融实验指标…" />
          <text class="field-label">修改要求与执行事项 <text class="required">*</text></text>
          <textarea v-model="returnRequirements" class="area" :maxlength="500" placeholder="逐项列出学生需要完成的事项" />
          <view class="deadline-row">
            <picker mode="date" :value="returnDeadline" @change="(event: unknown) => returnDeadline = (event as { detail: { value: string } }).detail.value">
              <view class="deadline-picker"><text class="deadline-value">修订截止 {{ returnDeadline || '未选择' }}</text><text class="deadline-edit">修改 ›</text></view>
            </picker>
            <text class="deadline-note">默认取今天 + {{ revisionDays }} 天；退回必须选择修订截止</text>
          </view>
          <view class="sheet-actions">
            <button class="bar-button ghost" @tap="returnOpen = false">取消</button>
            <button class="bar-button danger-solid" :disabled="!canReturn || deciding" :loading="deciding" @tap="confirmReturn">确认退回</button>
          </view>
          <text v-if="returnHint" class="sheet-hint">{{ returnHint }}</text>
        </view>
      </view>
    </template>
  </view>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import { describeError } from '../../api/request'
import { fetchSubmission, type Comment, type SubmissionDetail } from '../../api/submissions'
import { addComment, decideSubmission, fetchVersionContent, setCommentStatus, type VersionContent } from '../../api/reviews'
import { ensureSessionReady } from '../../utils/session-guard'
import { formatDateTime } from '../../utils/format'
import { materialGateItems } from '../../utils/student-flow'
import {
  buildReturnPayload,
  canApproveReview,
  canDecideReview,
  commentPayload,
  defaultRevisionDeadline,
  groupComments,
} from '../../utils/teacher-flow'
import { applySharePolicy } from '../../utils/privacy'

const detail = ref<SubmissionDetail | null>(null)
const content = ref<VersionContent | null>(null)
const loading = ref(false)
const contentLoading = ref(false)
const errorMessage = ref('')
const tab = ref<'content' | 'comments' | 'checks'>('content')

const newSeverity = ref<'blocking' | 'info'>('blocking')
const newAnchor = ref('')
const newQuoted = ref('')
const newBody = ref('')
const adding = ref(false)
const selectedParagraph = ref<{ id: string; text: string } | null>(null)

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
const blockingGates = computed(() => (candidate.value?.gates || []).filter((gate) => gate.blocking && !gate.passed).length)
const materialItems = computed(() => materialGateItems(candidate.value?.gates || []))
const blockingOpen = computed(() => (detail.value?.comments || []).filter((item) => item.severity === 'blocking' && item.status !== 'resolved').length)
const grouped = computed(() => groupComments(detail.value?.comments || []))
const approveGate = computed(() => canApproveReview({
  submissionState: detail.value?.submission.state,
  blockingOpen: blockingOpen.value,
  manualChecks: manualChecks.value,
  confirmations: confirmations.value,
  candidateReady: candidateReady.value,
  blockingGates: blockingGates.value,
}))
const canApprove = computed(() => approveGate.value.ok)
  const canDecide = computed(() => canDecideReview(detail.value?.submission.state))
  const approveHint = computed(() => canDecide.value ? approveGate.value.reason : '该提交已有最终决定，仅可查看当时快照')
const returnDraft = computed(() => buildReturnPayload({
  reason: returnReason.value,
  requirements: returnRequirements.value,
  deadline: returnDeadline.value,
}))
const canReturn = computed(() => returnDraft.value.ok)
const returnHint = computed(() => returnDraft.value.ok ? '' : (returnDraft.value.reason || ''))
const studentLabel = computed(() => {
  const participant = (detail.value?.participant || {}) as Record<string, unknown>
  return String(participant.student_name || participant.student_id || '学生')
})
const stageLabel = computed(() => String(detail.value?.stage?.name || ''))
const revisionDays = computed(() => {
  const config = (detail.value?.config || {}) as Record<string, unknown>
  return Number(config.revision_days ?? 7)
})
const submissionId = ref('')

onLoad((params) => {
  applySharePolicy('/pages/review/index')
  if (!ensureSessionReady()) return
  submissionId.value = (params as Record<string, string>)?.id || ''
  if (!submissionId.value) {
    errorMessage.value = '缺少提交参数，请从待审核列表进入'
    return
  }
  void loadAll()
})

onShow(() => {
  if (!ensureSessionReady()) return
})

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

function selectParagraph(section: { title?: string; key: string }, paragraph: { id: string; text?: string }) {
  if (!canDecide.value) return
  const text = String(paragraph.text || '').trim()
  selectedParagraph.value = { id: paragraph.id, text }
  newAnchor.value = `${section.title || section.key} · ${paragraph.id}`
  newQuoted.value = text.slice(0, 280)
  uni.showToast({ title: '已引用该段落', icon: 'none' })
}

async function submitComment() {
  if (!canDecide.value) return
  const payload = commentPayload({
    body: newBody.value,
    severity: newSeverity.value,
    blockId: newAnchor.value,
    quotedText: newQuoted.value,
  })
  if (!payload.body) {
    uni.showToast({ title: '请填写批注内容', icon: 'none' })
    return
  }
  adding.value = true
  try {
    await addComment(submissionId.value, payload)
    newBody.value = ''
    newAnchor.value = ''
    newQuoted.value = ''
    selectedParagraph.value = null
    uni.showToast({ title: '批注已添加', icon: 'none' })
    detail.value = await fetchSubmission(submissionId.value)
  } catch (error) {
    handleDecisionError(error)
  } finally {
    adding.value = false
  }
}

async function resolveComment(comment: Comment) {
  if (!canDecide.value) return
  try {
    await setCommentStatus(comment.id, 'resolved')
    detail.value = await fetchSubmission(submissionId.value)
  } catch (error) {
    handleDecisionError(error)
  }
}

async function reopenComment(comment: Comment) {
  if (!canDecide.value) return
  try {
    await setCommentStatus(comment.id, 'open')
    detail.value = await fetchSubmission(submissionId.value)
  } catch (error) {
    handleDecisionError(error)
  }
}

function toggleConfirm(name: string) {
  if (!canDecide.value) return
  confirmations.value[name] = !confirmations.value[name]
}

function openReturn() {
  if (!canDecide.value) return
  returnReason.value = ''
  returnRequirements.value = ''
  returnDeadline.value = defaultRevisionDeadline(revisionDays.value)
  returnOpen.value = true
}

async function confirmReturn() {
  if (!canDecide.value) return
  const draft = returnDraft.value
  if (!draft.ok || !draft.payload) {
    uni.showToast({ title: draft.reason || '请补全退回信息', icon: 'none' })
    return
  }
  deciding.value = true
  try {
    await decideSubmission(submissionId.value, draft.payload)
    uni.showToast({ title: '已退回并通知学生', icon: 'none' })
    backToTasks()
  } catch (error) {
    handleDecisionError(error)
  } finally {
    deciding.value = false
  }
}

async function approve() {
  if (!canDecide.value || !canApprove.value) return
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
    handleDecisionError(error)
  } finally {
    deciding.value = false
  }
}

function handleDecisionError(error: unknown) {
  const message = describeError(error)
  uni.showToast({ title: message, icon: 'none' })
  if (message.includes('状态已变化')) {
    void loadAll()
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
function statusTone(comment: Comment) {
  if (comment.status === 'resolved') return 'green'
  if (comment.status === 'addressed') return 'orange'
  return 'gray'
}
function blockTypeLabel(type: string) {
  return ({ image: '图片', table: '表格', equation: '公式' } as Record<string, string>)[type] || type
}
function formatTime(value: unknown) {
  return formatDateTime(value)
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 24rpx 36rpx 220rpx; }
.panel { margin-top: 24rpx; padding: 30rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 20rpx; }
.state-panel { display: flex; flex-direction: column; min-height: 200rpx; justify-content: center; }
.panel-title { display: block; color: #111827; font-size: 29rpx; font-weight: 750; }
.section-label { display: block; margin: 28rpx 8rpx 0; color: #434655; font-size: 24rpx; font-weight: 700; }
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
.paragraph.selected { background: #e5efff; border-radius: 8rpx; padding: 8rpx; }
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
