<template>
  <view class="page">
    <view class="hero">
      <view class="hero-check">✓</view>
      <text class="hero-title">提交成功</text>
    </view>

    <view class="panel voucher">
      <text class="voucher-label">本次提交编号</text>
      <text class="voucher-number">{{ submissionNumber }}</text>
      <text class="voucher-meta">{{ submittedAt }}</text>
      <view class="hash-row">
        <text class="hash">版本快照 {{ shortHash }}</text>
        <text class="hash-note">🔒 提交后不可变</text>
      </view>
    </view>

    <view class="panel notice">
      <text class="notice-text">重复点击不会产生重复提交；结果可在「提交历史」中随时查询。</text>
    </view>

    <view class="panel notify-card">
      <view class="notify-head">
        <text class="notify-title">开启审核结果提醒</text>
        <text class="notify-sub">退回或通过时第一时间收到微信通知</text>
      </view>
      <button class="notify-button" @tap="requestSubscribe">去开启</button>
    </view>

    <view class="bottom-bar">
      <button class="bar-button primary" @tap="backToTasks">返回我的任务</button>
      <button class="bar-button ghost" @tap="goHistory">查看提交历史</button>
    </view>
  </view>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'

const submissionNumber = ref('—')
const submittedAt = ref('')
const contentHash = ref('')

function shortHash() {
  const text = contentHash.value
  return text.length > 12 ? `${text.slice(0, 6)}…${text.slice(-4)}` : text || '—'
}

onLoad((params) => {
  const query = params as Record<string, string> | undefined
  submissionNumber.value = query?.number ? decodeURIComponent(query.number) : '—'
  contentHash.value = query?.hash ? decodeURIComponent(query.hash) : ''
  submittedAt.value = new Date().toLocaleString('zh-CN', { hour12: false })
})

// 订阅消息模板 ID 上线前在 manifest 或配置中下发; 未配置时引导置灰不阻断
function requestSubscribe() {
  const templateIds = (import.meta.env.VITE_SUBSCRIBE_TEMPLATE_IDS || '').split(',').map((id) => id.trim()).filter(Boolean)
  if (!templateIds.length) {
    uni.showToast({ title: '提醒模板尚未配置，上线后开放', icon: 'none' })
    return
  }
  uni.requestSubscribeMessage({
    tmplIds: templateIds,
    complete: () => uni.showToast({ title: '已按微信授权结果处理', icon: 'none' }),
  })
}

function backToTasks() {
  uni.switchTab({ url: '/pages/tasks/index' })
}

function goHistory() {
  uni.redirectTo({ url: '/pages/submissions/history' })
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 120rpx 36rpx 220rpx; }
.hero { display: flex; flex-direction: column; align-items: center; }
.hero-check { box-sizing: border-box; width: 132rpx; height: 132rpx; border: 8rpx solid #16a34a; border-radius: 50%; color: #16a34a; font-size: 64rpx; font-weight: 800; text-align: center; line-height: 116rpx; }
.hero-title { margin-top: 30rpx; color: #111827; font-size: 46rpx; font-weight: 800; }
.panel { margin-top: 30rpx; padding: 34rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 20rpx; }
.voucher { display: flex; flex-direction: column; align-items: center; }
.voucher-label { color: #6b7280; font-size: 24rpx; }
.voucher-number { margin-top: 12rpx; color: #004ac6; font-size: 76rpx; font-weight: 800; letter-spacing: 2rpx; }
.voucher-meta { margin-top: 8rpx; color: #434655; font-size: 24rpx; }
.hash-row { display: flex; align-items: center; gap: 18rpx; margin-top: 22rpx; padding: 14rpx 20rpx; border-radius: 10rpx; background: #f5f7fb; }
.hash { color: #434655; font-size: 21rpx; }
.hash-note { color: #9ca3af; font-size: 20rpx; }
.notice { background: #f5f7fb; }
.notice-text { color: #434655; font-size: 24rpx; line-height: 1.6; }
.notify-card { display: flex; align-items: center; justify-content: space-between; background: #eff4ff; border-color: #cbdbf6; }
.notify-head { display: flex; flex-direction: column; }
.notify-title { color: #111827; font-size: 27rpx; font-weight: 750; }
.notify-sub { margin-top: 8rpx; color: #434655; font-size: 22rpx; }
.notify-button { box-sizing: border-box; width: 150rpx; height: 64rpx; line-height: 64rpx; margin: 0; padding: 0; border-radius: 999rpx; background: #004ac6; color: #fff; font-size: 24rpx; }
.bottom-bar { position: fixed; left: 0; right: 0; bottom: 0; box-sizing: border-box; display: flex; gap: 20rpx; padding: 20rpx 36rpx calc(20rpx + env(safe-area-inset-bottom)); background: #fff; border-top: 1rpx solid #e5e7eb; }
.bar-button { flex: 1; height: 84rpx; line-height: 84rpx; border-radius: 14rpx; font-size: 28rpx; font-weight: 700; }
.bar-button.primary { background: #004ac6; color: #fff; }
.bar-button.ghost { border: 1rpx solid #004ac6; background: #fff; color: #004ac6; }
</style>
