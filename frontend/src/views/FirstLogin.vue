<template>
  <div class="stitch-auth-page">
    <header class="stitch-auth-header">
      <div class="auth-header-inner">
        <div class="brand-box"><img alt="" class="brand-icon-img" src="/icon-shield.png" /><span class="brand-name">PaperGuard</span></div>
      </div>
    </header>
    <main class="first-login-main">
      <section class="first-login-card">
        <div class="brand-row"><span>论文卫士Pro</span></div>
        <h1>{{ t('auth.firstLoginTitle') }}</h1>
        <p class="sub">{{ t('auth.firstLoginSub') }}</p>
        <InlineNotice v-if="message" :message="message" :type="noticeType" />
        <form @submit.prevent="submit">
          <label>{{ t('auth.emailLabel') }}<input v-model="email" type="email" autocomplete="email" :placeholder="t('auth.emailPlaceholder')" required /></label>
          <div class="code-row">
            <label class="code-field">{{ t('auth.emailCodePlaceholder') }}<input v-model="emailCode" inputmode="numeric" maxlength="6" required /></label>
            <button type="button" class="code-button" :disabled="cooldown > 0 || !email" @click="sendCode">{{ cooldown > 0 ? `${cooldown}s` : t('auth.sendCode') }}</button>
          </div>
          <label>{{ t('auth.newPasswordLabel') }}<input v-model="newPassword" type="password" autocomplete="new-password" required /></label>
          <label>{{ t('auth.confirmPasswordLabel') }}<input v-model="confirmPassword" type="password" autocomplete="new-password" required /></label>
          <button class="submit-button" type="submit" :disabled="loading">{{ loading ? t('auth.processing') : t('auth.completeFirstLogin') }}</button>
        </form>
      </section>
    </main>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import InlineNotice from '../components/InlineNotice.vue'
import { useRoute, useRouter } from 'vue-router'
import { auth, setAuth } from '../auth'
import { authApi } from '../features/auth/api'
import { homePath } from '../shared/auth/permissions'
import { useI18n } from '../i18n'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const email = ref('')
const emailCode = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const cooldown = ref(0)
const loading = ref(false)
const message = ref('')
const noticeType = ref('error')

function setNotice(value, type = 'error') { message.value = value; noticeType.value = type }
async function sendCode() {
  if (!email.value || cooldown.value > 0) return
  try {
    await authApi.requestEmailCode(email.value.trim(), 'first_login')
    setNotice(t('auth.codeSent'), 'success')
    cooldown.value = 60
    const timer = setInterval(() => { cooldown.value -= 1; if (cooldown.value <= 0) clearInterval(timer) }, 1000)
  } catch (error) { setNotice(error?.message || t('auth.emailCodeFailed')) }
}
async function submit() {
  message.value = ''
  if (newPassword.value !== confirmPassword.value) { setNotice(t('auth.passwordMismatch')); return }
  if (newPassword.value.length < 12 || !/[A-Za-z]/.test(newPassword.value) || !/[0-9]/.test(newPassword.value)) { setNotice(t('auth.passwordRule')); return }
  loading.value = true
  try {
    const result = await authApi.completeFirstLogin({ email: email.value.trim(), email_code: emailCode.value.trim(), new_password: newPassword.value })
    setAuth(result.token, result.user)
    const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/') ? route.query.redirect : homePath(result.user)
    router.replace(redirect)
  } catch (error) { setNotice(error?.message || t('auth.firstLoginFailed')) } finally { loading.value = false }
}

if (!auth.token) router.replace({ path: '/login', query: { redirect: route.query.redirect || '/' } })
</script>

<style scoped>
.stitch-auth-page { min-height: 100vh; display: flex; flex-direction: column; background: var(--background); color: var(--on-surface); }
.stitch-auth-header { height: 60px; padding: 0 32px; display: flex; align-items: center; border-bottom: 1px solid var(--border); background: rgba(248,249,255,.85); }
.auth-header-inner { width: 100%; max-width: 1080px; margin: auto; }.brand-box, .brand-row { display: flex; align-items: center; gap: 8px; }.brand-icon-img, .card-logo { width: 28px; height: 28px; }.brand-name { font-weight: 800; }
.first-login-main { flex: 1; display: flex; justify-content: center; align-items: flex-start; padding: 56px 20px; }.first-login-card { width: min(100%, 460px); padding: 30px; background: #fff; border: 1px solid var(--border); border-radius: 12px; box-shadow: var(--shadow-card); }.first-login-card h1 { margin: 18px 0 0; font-size: 25px; }.sub { color: var(--on-surface-variant); font-size: 14px; line-height: 1.6; }.first-login-card form { display: grid; gap: 15px; margin-top: 20px; }.first-login-card label { display: grid; gap: 6px; font-size: 13px; font-weight: 600; }.first-login-card input { box-sizing: border-box; width: 100%; height: 42px; padding: 0 12px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; }.code-row { display: flex; align-items: end; gap: 8px; }.code-field { flex: 1; }.code-button { height: 42px; padding: 0 12px; border: 1px solid var(--primary); border-radius: 8px; background: #fff; color: var(--primary); white-space: nowrap; }.code-button:disabled { border-color: var(--border); color: #94a3b8; }.submit-button { height: 46px; border: 0; border-radius: 8px; background: var(--primary); color: #fff; font-weight: 700; }.submit-button:disabled { opacity: .6; }.error-banner, .ok-banner { padding: 11px 13px; border-radius: 8px; font-size: 13px; line-height: 1.5; }.error-banner { background: var(--error-container, #ffdad6); color: #93000a; }.ok-banner { background: #e8f7ee; color: #166534; }
</style>
