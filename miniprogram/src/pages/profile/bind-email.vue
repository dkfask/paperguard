<template>
  <view class="page">
    <view class="hero">
      <text class="hero-title">{{ first ? '绑定邮箱，完成注册' : '绑定邮箱' }}</text>
      <text class="hero-sub">绑定后可用「邮箱 + 密码」在电脑端登录；邮箱也用于账号找回。</text>
    </view>

    <view class="panel">
      <input v-model="email" class="field" type="text" placeholder="邮箱地址" maxlength="254" />
      <view class="code-row">
        <input v-model="emailCode" class="field code-field" inputmode="numeric" placeholder="6 位邮箱验证码" maxlength="6" />
        <button class="code-button" :disabled="cooldown > 0 || !email" @tap="sendCode">
          {{ cooldown > 0 ? `${cooldown}s` : '发送验证码' }}
        </button>
      </view>
      <input v-model="password" class="field" password placeholder="设置桌面端登录密码（至少 12 位，含字母和数字）" maxlength="128" />
      <input v-model="confirmPassword" class="field" password placeholder="再次输入密码" maxlength="128" />
      <button class="primary-button" :loading="submitting" @tap="submit">绑定邮箱并设置密码</button>
      <text class="hint">邮箱验证成功后才能进入论文任务；请使用本人可正常收信的邮箱。</text>
    </view>

    <text v-if="message" class="message">{{ message }}</text>
  </view>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { bindMpEmail, completeFirstLogin, describeError, sendEmailCode } from '../../api/request'
import { useSessionStore } from '../../stores/session'

const session = useSessionStore()
const first = ref(false)
const email = ref('')
const emailCode = ref('')
const password = ref('')
const confirmPassword = ref('')
const cooldown = ref(0)
const submitting = ref(false)
const message = ref('')

onLoad((params) => {
  first.value = (params as Record<string, string>)?.first === '1'
  if (!session.isAuthenticated) {
    uni.reLaunch({ url: '/pages/login/index' })
  }
})

async function sendCode() {
  if (!email.value || cooldown.value > 0) return
  try {
    await sendEmailCode(email.value.trim(), first.value ? 'first_login' : 'bind')
    message.value = '验证码已发送，请查收邮箱'
    cooldown.value = 60
    const timer = setInterval(() => {
      cooldown.value -= 1
      if (cooldown.value <= 0) clearInterval(timer)
    }, 1000)
  } catch (error) {
    message.value = describeError(error)
  }
}

async function submit() {
  if (!email.value.trim()) {
    message.value = '请填写邮箱地址'
    return
  }
  if (password.value.length < 12 || !/[a-zA-Z]/.test(password.value) || !/[0-9]/.test(password.value)) {
    message.value = '密码至少 12 位，且需同时包含字母和数字'
    return
  }
  if (password.value !== confirmPassword.value) {
    message.value = '两次输入的密码不一致'
    return
  }
  submitting.value = true
  try {
    const result = first.value
      ? await completeFirstLogin(email.value.trim(), emailCode.value.trim(), password.value)
      : await bindMpEmail(email.value.trim(), emailCode.value.trim(), password.value)
    if (result.token) session.updateAuth(result.token, result.user)
    else session.updateUser(result.user)
    uni.showToast({ title: '邮箱绑定成功', icon: 'none' })
    setTimeout(() => uni.switchTab({ url: '/pages/tasks/index' }), 600)
  } catch (error) {
    message.value = describeError(error)
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; padding: 96rpx 48rpx 60rpx; background: #f5f7fb; }
.hero { display: flex; flex-direction: column; }
.hero-title { color: #111827; font-size: 46rpx; font-weight: 800; }
.hero-sub { margin-top: 16rpx; color: #6b7280; font-size: 25rpx; line-height: 1.6; }
.panel { margin-top: 40rpx; padding: 34rpx; background: #fff; border: 1rpx solid #e5e7eb; border-radius: 24rpx; box-shadow: 0 14rpx 40rpx rgba(17, 24, 39, .06); }
.field { box-sizing: border-box; height: 92rpx; margin-top: 22rpx; padding: 0 24rpx; background: #f5f7fb; border: 1rpx solid #e5e7eb; border-radius: 14rpx; color: #111827; font-size: 27rpx; }
.code-row { display: flex; gap: 16rpx; align-items: center; }
.code-field { flex: 1; }
.code-button { box-sizing: border-box; height: 92rpx; line-height: 92rpx; margin-top: 22rpx; padding: 0 24rpx; border: 1rpx solid #004ac6; border-radius: 14rpx; background: #fff; color: #004ac6; font-size: 24rpx; }
.code-button[disabled] { border-color: #e5e7eb; color: #9ca3af; }
.primary-button { margin: 34rpx 0 0; height: 88rpx; line-height: 88rpx; border-radius: 14rpx; background: #004ac6; color: #fff; font-size: 29rpx; font-weight: 700; }
.hint { display: block; margin-top: 20rpx; color: #9ca3af; font-size: 21rpx; line-height: 1.6; }
.message { display: block; margin-top: 24rpx; color: #ba1a1a; font-size: 24rpx; text-align: center; }
</style>
