<template>
  <div class="login-wrap">
    <div class="card login-card">
      <div class="login-logo">📄 PaperGuard</div>
      <p class="login-sub">{{ forceChange ? '首次登录请设置新密码' : (mode === 'login' ? '论文格式全流程约束系统' : '创建新账号') }}</p>

      <div v-if="error" :class="noticeType === 'success' ? 'notice-banner' : 'error-banner'">
        {{ noticeType === 'success' ? '✅' : '⚠' }} {{ error }}
      </div>

      <form @submit.prevent="submit">
        <div v-if="mode === 'register'" class="role-selector">
          <label class="role-pill" :class="{ active: role === 'student' }">
            <input type="radio" v-model="role" value="student" />
            <span>👨‍🎓 学生账号</span>
          </label>
          <label class="role-pill" :class="{ active: role === 'teacher' }">
            <input type="radio" v-model="role" value="teacher" />
            <span>👨‍🏫 教师账号 (需认证)</span>
          </label>
        </div>

        <label class="field">
          <span>用户名</span>
          <input v-model="username" autocomplete="username" placeholder="至少 3 个字符" />
        </label>
        <label v-if="mode === 'register'" class="field">
          <span>姓名 / 昵称</span>
          <input v-model="displayName" placeholder="例如：李老师 / 张三同学" />
        </label>
        <label class="field">
          <span>密码</span>
          <input v-model="password" type="password" autocomplete="current-password" :placeholder="mode === 'login' ? '密码' : '至少 8 位，含字母和数字'" />
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
          {{ loading ? '请稍候…' : (mode === 'login' ? '登 录' : (role === 'teacher' ? '注册教师账号 (提交认证)' : '注 册 学 生 账 号')) }}
        </button>
      </form>

      <div v-if="!forceChange" class="login-switch">
        <template v-if="mode === 'login'">
          还没有账号?
          <a href="#" @click.prevent="switchMode('register')">立即注册</a>
        </template>
        <template v-else>
          已有账号?
          <a href="#" @click.prevent="switchMode('login')">去登录</a>
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
const role = ref('student')
const forceChange = computed(() => route.query.forceChange === '1' && !!auth.token)
const username = ref('')
const password = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const displayName = ref('')
const error = ref('')
const noticeType = ref('error')
const loading = ref(false)

function switchMode(m) {
  mode.value = m
  error.value = ''
  noticeType.value = 'error'
}

function setNotice(message, type = 'error') {
  error.value = message
  noticeType.value = type
}

async function submit() {
  error.value = ''
  loading.value = true
  try {
    if (forceChange.value) {
      if (newPassword.value !== confirmPassword.value) throw new Error('两次输入的新密码不一致')
      await changePassword(password.value, newPassword.value)
      clearAuth()
      router.replace('/login')
      setNotice('密码已更新, 请使用新密码重新登录', 'success')
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
          username: username.value,
          password: password.value,
          display_name: displayName.value,
          role: role.value,
        }),
      })
      if (!resp.ok) {
        const d = await resp.json().catch(() => ({}))
        throw new Error(d.detail || '注册失败')
      }
      mode.value = 'login'
      if (role.value === 'teacher') {
        setNotice('教师账号注册成功！需管理员认证后开通教师管理权限，您可先登录体验。', 'success')
      } else {
        setNotice('注册成功, 请登录', 'success')
      }
    }
  } catch (e) {
    setNotice(e.message)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  display: flex;
  justify-content: center;
  padding-top: 6vh;
}
.login-card {
  width: min(420px, 100%);
  text-align: center;
  padding: 36px 32px;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  border: 1px solid var(--slate-200);
}
.login-logo {
  font-size: 26px;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: var(--primary);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.login-sub {
  color: var(--slate-500);
  font-size: 14px;
  margin: 6px 0 24px;
}
.field { display: block; text-align: left; margin-bottom: 14px; }
.field span { display: block; font-size: 13px; color: var(--muted); margin-bottom: 6px; }
.field input {
  width: 100%; padding: 10px 12px; border: 1px solid var(--border);
  border-radius: 8px; font-size: 14px;
}
.notice-banner {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #15803d;
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 16px;
}
.login-switch { margin-top: 16px; font-size: 14px; color: var(--muted); }

.login-switch a { color: var(--primary); text-decoration: none; }
.login-hint { margin-top: 14px; font-size: 12px; color: #94a3b8; }

.role-selector {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  background: #f1f5f9;
  padding: 4px;
  border-radius: 10px;
}
.role-pill {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--muted);
  cursor: pointer;
  transition: all 0.2s;
}
.role-pill input {
  display: none;
}
.role-pill.active {
  background: #fff;
  color: var(--primary);
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  font-weight: 600;
}
</style>
