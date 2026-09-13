<template>
  <view class="page">
    <view class="page-header"><view><text class="eyebrow">INBOX</text><text class="title">消息</text></view><text class="count">{{ unread }} 条未读</text></view>
    <view v-if="loading" class="panel"><text>正在加载消息…</text></view>
    <view v-else-if="errorMessage" class="panel"><text class="error-text">{{ errorMessage }}</text><button class="secondary-button" @tap="loadMessages">重新加载</button></view>
    <view v-else-if="items.length === 0" class="panel empty"><text class="empty-title">暂无消息</text><text class="muted">阶段发布、审核反馈和截止提醒会显示在这里。</text></view>
    <view v-else>
      <view v-for="item in items" :key="String(item.id)" class="panel message" :class="{ unread: !item.read_at }" @tap="read(item)">
        <view class="message-head"><text class="message-type">{{ String(item.event_type || '系统消息') }}</text><text class="muted">{{ String(item.created_at || '') }}</text></view>
        <text class="message-body">{{ String(item.message || item.title || '你有一条新的 PaperGuard 消息') }}</text>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { describeError, fetchNotifications, markNotificationRead } from '../../api/request'
import { useSessionStore } from '../../stores/session'
import { ensureSessionReady } from '../../utils/session-guard'

const session = useSessionStore()
const items = ref<Array<Record<string, unknown>>>([])
const unread = ref(0)
const loading = ref(false)
const errorMessage = ref('')

onShow(() => {
  if (!ensureSessionReady()) return
  void loadMessages()
})

async function loadMessages() {
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await fetchNotifications()
    items.value = result.items
    unread.value = result.unread
  } catch (error) {
    errorMessage.value = describeError(error)
  } finally {
    loading.value = false
  }
}

async function read(item: Record<string, unknown>) {
  if (item.read_at || !item.id) return
  try {
    await markNotificationRead(String(item.id))
    item.read_at = new Date().toISOString()
    unread.value = Math.max(0, unread.value - 1)
  } catch (error) {
    errorMessage.value = describeError(error)
  }
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 48rpx 36rpx 120rpx; }
.page-header { display: flex; align-items: flex-end; justify-content: space-between; }
.eyebrow { display: block; color: #6b7280; font-size: 22rpx; letter-spacing: 3rpx; }
.title { display: block; margin-top: 8rpx; color: #111827; font-size: 50rpx; font-weight: 800; }
.count { color: #004ac6; font-size: 24rpx; }
.panel { margin-top: 24rpx; padding: 30rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 20rpx; }
.empty { padding: 70rpx 30rpx; }
.empty-title { display: block; color: #111827; font-size: 30rpx; font-weight: 750; }
.muted { color: #6b7280; font-size: 22rpx; }
.message { border-left: 6rpx solid transparent; }
.message.unread { border-left-color: #004ac6; background: #fbfdff; }
.message-head { display: flex; justify-content: space-between; align-items: center; }
.message-type { color: #004ac6; font-size: 24rpx; font-weight: 700; }
.message-body { display: block; margin-top: 18rpx; color: #111827; font-size: 27rpx; line-height: 1.5; }
.error-text { color: #ba1a1a; font-size: 24rpx; }
.secondary-button { margin: 28rpx 0 0; height: 76rpx; line-height: 76rpx; border: 1rpx solid #004ac6; border-radius: 12rpx; background: #fff; color: #004ac6; font-size: 25rpx; }
</style>
