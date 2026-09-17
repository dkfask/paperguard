<template>
  <view class="page">
    <text class="eyebrow">STAGE REQUIREMENTS</text>
    <text class="title">确认阶段要求</text>
    <text class="muted">阅读本阶段有效规范后确认。长篇写作与论文绑定仍在电脑端完成。</text>

    <view v-if="loading" class="panel state-panel"><text>正在加载规范摘要…</text></view>
    <view v-else-if="errorMessage" class="panel state-panel">
      <text class="error-text">{{ errorMessage }}</text>
      <button class="secondary-button" @tap="loadDetail">重新加载</button>
    </view>
    <template v-else>
      <view class="panel">
        <text class="panel-title">{{ stageName }}</text>
        <text class="muted">{{ cohortName }}</text>
        <text v-if="!summary.projectBound" class="warn-text">尚未绑定主论文。请先在电脑端绑定后再确认要求。</text>
        <text v-else-if="summary.alreadyAcknowledged" class="pass-text">已确认当前规范版本。</text>
      </view>

      <view v-if="summary.policies.length" class="panel">
        <text class="panel-title">有效规范</text>
        <view v-for="policy in summary.policies" :key="policy.name + policy.version" class="policy-row">
          <text class="policy-name">{{ policy.name }}</text>
          <text class="muted">{{ policy.version ? `版本 ${policy.version}` : '当前版本' }}</text>
        </view>
      </view>

      <view class="panel">
        <text class="panel-title">要求摘要</text>
        <view v-if="summary.items.length === 0" class="muted">本阶段未配置额外规范条目，确认后即可进入写作与预检。</view>
        <view v-for="item in summary.items" :key="item.id" class="item-row">
          <text class="item-title">{{ item.title }}</text>
          <text v-if="item.source" class="muted">{{ item.source }}</text>
        </view>
      </view>

      <view class="check-row" @tap="readConfirmed = !readConfirmed">
        <view class="checkbox" :class="{ checked: readConfirmed }">{{ readConfirmed ? '✓' : '' }}</view>
        <text class="check-text">我已阅读上述阶段要求与规范摘要</text>
      </view>

      <button
        class="primary-button"
        :disabled="!canAcknowledge || submitting"
        :loading="submitting"
        @tap="confirmRequirements"
      >确认阶段要求</button>
      <text class="hint">{{ hint }}</text>
      <button class="ghost-button" @tap="copyDesktopHint">继续写作请用电脑端</button>
    </template>
  </view>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { acknowledgeTask, describeError, fetchParticipant } from '../../api/request'
import { summarizeRequirementItems } from '../../utils/student-flow'
import { applySharePolicy } from '../../utils/privacy'

const participantId = ref('')
const loading = ref(false)
const submitting = ref(false)
const errorMessage = ref('')
const readConfirmed = ref(false)
const stageName = ref('阶段任务')
const cohortName = ref('论文批次')
const summary = ref(summarizeRequirementItems(null))

const canAcknowledge = computed(() => summary.value.projectBound && readConfirmed.value && !summary.value.alreadyAcknowledged)
const hint = computed(() => {
  if (!summary.value.projectBound) return '未绑定主论文时不能确认要求'
  if (summary.value.alreadyAcknowledged) return '规范已确认，可返回任务列表预检提交'
  if (!readConfirmed.value) return '勾选已阅读后才能确认'
  return '确认后任务进入进行中，可在电脑端写作并在小程序预检提交'
})

onLoad((params) => {
  applySharePolicy('/pages/requirements/index')
  const query = params as Record<string, string> | undefined
  participantId.value = query?.id || ''
  if (!participantId.value) {
    errorMessage.value = '缺少阶段任务参数，请从「我的任务」进入'
    return
  }
  void loadDetail()
})

async function loadDetail() {
  loading.value = true
  errorMessage.value = ''
  try {
    const detail = await fetchParticipant(participantId.value)
    stageName.value = String(detail.stage?.name || detail.participant?.stage_name || '阶段任务')
    cohortName.value = String(detail.cohort?.name || detail.participant?.cohort_name || '论文批次')
    summary.value = summarizeRequirementItems(detail)
    readConfirmed.value = summary.value.alreadyAcknowledged
  } catch (error) {
    errorMessage.value = describeError(error)
  } finally {
    loading.value = false
  }
}

async function confirmRequirements() {
  if (!canAcknowledge.value) return
  submitting.value = true
  try {
    await acknowledgeTask(participantId.value, summary.value.snapshotHash)
    uni.showToast({ title: '已确认阶段要求', icon: 'none' })
    uni.navigateBack({ fail: () => uni.switchTab({ url: '/pages/tasks/index' }) })
  } catch (error) {
    uni.showToast({ title: describeError(error), icon: 'none' })
  } finally {
    submitting.value = false
  }
}

function copyDesktopHint() {
  const hintText = '请在电脑浏览器打开论文卫士Pro 完成论文绑定与长文写作'
  uni.setClipboardData({
    data: hintText,
    success: () => uni.showToast({ title: '已复制电脑端提示', icon: 'none' }),
    fail: () => uni.showToast({ title: hintText, icon: 'none' }),
  })
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 48rpx 36rpx 120rpx; }
.eyebrow { display: block; color: #6b7280; font-size: 22rpx; letter-spacing: 3rpx; }
.title { display: block; margin-top: 8rpx; color: #111827; font-size: 50rpx; font-weight: 800; }
.muted { display: block; margin-top: 12rpx; color: #6b7280; font-size: 24rpx; line-height: 1.55; }
.panel { margin-top: 28rpx; padding: 30rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 20rpx; }
.state-panel { display: flex; flex-direction: column; min-height: 180rpx; justify-content: center; }
.panel-title { display: block; color: #111827; font-size: 29rpx; font-weight: 750; }
.warn-text { display: block; margin-top: 16rpx; color: #b45309; font-size: 24rpx; line-height: 1.5; }
.pass-text { display: block; margin-top: 16rpx; color: #16a34a; font-size: 24rpx; }
.policy-row, .item-row { margin-top: 18rpx; padding-top: 18rpx; border-top: 1rpx solid #f3f4f6; }
.policy-name, .item-title { display: block; color: #111827; font-size: 26rpx; font-weight: 650; }
.check-row { display: flex; gap: 16rpx; align-items: flex-start; margin-top: 28rpx; }
.checkbox { width: 40rpx; height: 40rpx; border: 2rpx solid #004ac6; border-radius: 8rpx; color: #004ac6; text-align: center; line-height: 36rpx; font-size: 24rpx; }
.checkbox.checked { background: #e5efff; }
.check-text { flex: 1; color: #111827; font-size: 25rpx; line-height: 1.5; }
.primary-button, .secondary-button, .ghost-button { height: 78rpx; line-height: 78rpx; margin: 24rpx 0 0; border-radius: 12rpx; font-size: 25rpx; }
.primary-button { background: #004ac6; color: #fff; border: none; }
.primary-button[disabled] { opacity: 0.45; }
.secondary-button { border: 1rpx solid #004ac6; background: #fff; color: #004ac6; }
.ghost-button { border: 1rpx solid #e5e7eb; background: #fff; color: #434655; }
.hint { display: block; margin-top: 12rpx; color: #6b7280; font-size: 22rpx; }
.error-text { color: #ba1a1a; font-size: 25rpx; line-height: 1.5; }
</style>
