<template>
  <view class="page">
    <view v-if="stageName" class="stage-bar">阶段「{{ stageName }}」 · 提交前将冻结不可变候选版本</view>

    <view v-if="loading" class="panel state-panel"><text>正在预检：冻结候选版本并运行门禁检查…</text></view>

    <template v-else>
      <view v-if="errorMessage" class="panel state-panel">
        <text class="error-text">{{ errorMessage }}</text>
        <button v-if="recovery.kind === 'repreflight' || recovery.kind === 'refresh'" class="secondary-button" @tap="runPreflight">{{ recovery.label }}</button>
        <button v-else class="secondary-button" @tap="followRecovery">{{ recovery.label }}</button>
      </view>

      <template v-else-if="candidate">
        <view class="panel gate-block gate-danger" v-if="blockingGates.length">
          <view class="block-head"><text class="block-title">{{ blockingGates.length }} 项阻断，暂不能提交</text></view>
          <view v-for="gate in blockingGates" :key="gate.key" class="block-item">
            <text class="block-name">{{ gate.label }}</text>
            <text class="block-detail">{{ gateDetail(gate) }}</text>
          </view>
        </view>

        <view v-if="deadlineText" class="deadline-bar" :class="{ late: deadlineLate }">
          <text>{{ deadlineLate ? '已逾期' : '有效截止' }} · {{ deadlineText }}{{ deadlineCountdown ? ` · ${deadlineCountdown}` : '' }}</text>
        </view>

        <view class="panel">
          <text class="panel-title">候选版本</text>
          <text class="meta-line">冻结于 {{ formatTime(candidate.created_at) }} · 源修订 R{{ candidate.source_revision }}</text>
          <view class="hash-row"><text class="hash">内容摘要 {{ shortHash(candidate.content_hash) }}</text><text class="hash-note">提交后不可变</text></view>
        </view>

        <view class="panel">
          <text class="panel-title">提交前门禁校验</text>
          <view v-for="gate in candidate.gates" :key="gate.key" class="gate-row">
            <text class="gate-name">{{ gate.label }}</text>
            <text class="gate-badge" :class="gate.passed ? 'pass' : (gate.blocking ? 'block' : 'warn')">
              {{ gate.passed ? '已通过' : (gate.blocking ? '未满足' : '待确认') }}
            </text>
          </view>
        </view>

        <view v-if="materialItems.length" class="panel">
          <text class="panel-title">必交材料</text>
          <view v-for="item in materialItems" :key="item.label" class="gate-row">
            <text class="gate-name">{{ item.label }}</text>
            <text class="gate-badge" :class="item.passed ? 'pass' : 'block'">{{ item.passed ? '已绑定' : '未绑定' }}</text>
          </view>
          <text class="muted">材料绑定请使用电脑端过程材料；此处只展示门禁结果。</text>
        </view>

        <view v-if="candidate.warning_count > 0" class="panel">
          <view class="warn-row">
            <text class="warn-text">存在 {{ candidate.warning_count }} 条格式警告（不阻断提交）</text>
          </view>
          <view class="check-row" @tap="warningAcknowledged = !warningAcknowledged">
            <view class="checkbox" :class="{ checked: warningAcknowledged }">{{ warningAcknowledged ? '✓' : '' }}</view>
            <text class="check-text">我已知晓上述警告，确认按当前版本提交</text>
          </view>
        </view>

        <view v-if="needsNote" class="panel">
          <text class="panel-title">修改说明</text>
          <textarea v-model="modificationNote" class="note-area" :maxlength="300" placeholder="简要说明本轮修改内容，将随提交存档" />
        </view>

        <view class="bottom-bar">
          <button class="bar-button ghost" @tap="runPreflight">重新预检</button>
          <button
            class="bar-button primary"
            :disabled="!canSubmit || submitting"
            :loading="submitting"
            @tap="submit"
          >确认正式提交</button>
        </view>
        <text class="bar-hint">{{ submitHint }}</text>
      </template>

      <view v-else class="panel state-panel">
        <text class="panel-title">准备提交</text>
        <text class="muted">将冻结当前正文为不可变候选版本，并按阶段门禁逐项校验。</text>
        <textarea v-if="needsNote" v-model="modificationNote" class="note-area" :maxlength="300" placeholder="修改说明：简要说明本轮修改内容" />
        <button class="bar-button primary" :loading="loading" @tap="runPreflight">开始预检</button>
      </view>
    </template>
  </view>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { describeError, fetchParticipant, type Task } from '../../api/request'
import { confirmSubmission, createCandidate, type Candidate, type Gate } from '../../api/submissions'
import { idempotencyKey, renewIdempotencyKey } from '../../utils/idempotency'
import { deadlineCountdownLabel, formatDateTime, isDeadlinePassed, shortHash as formatHash } from '../../utils/format'
import { requestSubscribeAuthorization } from '../../utils/subscribe'
import { applySharePolicy } from '../../utils/privacy'
import { effectiveDeadline, materialGateItems, submitRecoveryAction, type SubmitRecoveryAction } from '../../utils/student-flow'

const participantId = ref('')
const stageName = ref('')
const candidate = ref<Candidate | null>(null)
const loading = ref(false)
const errorMessage = ref('')
const warningAcknowledged = ref(false)
const modificationNote = ref('')
const submitting = ref(false)
// 修订再提交需要修改说明(后端 CandidateCreate.modification_note)
const needsNote = ref(false)
const recovery = ref<SubmitRecoveryAction>({ kind: 'repreflight', label: '重新预检' })
const participant = ref<Task | null>(null)
const deadlineRaw = computed(() => effectiveDeadline(participant.value))
const deadlineText = computed(() => formatDateTime(deadlineRaw.value))
const deadlineCountdown = computed(() => deadlineCountdownLabel(deadlineRaw.value))
const deadlineLate = computed(() => Boolean(participant.value?.is_late) || isDeadlinePassed(deadlineRaw.value))

const blockingGates = computed(() => candidate.value?.gates.filter((gate) => !gate.passed && gate.blocking) || [])
const materialItems = computed(() => materialGateItems(candidate.value?.gates || []))
const canSubmit = computed(() => {
  if (!candidate.value || candidate.value.status !== 'ready') return false
  if (candidate.value.warning_count > 0 && !warningAcknowledged.value) return false
  return true
})
const submitHint = computed(() => {
  if (!candidate.value) return ''
  if (candidate.value.status !== 'ready') return '完成全部阻断项后需重新预检'
  if (candidate.value.warning_count > 0 && !warningAcknowledged.value) return '勾选警告确认后可提交'
  return '提交成功后将生成唯一提交编号，重复点击不会产生重复提交'
})

onLoad((params) => {
  applySharePolicy('/pages/submit/index')
  const query = params as Record<string, string> | undefined
  participantId.value = query?.participant_id || ''
  stageName.value = query?.stage || ''
  needsNote.value = query?.revision === '1'
  if (!participantId.value) {
    errorMessage.value = '缺少阶段任务参数，请从「我的任务」进入'
    return
  }
  void loadParticipant()
  void runPreflight()
})

async function loadParticipant() {
  try {
    const detail = await fetchParticipant(participantId.value)
    participant.value = detail.participant
    if (!stageName.value) stageName.value = String(detail.stage?.name || detail.participant.stage_name || '')
  } catch {
    participant.value = null
  }
}

async function runPreflight() {
  loading.value = true
  errorMessage.value = ''
  candidate.value = null
  try {
    candidate.value = await createCandidate(participantId.value, [], modificationNote.value.trim())
    warningAcknowledged.value = false
  } catch (error) {
    errorMessage.value = describeError(error)
    recovery.value = submitRecoveryAction(error)
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!candidate.value || !canSubmit.value) return
  submitting.value = true
  try {
    await requestSubscribeAuthorization({ silent: true })
    const submission = await confirmSubmission(
      candidate.value.id,
      idempotencyKey(`submission:${participantId.value}`),
      warningAcknowledged.value,
    )
    renewIdempotencyKey(`submission:${participantId.value}`)
    uni.redirectTo({
      url: `/pages/submit/success?number=${encodeURIComponent(submission.submission_number)}&hash=${encodeURIComponent(candidate.value.content_hash)}`,
    })
  } catch (error) {
    const next = submitRecoveryAction(error)
    recovery.value = next
    errorMessage.value = describeError(error)
    uni.showToast({ title: errorMessage.value, icon: 'none' })
    if (next.kind === 'repreflight') void runPreflight()
  } finally {
    submitting.value = false
  }
}

function followRecovery() {
  const url = recovery.value.url
  if (!url) return
  if (url === '/pages/tasks/index') {
    uni.switchTab({ url })
    return
  }
  uni.redirectTo({ url })
}

function gateDetail(gate: Gate) {
  return Array.isArray(gate.detail) ? gate.detail.join('、') || '待补充' : gate.detail || '待补充'
}

function formatTime(value: string) {
  return formatDateTime(value)
}

function shortHash(hash: string) {
  return formatHash(hash)
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 24rpx 36rpx 200rpx; }
.stage-bar { padding: 18rpx 24rpx; border-radius: 14rpx; background: #eff4ff; color: #004ac6; font-size: 24rpx; }
.deadline-bar { display: flex; align-items: center; margin-top: 24rpx; padding: 20rpx 24rpx; border-radius: 14rpx; background: #fef3c7; color: #b45309; font-size: 25rpx; font-weight: 700; }
.deadline-bar.late { background: #fdeaea; color: #ba1a1a; }
.panel { margin-top: 24rpx; padding: 30rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 20rpx; }
.state-panel { display: flex; flex-direction: column; min-height: 200rpx; justify-content: center; }
.panel-title { display: block; color: #111827; font-size: 29rpx; font-weight: 750; }
.muted { display: block; margin-top: 14rpx; color: #6b7280; font-size: 24rpx; line-height: 1.55; }
.meta-line { display: block; margin-top: 14rpx; color: #6b7280; font-size: 23rpx; }
.hash-row { display: flex; align-items: center; justify-content: space-between; margin-top: 18rpx; padding: 14rpx 18rpx; border-radius: 10rpx; background: #f5f7fb; }
.hash { color: #434655; font-size: 21rpx; }
.hash-note { color: #9ca3af; font-size: 20rpx; }
.gate-row { display: flex; justify-content: space-between; align-items: center; margin-top: 22rpx; }
.gate-name { color: #111827; font-size: 26rpx; }
.gate-badge { padding: 6rpx 18rpx; border-radius: 999rpx; font-size: 22rpx; font-weight: 700; }
.gate-badge.pass { background: #e8f7ee; color: #16a34a; }
.gate-badge.warn { background: #fef3c7; color: #b45309; }
.gate-badge.block { background: #fdeaea; color: #ba1a1a; }
.gate-block { border-left: 8rpx solid #ba1a1a; }
.gate-danger { background: #fff8f8; }
.block-head { display: flex; align-items: center; }
.block-title { color: #ba1a1a; font-size: 29rpx; font-weight: 750; }
.block-item { margin-top: 18rpx; }
.block-name { display: block; color: #111827; font-size: 25rpx; font-weight: 700; }
.block-detail { display: block; margin-top: 6rpx; color: #6b7280; font-size: 23rpx; }
.warn-row { display: flex; }
.warn-text { color: #b45309; font-size: 25rpx; font-weight: 700; }
.check-row { display: flex; align-items: center; gap: 16rpx; margin-top: 22rpx; }
.checkbox { box-sizing: border-box; width: 36rpx; height: 36rpx; border: 2rpx solid #cbd5e1; border-radius: 8rpx; background: #fff; color: #fff; text-align: center; font-size: 24rpx; line-height: 32rpx; }
.checkbox.checked { border-color: #004ac6; background: #004ac6; }
.check-text { color: #111827; font-size: 25rpx; }
.note-area { box-sizing: border-box; width: 100%; min-height: 140rpx; margin-top: 18rpx; padding: 20rpx; background: #f5f7fb; border: 1rpx solid #e5e7eb; border-radius: 12rpx; color: #111827; font-size: 25rpx; }
.bottom-bar { position: fixed; left: 0; right: 0; bottom: 0; box-sizing: border-box; display: flex; gap: 20rpx; padding: 20rpx 36rpx calc(20rpx + env(safe-area-inset-bottom)); background: #fff; border-top: 1rpx solid #e5e7eb; }
.bar-button { flex: 1; height: 84rpx; line-height: 84rpx; border-radius: 14rpx; font-size: 28rpx; font-weight: 700; }
.bar-button.primary { background: #004ac6; color: #fff; }
.bar-button.primary[disabled] { background: #e5e7eb; color: #9ca3af; }
.bar-button.ghost { border: 1rpx solid #cbd5e1; background: #fff; color: #434655; }
.bar-hint { display: block; position: fixed; left: 0; right: 0; bottom: calc(140rpx + env(safe-area-inset-bottom)); color: #9ca3af; font-size: 21rpx; text-align: center; }
.error-text { color: #ba1a1a; font-size: 25rpx; line-height: 1.5; }
.secondary-button { margin: 24rpx 0 0; height: 76rpx; line-height: 76rpx; border: 1rpx solid #004ac6; border-radius: 12rpx; background: #fff; color: #004ac6; font-size: 25rpx; }
</style>
