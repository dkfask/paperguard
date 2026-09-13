<template>
  <div class="stitch-auth-page">
    <!-- Top Bar with Logo and Language Switcher -->
    <header class="stitch-auth-header">
      <div class="auth-header-inner">
        <div class="brand-box">
          <img alt="PaperGuard Logo" class="brand-icon-img" src="/logo.png" />
          <span class="brand-name">PaperGuard</span>
        </div>
        <button class="auth-lang-btn" type="button" :title="t('shell.switchLanguage')" @click="toggleLang">
          <span class="material-symbols-outlined text-[16px]">translate</span>
          <span>{{ lang === 'zh' ? t('shell.switchToEnglish') : t('shell.switchToChinese') }}</span>
        </button>
      </div>
    </header>

    <!-- Main Authentication Stage -->
    <main class="stitch-auth-main">
      <div class="auth-heading-box">
        <div class="login-center-logo">
          <img alt="PaperGuard Logo" src="/logo.png" />
        </div>
        <h1 class="auth-title">
          {{ forceChange ? t('auth.setPasswordTitle') : t('auth.welcomeTitle') }}
        </h1>
        <p class="auth-sub">
          {{ forceChange ? t('auth.setPasswordSub') : t('auth.loginSub') }}
        </p>
      </div>

      <!-- Stitch Glassmorphic Card -->
      <div class="stitch-auth-card">
        <div v-if="error" :class="noticeType === 'success' ? 'ok-banner' : 'error-banner'" style="margin-bottom: 16px">
          <span class="material-symbols-outlined text-[16px]">{{ noticeType === 'success' ? 'check_circle' : 'warning' }}</span>
          <span>{{ error }}</span>
        </div>

        <form class="auth-form" @submit.prevent="submit">
          <div class="form-field">
            <label for="username">{{ t('auth.usernameLabel') }}</label>
            <input id="username" v-model="username" autocomplete="username" :placeholder="t('auth.usernamePlaceholder')" required />
          </div>

          <div class="form-field">
            <label for="password">{{ t('auth.passwordLabel') }}</label>
            <input id="password" v-model="password" type="password" autocomplete="current-password" :placeholder="t('auth.passwordPlaceholder')" required />
          </div>

          <div v-if="forceChange" class="form-field">
            <label for="newPassword">{{ t('auth.newPasswordLabel') }}</label>
            <input id="newPassword" v-model="newPassword" type="password" autocomplete="new-password" placeholder="••••••••••••" required />
          </div>

          <div v-if="forceChange" class="form-field">
            <label for="confirmPassword">{{ t('auth.confirmPasswordLabel') }}</label>
            <input id="confirmPassword" v-model="confirmPassword" type="password" autocomplete="new-password" placeholder="••••••••••••" required />
          </div>

          <button class="primary auth-submit-btn" type="submit" :disabled="loading">
            <span>{{ loading ? t('auth.processing') : t('auth.signIn') }}</span>
            <span class="material-symbols-outlined text-[16px]">arrow_forward</span>
          </button>
        </form>

        <div v-if="!forceChange" class="auth-switch-footer">
          <span>{{ t('auth.noAccount') }}</span>
          <router-link to="/register">{{ t('auth.createAccount') }}</router-link>
        </div>
      </div>

      <!-- Stitch Compliance Reassurance Footer -->
      <div class="stitch-compliance-footer">
        <span class="material-symbols-outlined text-[20px] text-secondary">verified_user</span>
        <p class="compliance-text">
          {{ t('auth.compliance') }}
        </p>
      </div>
    </main>

    <!-- Global Subtle Footer -->
    <footer class="stitch-auth-bottom-footer">
      <div>PaperGuard © 2026. All rights reserved.</div>
      <div class="footer-links">
        <span>{{ t('brandTagline') }}</span>
        <span>·</span>
        <span>GB/T 7713-1987 / 2006</span>
      </div>
    </footer>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { setAuth, clearAuth, auth } from '../auth'
import { authApi } from '../features/auth/api'
import { useI18n } from '../i18n'

const route = useRoute()
const router = useRouter()
const { lang, setLang, t } = useI18n()

function toggleLang() {
  setLang(lang.value === 'zh' ? 'en' : 'zh')
}

const forceChange = computed(() => route.query.forceChange === '1' && !!auth.token)
const username = ref('')
const password = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const error = ref('')
const noticeType = ref('error')
const loading = ref(false)


function setNotice(message, type = 'error') {
  error.value = message
  noticeType.value = type
}

async function submit() {
  error.value = ''
  loading.value = true
  try {
    if (forceChange.value) {
      if (newPassword.value !== confirmPassword.value) throw new Error(t('auth.passwordMismatch'))
      await authApi.changePassword(password.value, newPassword.value)
      clearAuth()
      router.replace('/login')
      setNotice(t('auth.passwordUpdated'), 'success')
      return
    }
    {
      const data = await authApi.login({ username: username.value, password: password.value })
      setAuth(data.token, data.user)
      const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/')
        ? route.query.redirect : '/'
      if (data.user.must_change_password) {
        router.replace({ path: '/login', query: { forceChange: '1', redirect } })
      } else {
        router.replace(redirect)
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
.stitch-auth-page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--background);
  color: var(--on-surface);
}

/* 顶部轻量 Bar */
.stitch-auth-header {
  height: 60px;
  padding: 0 32px;
  display: flex;
  align-items: center;
  border-bottom: 1px solid var(--border);
  background: rgba(248, 249, 255, 0.85);
  backdrop-filter: blur(12px);
}

.auth-header-inner {
  max-width: 1080px;
  width: 100%;
  margin: 0 auto;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.brand-box {
  display: flex;
  align-items: center;
  gap: 8px;
}

.brand-icon-img {
  height: 28px;
  width: auto;
}

.brand-name {
  font-family: "Geist", sans-serif;
  font-size: 18px;
  font-weight: 700;
  color: var(--primary);
}

.auth-lang-btn {
  background: transparent;
  border: 1px solid var(--border);
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--on-surface-variant);
  cursor: pointer;
}

.auth-lang-btn:hover {
  background: var(--surface-container-low);
  color: var(--primary);
}

/* 主画布 */
.stitch-auth-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: 40px 16px;
  width: 100%;
  max-width: 460px;
  margin: 0 auto;
}

.auth-heading-box {
  text-align: center;
  margin-bottom: 20px;
}

.login-center-logo {
  display: flex;
  justify-content: center;
  margin-bottom: 12px;
}

.login-center-logo img {
  height: 44px;
  width: auto;
  filter: drop-shadow(0 4px 10px rgba(37, 99, 235, 0.2));
}

.auth-title {
  font-family: "Source Serif 4", serif;
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.01em;
  margin-bottom: 6px;
  color: var(--on-surface);
}

.auth-sub {
  font-family: "Geist", sans-serif;
  font-size: 13px;
  color: var(--secondary);
  line-height: 1.5;
}

/* Stitch 玻璃卡片 */
.stitch-auth-card {
  width: 100%;
  background: var(--surface-container-lowest);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  padding: 28px 28px 24px;
  box-shadow: 0 4px 20px -2px rgba(15, 23, 42, 0.08);
}

/* 角色胶囊 */
.stitch-role-tabs {
  display: flex;
  background: var(--surface-container-low);
  border-radius: var(--radius-pill);
  padding: 3px;
  margin-bottom: 20px;
  border: 1px solid var(--border);
}

.role-tab-btn {
  flex: 1;
  border: none;
  background: transparent;
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 500;
  color: var(--secondary);
  border-radius: var(--radius-pill);
  box-shadow: none;
}

.role-tab-btn.active {
  background: #ffffff;
  color: var(--primary);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

/* 表单字段 */
.auth-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  text-align: left;
}

.form-field label {
  font-size: 12px;
  font-weight: 600;
  color: var(--on-surface);
}

.form-field input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 13px;
  background: #ffffff;
  transition: all 0.15s ease;
}

.form-field input:focus {
  border-color: var(--primary-container);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.15);
}

.auth-submit-btn {
  width: 100%;
  margin-top: 8px;
  padding: 9px 16px;
  font-size: 14px;
  font-weight: 600;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.auth-switch-footer {
  margin-top: 18px;
  font-size: 13px;
  color: var(--secondary);
  text-align: center;
  display: flex;
  justify-content: center;
  gap: 6px;
}

.auth-switch-footer a {
  color: var(--primary);
  text-decoration: none;
  font-weight: 600;
}

.auth-switch-footer a:hover {
  text-decoration: underline;
}

/* 底部合规背书 */
.stitch-compliance-footer {
  margin-top: 24px;
  display: flex;
  align-items: center;
  gap: 8px;
  text-align: center;
}

.compliance-text {
  font-family: "JetBrains Mono", monospace;
  font-size: 11px;
  color: var(--secondary);
}

.stitch-auth-bottom-footer {
  padding: 16px 24px;
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--secondary);
  background: var(--surface-container-lowest);
}

.footer-links {
  display: flex;
  gap: 8px;
}

@media (max-width: 600px) {
  .stitch-auth-bottom-footer {
    flex-direction: column;
    align-items: center;
    gap: 6px;
  }
}

.email-row {
  display: flex;
  gap: 8px;
}

.email-row input:first-child {
  flex: 1;
}

.code-button {
  box-sizing: border-box;
  height: 42px;
  padding: 0 12px;
  border: 1px solid var(--primary, #004ac6);
  border-radius: 8px;
  background: #fff;
  color: var(--primary, #004ac6);
  font-size: 13px;
  white-space: nowrap;
  cursor: pointer;
}

.code-button:disabled {
  border-color: #cbd5e1;
  color: #94a3b8;
  cursor: not-allowed;
}
</style>
