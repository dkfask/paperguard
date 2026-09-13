<template>
  <view class="page auth-page">
    <view class="auth-mark">PG</view>
    <text class="eyebrow">PAPERGUARD</text>
    <text class="title">论文过程工作台</text>
    <text class="subtitle">使用微信登录后绑定校内账号</text>

    <view v-if="!bindTicket" class="panel intro-panel">
      <text class="panel-title">微信安全登录</text>
      <text class="panel-copy">小程序只保存会话凭证，不会采集手机号。未绑定账号的微信会进入校内账号绑定流程。</text>
      <button class="primary-button" :loading="loading" @tap="startWechatLogin">微信登录</button>
    </view>

    <view v-else class="panel bind-panel">
      <text class="panel-title">绑定校内账号</text>
      <text class="panel-copy">绑定后可查看任务、消息和审核反馈。绑定票据仅短时有效。</text>
      <input v-model="username" class="field" placeholder="校内账号" maxlength="64" />
      <input v-model="password" class="field" password placeholder="账号密码" maxlength="128" />
      <button class="primary-button" :loading="loading" @tap="bindAccount">确认绑定</button>
      <button class="text-button" @tap="resetBinding">重新获取微信登录凭证</button>
    </view>

    <text v-if="errorMessage" class="error-text">{{ errorMessage }}</text>
    <text class="legal-copy">继续即表示同意 PaperGuard 隐私说明与校内账号使用规则</text>
  </view>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { mpBind, mpLogin, ApiError } from '../../api/request'
import { useSessionStore } from '../../stores/session'

const session = useSessionStore()
const loading = ref(false)
const bindTicket = ref(session.bindTicket)
const username = ref('')
const password = ref('')
const errorMessage = ref('')

onLoad(() => {
  if (session.isAuthenticated) {
    uni.switchTab({ url: '/pages/tasks/index' })
  }
})

function showError(error: unknown) {
  errorMessage.value = error instanceof ApiError ? error.message : '请求失败，请稍后重试'
}

async function startWechatLogin() {
  loading.value = true
  errorMessage.value = ''
  try {
    const login = await new Promise<{ code: string }>((resolve, reject) => {
      uni.login({
        provider: 'weixin',
        success: (result) => resolve({ code: result.code }),
        fail: reject,
      })
    })
    const result = await mpLogin(login.code)
    if (result.bound && result.token && result.user) {
      session.setAuth(result.token, result.user)
      uni.switchTab({ url: '/pages/tasks/index' })
      return
    }
    bindTicket.value = result.bind_ticket || ''
    session.setBindTicket(bindTicket.value)
  } catch (error) {
    showError(error)
  } finally {
    loading.value = false
  }
}

async function bindAccount() {
  if (!username.value.trim() || !password.value) {
    errorMessage.value = '请输入校内账号和密码'
    return
  }
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await mpBind(bindTicket.value, username.value.trim(), password.value)
    if (!result.token || !result.user) throw new Error('绑定响应缺少会话')
    session.setAuth(result.token, result.user)
    uni.switchTab({ url: '/pages/tasks/index' })
  } catch (error) {
    showError(error)
  } finally {
    loading.value = false
  }
}

function resetBinding() {
  session.setBindTicket('')
  bindTicket.value = ''
  username.value = ''
  password.value = ''
  errorMessage.value = ''
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 132rpx 48rpx 56rpx; }
.auth-page { display: flex; flex-direction: column; background: #f5f7fb; }
.auth-mark { width: 82rpx; height: 82rpx; border-radius: 24rpx; background: #004ac6; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 28rpx; font-weight: 800; letter-spacing: 2rpx; }
.eyebrow { margin-top: 48rpx; color: #004ac6; font-size: 22rpx; font-weight: 800; letter-spacing: 4rpx; }
.title { margin-top: 18rpx; color: #111827; font-size: 54rpx; line-height: 1.2; font-weight: 800; }
.subtitle { margin-top: 18rpx; color: #6b7280; font-size: 27rpx; }
.panel { margin-top: 64rpx; padding: 38rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 24rpx; box-shadow: 0 14rpx 40rpx rgba(17, 24, 39, .06); }
.panel-title { display: block; color: #111827; font-size: 34rpx; font-weight: 750; }
.panel-copy { display: block; margin-top: 18rpx; color: #6b7280; font-size: 25rpx; line-height: 1.65; }
.field { box-sizing: border-box; height: 92rpx; margin-top: 22rpx; padding: 0 24rpx; background: #f5f7fb; border: 1rpx solid #e5e7eb; border-radius: 14rpx; color: #111827; font-size: 27rpx; }
.primary-button { margin: 34rpx 0 0; height: 88rpx; line-height: 88rpx; border-radius: 14rpx; background: #004ac6; color: #fff; font-size: 29rpx; font-weight: 700; }
.text-button { margin-top: 18rpx; background: transparent; color: #004ac6; font-size: 25rpx; }
.error-text { margin-top: 24rpx; color: #ba1a1a; font-size: 25rpx; line-height: 1.5; }
.legal-copy { margin-top: auto; padding-top: 80rpx; color: #9ca3af; text-align: center; font-size: 21rpx; line-height: 1.6; }
</style>
