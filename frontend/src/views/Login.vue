<template>
  <div class="login-wrap">
    <div class="card login-card">
      <div class="login-logo">📄 PaperGuard</div>
      <p class="login-sub">{{ forceChange ? '首次登录请设置新密码' : (mode === 'login' ? '论文格式全流程约束系统' : '注册学生账号') }}</p>

      <div v-if="error" class="error-banner">⚠ {{ error }}</div>

      <form @submit.prevent="submit">
        <label class="field">
          <span>用户名</span>
          <input v-model="username" autocomplete="username" placeholder="至少 3 个字符" />
        </label>
        <label class="field">
          <span>密码</span>
          <input v-model="password" type="password" autocomplete="current-password" :placeholder="mode === 'login' ? '密码' : '至少 6 位'" />
        </label>
        <label v-if="forceChange" class="field">
          <span>新密码</span>
          <input v-model="newPassword" type="password" autocomplete="new-password" placeholder="至少 12 位, 含字母和数字" />
        </label>
        <label v-if="forceChange" class="field">
          <span>确认新密码</span>
          <input v-model="confirmPassword" type="password" autocomplete="new-password" placeholder="再次输入新密码" />
        </label>
        <button class="primary" type="submit" :disabled="loading" style="width: 100%; margin-top: 8px">
          {{ loading ? '请稍候…' : (mode === 'login' ? '登 录' : '注 册') }}
        </button>
      </form>

      <div v-if="!forceChange" class="login-switch">
        <template v-if="mode === 'login'">
          还没有账号?
          <a href="#" @click.prevent="mode = 'register'">注册学生账号</a>
        </template>
        <template v-else>
          已有账号?
          <a href="#" @click.prevent="mode = 'login'">去登录</a>
        </template>
      </div>

    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { setAuth, clearAuth, auth } from '../auth'
import { changePassword } from '../api'

const route = useRoute()
const router = useRouter()
const mode = ref('login')
const forceChange = computed(() => route.query.forceChange === '1' && !!auth.token)
const username = ref('')
const password = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const displayName = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    if (forceChange.value) {
      if (newPassword.value !== confirmPassword.value) throw new Error('两次输入的新密码不一致')
      await changePassword(password.value, newPassword.value)
      clearAuth()
      router.replace('/login')
      error.value = '密码已更新, 请使用新密码重新登录'
      return
    }
    if (mode.value === 'login') {
      const resp = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.value, password: password.value }),
      })
      if (!resp.ok) {
        const d = await resp.json().catch(() => ({}))
        throw new Error(d.detail || '登录失败')
      }
      const data = await resp.json()
      setAuth(data.token, data.user)
      const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/')
        ? route.query.redirect : '/'
      if (data.user.must_change_password) {
        router.replace({ path: '/login', query: { forceChange: '1', redirect } })
      } else {
        router.replace(redirect)
      }
    } else {
      const resp = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: username.value, password: password.value, display_name: displayName.value,
        }),
      })
      if (!resp.ok) {
        const d = await resp.json().catch(() => ({}))
        throw new Error(d.detail || '注册失败')
      }
      mode.value = 'login'
      error.value = '注册成功, 请登录'
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap { display: flex; justify-content: center; padding-top: 8vh; }
.login-card { width: min(380px, 100%); text-align: center; padding: 32px; }
.login-logo { font-size: 24px; font-weight: 700; color: var(--primary); }
.login-sub { color: var(--muted); font-size: 14px; margin: 8px 0 20px; }
.field { display: block; text-align: left; margin-bottom: 14px; }
.field span { display: block; font-size: 13px; color: var(--muted); margin-bottom: 6px; }
.field input {
  width: 100%; padding: 10px 12px; border: 1px solid var(--border);
  border-radius: 8px; font-size: 14px;
}
.login-switch { margin-top: 16px; font-size: 14px; color: var(--muted); }
.login-switch a { color: var(--primary); text-decoration: none; }
.login-hint { margin-top: 14px; font-size: 12px; color: #94a3b8; }
</style>
