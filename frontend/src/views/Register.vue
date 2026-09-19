<template>
  <div class="stitch-auth-page">
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

    <main class="register-main">
      <div class="register-columns">
        <!-- 左栏:注册表单 -->
        <section class="register-card">
          <div class="brand-row">
            <img alt="PaperGuard Logo" class="card-logo" src="/logo.png" />
            <span class="card-brand">论文卫士Pro</span>
          </div>
          <h1 class="card-title">{{ t('auth.registerTitle') }}</h1>
          <p class="card-sub">{{ t('auth.registerSub') }}</p>

          <div v-if="error" :class="noticeType === 'success' ? 'ok-banner' : 'error-banner'">
            <span class="material-symbols-outlined text-[16px]">{{ noticeType === 'success' ? 'check_circle' : 'warning' }}</span>
            <span>{{ error }}<template v-if="requestId"> (request_id: {{ requestId }})</template></span>
          </div>

          <template v-if="!registered">
            <div class="role-tabs">
              <button type="button" class="role-tab" :class="{ active: role === 'student' }" @click="role = 'student'">
                <span class="material-symbols-outlined text-[16px]">school</span>
                <span>{{ t('auth.studentAccount') }}</span>
              </button>
              <button type="button" class="role-tab" :class="{ active: role === 'teacher' }" @click="role = 'teacher'">
                <span class="material-symbols-outlined text-[16px]">psychology</span>
                <span>{{ t('auth.teacherAccount') }}</span>
              </button>
            </div>

            <form class="register-form" @submit.prevent="submit">
              <div class="form-field">
                <label for="reg-email">{{ t('auth.emailLabel') }}</label>
                <div class="email-row">
                  <input id="reg-email" v-model="email" type="email" autocomplete="email" :placeholder="t('auth.emailPlaceholder')" required />
                  <button type="button" class="code-button" :disabled="codeCooldown > 0 || !email" @click="sendCode">
                    {{ codeCooldown > 0 ? `${codeCooldown}s` : t('auth.sendCode') }}
                  </button>
                </div>
                <input v-model="emailCode" class="mt8" inputmode="numeric" maxlength="6" :placeholder="t('auth.emailCodePlaceholder')" required />
              </div>

              <div class="form-field">
                <label for="reg-username">{{ t('auth.usernameLabel') }}</label>
                <input id="reg-username" v-model="username" autocomplete="username" :placeholder="t('auth.usernamePlaceholder')" />
                <p class="field-hint">{{ t('auth.usernameOptionalHint') }}</p>
              </div>

              <div class="form-field">
                <label for="reg-name">{{ t('auth.fullNameLabel') }}</label>
                <input id="reg-name" v-model="displayName" :placeholder="t('auth.fullNamePlaceholder')" />
              </div>

              <div class="form-field">
                <label for="reg-password">{{ t('auth.passwordLabel') }}</label>
                <input id="reg-password" v-model="password" type="password" autocomplete="new-password" :placeholder="t('auth.passwordPlaceholder')" />
                <p class="field-hint">{{ t('auth.passwordRule') }}</p>
              </div>

              <div class="form-field">
                <label for="reg-confirm">{{ t('auth.confirmPasswordLabel') }}</label>
                <input id="reg-confirm" v-model="confirmPassword" type="password" autocomplete="new-password" :placeholder="t('auth.confirmPasswordLabel')" />
              </div>

              <label class="agree-row">
                <input v-model="agree" type="checkbox" />
                <span>{{ t('auth.agreeTerms') }}</span>
              </label>

              <button class="submit-button" type="submit" :disabled="loading">
                <span>{{ loading ? t('auth.processing') : (role === 'teacher' ? t('auth.registerTeacher') : t('auth.registerStudent')) }}</span>
                <span class="material-symbols-outlined text-[16px]">arrow_forward</span>
              </button>
            </form>
          </template>

          <template v-else>
            <div class="success-box">
              <span class="material-symbols-outlined success-icon">check_circle</span>
              <p class="success-title">{{ t('auth.registrationSuccess') }}</p>
              <p v-if="registeredRole === 'teacher'" class="success-sub">{{ t('auth.teacherRegistered') }}</p>
              <button class="submit-button" type="button" @click="goLogin">{{ t('auth.backToLogin') }}</button>
            </div>
          </template>

          <p class="login-link">
            {{ t('auth.hasAccount') }}
            <router-link to="/login">{{ t('auth.backToLogin') }}</router-link>
          </p>
        </section>

        <!-- 右栏:品牌与合规背书 -->
        <aside class="brand-panel">
          <p class="brand-statement">{{ t('register.statement') }}</p>
          <p class="brand-sub">{{ t('register.statementSub') }}</p>
          <div class="feature-card">
            <span class="material-symbols-outlined feature-icon">auto_stories</span>
            <div>
              <p class="feature-title">{{ t('register.featureTemplateTitle') }}</p>
              <p class="feature-text">{{ t('register.featureTemplateText') }}</p>
            </div>
          </div>
          <div class="feature-card">
            <span class="material-symbols-outlined feature-icon">description</span>
            <div>
              <p class="feature-title">{{ t('register.featureExportTitle') }}</p>
              <p class="feature-text">{{ t('register.featureExportText') }}</p>
            </div>
          </div>
          <div class="feature-card">
            <span class="material-symbols-outlined feature-icon">history_edu</span>
            <div>
              <p class="feature-title">{{ t('register.featureAuditTitle') }}</p>
              <p class="feature-text">{{ t('register.featureAuditText') }}</p>
            </div>
          </div>
          <p class="compliance-line">🔒 {{ t('auth.compliance') }}</p>
        </aside>
      </div>
    </main>

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
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { authApi } from '../features/auth/api'
import { useI18n } from '../i18n'

const router = useRouter()
const { lang, setLang, t } = useI18n()

function toggleLang() {
  setLang(lang.value === 'zh' ? 'en' : 'zh')
}

const role = ref('student')
const email = ref('')
const emailCode = ref('')
const codeCooldown = ref(0)
const username = ref('')
const displayName = ref('')
const password = ref('')
const confirmPassword = ref('')
const agree = ref(false)
const loading = ref(false)
const error = ref('')
const noticeType = ref('error')
const requestId = ref('')
const registered = ref(false)
const registeredRole = ref('student')

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

function setNotice(message, type = 'error', reqId = '') {
  error.value = message
  noticeType.value = type
  requestId.value = reqId
}

async function sendCode() {
  if (!email.value || codeCooldown.value > 0) return
  try {
    await authApi.requestEmailCode(email.value.trim())
    setNotice(t('auth.codeSent'), 'success')
    codeCooldown.value = 60
    const timer = setInterval(() => {
      codeCooldown.value -= 1
      if (codeCooldown.value <= 0) clearInterval(timer)
    }, 1000)
  } catch (err) {
    setNotice(err?.message || '验证码发送失败', 'error', err?.requestId)
  }
}

async function submit() {
  error.value = ''
  requestId.value = ''
  if (!agree.value) {
    setNotice(t('auth.agreeTerms'))
    return
  }
  if (!email.value.trim()) {
    setNotice(t('auth.emailRequired'))
    return
  }
  if (!emailCode.value.trim()) {
    setNotice(t('auth.emailCodeRequired'))
    return
  }
  if (email.value && !EMAIL_RE.test(email.value.trim())) {
    setNotice(t('auth.emailInvalid'))
    return
  }
  if (password.value !== confirmPassword.value) {
    setNotice(t('auth.passwordMismatch'))
    return
  }
  loading.value = true
  try {
    await authApi.register({
      username: username.value.trim(),
      password: password.value,
      display_name: displayName.value,
      role: role.value,
      email: email.value.trim(),
      email_code: emailCode.value.trim(),
    })
    registeredRole.value = role.value
    registered.value = true
  } catch (err) {
    setNotice(err?.message || '注册失败', 'error', err?.requestId)
  } finally {
    loading.value = false
  }
}

function goLogin() {
  router.push('/login')
}
</script>

<style scoped>
.stitch-auth-page { min-height: 100vh; display: flex; flex-direction: column; background: var(--background); color: var(--on-surface); }
.stitch-auth-header { height: 60px; padding: 0 32px; display: flex; align-items: center; border-bottom: 1px solid var(--border); background: rgba(248, 249, 255, 0.85); backdrop-filter: blur(12px); }
.auth-header-inner { max-width: 1140px; width: 100%; margin: 0 auto; display: flex; justify-content: space-between; align-items: center; }
.brand-box { display: flex; align-items: center; gap: 8px; }
.brand-icon-img { width: 26px; height: 26px; }
.brand-name { font-weight: 800; letter-spacing: 0.2px; }
.auth-lang-btn { display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border: 1px solid var(--border); border-radius: 8px; background: #fff; color: var(--on-surface-variant); font-size: 13px; cursor: pointer; }

.register-main { flex: 1; display: flex; align-items: flex-start; justify-content: center; padding: 40px 24px; }
.register-columns { display: grid; grid-template-columns: 460px 1fr; gap: 28px; width: 100%; max-width: 1140px; align-items: stretch; }
@media (max-width: 960px) { .register-columns { grid-template-columns: 1fr; } }

.register-card { background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 28px; box-shadow: 0 4px 20px -2px rgba(15, 23, 42, 0.05); }
.brand-row { display: flex; align-items: center; gap: 8px; }
.card-logo { width: 28px; height: 28px; }
.card-brand { color: var(--on-surface-variant); font-size: 14px; font-weight: 700; }
.card-title { margin: 14px 0 0; font-size: 24px; font-weight: 800; color: var(--on-surface); }
.card-sub { margin: 8px 0 0; color: var(--on-surface-variant); font-size: 14px; }

.role-tabs { display: flex; gap: 10px; margin-top: 20px; }
.role-tab { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 6px; height: 40px; border: 1px solid var(--border); border-radius: 999px; background: #eff4ff; color: var(--on-surface-variant); font-size: 13px; cursor: pointer; }
.role-tab.active { border-color: var(--primary); background: var(--primary); color: #fff; font-weight: 700; }

.register-form { margin-top: 6px; }
.form-field { margin-top: 16px; display: flex; flex-direction: column; }
.form-field label { font-size: 13px; font-weight: 600; color: var(--on-surface); margin-bottom: 6px; }
.form-field input { height: 42px; padding: 0 12px; border: 1px solid var(--border); border-radius: 8px; background: #fff; color: var(--on-surface); font-size: 14px; }
.form-field input:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(0, 74, 198, 0.12); }
.form-field .mt8 { margin-top: 8px; }
.field-hint { margin: 6px 0 0; font-size: 12px; color: #94a3b8; }
.email-row { display: flex; gap: 8px; }
.email-row input { flex: 1; }
.code-button { height: 42px; padding: 0 12px; border: 1px solid var(--primary); border-radius: 8px; background: #fff; color: var(--primary); font-size: 13px; white-space: nowrap; cursor: pointer; }
.code-button:disabled { border-color: var(--border); color: #94a3b8; cursor: not-allowed; }

.agree-row { display: flex; align-items: flex-start; gap: 8px; margin-top: 18px; font-size: 13px; color: var(--on-surface-variant); cursor: pointer; }
.agree-row input { margin-top: 2px; }

.submit-button { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; height: 46px; margin-top: 18px; border: none; border-radius: 8px; background: var(--primary); color: #fff; font-size: 15px; font-weight: 700; cursor: pointer; }
.submit-button:disabled { opacity: 0.6; cursor: wait; }

.login-link { margin-top: 18px; font-size: 14px; color: var(--on-surface-variant); text-align: center; }
.login-link a { color: var(--primary); font-weight: 700; text-decoration: none; }
.login-link a:hover { text-decoration: underline; }

.error-banner, .ok-banner { display: flex; align-items: flex-start; gap: 8px; margin-top: 18px; padding: 12px 14px; border-radius: 8px; font-size: 13px; line-height: 1.5; }
.error-banner { background: var(--error-container, #ffdad6); color: #93000a; border: 1px solid rgba(186, 26, 26, 0.25); }
.ok-banner { background: #e8f7ee; color: #166534; border: 1px solid rgba(22, 163, 74, 0.25); }

.success-box { margin-top: 20px; display: flex; flex-direction: column; align-items: center; text-align: center; padding: 20px 0; }
.success-icon { font-size: 52px; color: #16a34a; }
.success-title { margin: 14px 0 0; font-size: 18px; font-weight: 800; color: var(--on-surface); }
.success-sub { margin: 10px 0 0; color: var(--on-surface-variant); font-size: 14px; line-height: 1.6; }

.brand-panel { display: flex; flex-direction: column; gap: 16px; padding: 32px; border: 1px solid var(--border); border-radius: 12px; background: linear-gradient(160deg, #eff4ff 0%, rgba(203, 219, 246, 0.4) 100%); }
.brand-statement { margin: 0; font-family: Newsreader, 'Source Serif 4', serif; font-size: 30px; font-weight: 700; line-height: 1.35; color: var(--on-surface); }
.brand-sub { margin: 0; color: var(--on-surface-variant); font-size: 15px; line-height: 1.7; }
.feature-card { display: flex; gap: 14px; padding: 16px 18px; background: #fff; border: 1px solid var(--border); border-radius: 12px; }
.feature-icon { color: var(--primary); font-size: 26px; }
.feature-title { margin: 0; font-size: 15px; font-weight: 750; color: var(--on-surface); }
.feature-text { margin: 6px 0 0; font-size: 13px; line-height: 1.6; color: var(--on-surface-variant); }
.compliance-line { margin: auto 0 0; padding-top: 12px; color: var(--on-surface-variant); font-size: 12.5px; }

.stitch-auth-bottom-footer { padding: 18px 32px; border-top: 1px solid var(--border); display: flex; justify-content: space-between; gap: 12px; color: var(--on-surface-variant); font-size: 13px; }
.footer-links { display: flex; align-items: center; gap: 6px; }
</style>
