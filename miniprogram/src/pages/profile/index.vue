<template>
  <view class="page">
    <text class="eyebrow">ACCOUNT</text>
    <text class="title">我的</text>
    <view v-if="user" class="panel identity"><view class="avatar">{{ (user.display_name || user.username).slice(0, 1) }}</view><view><text class="name">{{ user.display_name || user.username }}</text><text class="muted">{{ user.username }} · {{ roleLabel }}</text></view></view>
    <view class="panel">
      <text class="section-title">账号安全</text>
      <input v-model="currentPassword" class="field" password placeholder="当前密码" />
      <input v-model="newPassword" class="field" password placeholder="新密码（至少 12 位，含字母和数字）" />
      <button class="secondary-button" :loading="loading" @tap="updatePassword">更新密码</button>
      <button class="danger-button" :loading="loading" @tap="unbind">解绑微信</button>
    </view>
    <button class="logout-button" @tap="logout">退出小程序账号</button>
    <text v-if="message" class="message" :class="{ error: isError }">{{ message }}</text>
  </view>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { changePassword, describeError, fetchMe, mpUnbind } from '../../api/request'
import { useSessionStore } from '../../stores/session'
import { ensureSessionReady } from '../../utils/session-guard'

const session = useSessionStore()
const user = computed(() => session.user)
const roleLabel = computed(() => session.user?.role === 'teacher' ? '指导教师' : '学生')
const currentPassword = ref('')
const newPassword = ref('')
const loading = ref(false)
const message = ref('')
const isError = ref(false)

onShow(() => {
  if (!ensureSessionReady()) return
  void refresh()
})

async function refresh() {
  try {
    session.user = await fetchMe()
  } catch (error) {
    showError(error)
  }
}

async function updatePassword() {
  if (!currentPassword.value || !newPassword.value) {
    showError(new Error('请填写当前密码和新密码'))
    return
  }
  loading.value = true
  try {
    await changePassword(currentPassword.value, newPassword.value)
    session.clear()
    uni.redirectTo({ url: '/pages/login/index' })
  } catch (error) {
    showError(error)
  } finally {
    loading.value = false
  }
}

function unbind() {
  uni.showModal({
    title: '确认解绑微信？',
    content: '解绑后需要再次输入校内账号和密码才能绑定。',
    editable: true,
    placeholderText: '请输入当前密码',
    success: async (result) => {
      if (!result.confirm || !result.content) return
      loading.value = true
      try {
        await mpUnbind(result.content)
        session.clear()
        uni.redirectTo({ url: '/pages/login/index' })
      } catch (error) {
        showError(error)
      } finally {
        loading.value = false
      }
    },
  })
}

function logout() {
  session.clear()
  uni.redirectTo({ url: '/pages/login/index' })
}

function showError(error: unknown) {
  message.value = describeError(error)
  isError.value = true
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 48rpx 36rpx 120rpx; }
.eyebrow { display: block; color: #6b7280; font-size: 22rpx; letter-spacing: 3rpx; }
.title { display: block; margin-top: 8rpx; color: #111827; font-size: 50rpx; font-weight: 800; }
.panel { margin-top: 34rpx; padding: 30rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 20rpx; }
.identity { display: flex; align-items: center; gap: 22rpx; }
.avatar { width: 78rpx; height: 78rpx; border-radius: 50%; display: flex; align-items: center; justify-content: center; background: #e5efff; color: #004ac6; font-size: 34rpx; font-weight: 800; }
.name { display: block; color: #111827; font-size: 31rpx; font-weight: 750; }
.muted { display: block; margin-top: 8rpx; color: #6b7280; font-size: 23rpx; }
.section-title { display: block; color: #111827; font-size: 28rpx; font-weight: 750; }
.field { box-sizing: border-box; height: 86rpx; margin-top: 20rpx; padding: 0 22rpx; background: #f5f7fb; border: 1rpx solid #e5e7eb; border-radius: 12rpx; color: #111827; font-size: 24rpx; }
.secondary-button, .danger-button, .logout-button { height: 78rpx; line-height: 78rpx; margin: 24rpx 0 0; border-radius: 12rpx; font-size: 25rpx; }
.secondary-button { border: 1rpx solid #004ac6; background: #fff; color: #004ac6; }
.danger-button { border: 1rpx solid #f0b8b8; background: #fff8f8; color: #ba1a1a; }
.logout-button { border: 1rpx solid #e5e7eb; background: #fff; color: #6b7280; }
.message { display: block; margin-top: 22rpx; color: #166534; text-align: center; font-size: 24rpx; }
.message.error { color: #ba1a1a; }
</style>
