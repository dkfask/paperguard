import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { auth, clearAuth, setAuth } from '../src/auth'
import { currentLang } from '../src/i18n'

const routing = vi.hoisted(() => ({ query: {}, replace: vi.fn() }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: routing.query }),
  useRouter: () => ({ replace: routing.replace }),
}))
import Login from '../src/views/Login.vue'

const wrappers = []
function render() {
  const wrapper = mount(Login, { global: { stubs: {
    RouterLink: { template: '<a><slot /></a>' },
  } } })
  wrappers.push(wrapper)
  return wrapper
}
function response(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'x-request-id': 'synthetic-request-id' },
  })
}
async function openRecovery(wrapper) {
  await wrapper.get('.auth-recovery-link').trigger('click')
  await wrapper.get('#recoveryEmail').setValue('fixture@example.edu')
}
async function fillRecovery(wrapper, confirm = 'NewFixturePassword123') {
  await wrapper.get('#recoveryCode').setValue('123456')
  await wrapper.get('#recoveryPassword').setValue('NewFixturePassword123')
  await wrapper.get('#recoveryConfirm').setValue(confirm)
}

beforeEach(() => {
  clearAuth()
  currentLang.value = 'zh'
  routing.query = {}
  vi.clearAllMocks()
})
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  vi.useRealTimers()
  vi.unstubAllGlobals()
  clearAuth()
})

describe('login and password recovery', () => {
  it('renders a login failure as a message without a request ID', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ detail: '用户名或密码错误' }, 401)))
    const wrapper = render()
    await wrapper.get('#username').setValue('fixture')
    await wrapper.get('#password').setValue('wrong')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"] .inline-notice-summary').text()).toBe('用户名或密码错误')
    expect(wrapper.text()).not.toContain('synthetic-request-id')
    expect(wrapper.text()).not.toContain('请求 ID')
  })

  it('offers recovery guidance and clears secret fields on return', async () => {
    const wrapper = render()
    await openRecovery(wrapper)
    expect(wrapper.get('h1').text()).toBe('重置密码')
    expect(wrapper.text()).toContain('请联系导师或管理员重置密码')
    await fillRecovery(wrapper)
    await wrapper.get('.auth-back-link').trigger('click')
    await wrapper.get('.auth-recovery-link').trigger('click')
    for (const field of ['#recoveryCode', '#recoveryPassword', '#recoveryConfirm']) {
      expect(wrapper.get(field).element.value).toBe('')
    }
  })

  it('validates the email and sends a recovery code with a cooldown', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockResolvedValue(response({ ok: true, expires_in: 600 }))
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = render()
    await wrapper.get('.auth-recovery-link').trigger('click')
    await wrapper.get('.code-button').trigger('click')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('邮箱格式不正确')
    await wrapper.get('#recoveryEmail').setValue('fixture@example.edu')
    await wrapper.get('.code-button').trigger('click')
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/email/code', expect.objectContaining({
      body: JSON.stringify({ email: 'fixture@example.edu', purpose: 'password_reset' }),
    }))
    expect(wrapper.get('.code-button').attributes('disabled')).toBeDefined()
    expect(wrapper.get('.code-button').text()).toBe('60 秒后重发')
    await vi.advanceTimersByTimeAsync(60_000)
    expect(wrapper.get('.code-button').attributes('disabled')).toBeUndefined()
    await wrapper.get('#recoveryCode').setValue('123456')
    await wrapper.get('#recoveryEmail').setValue('changed@example.edu')
    expect(wrapper.get('#recoveryCode').element.value).toBe('')
  })

  it('shows an unavailable mail service and leaves the send button usable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({
      detail: { message: '邮件服务尚未配置，请联系导师或管理员重置密码' },
    }, 503)))
    const wrapper = render()
    await openRecovery(wrapper)
    await wrapper.get('.code-button').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('请联系导师或管理员重置密码')
    expect(wrapper.text()).not.toContain('synthetic-request-id')
    expect(wrapper.get('.code-button').attributes('disabled')).toBeUndefined()
  })

  it('keeps the recovery form on failure and returns to login after a retry succeeds', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ detail: { message: '验证码不正确' } }, 400))
      .mockResolvedValueOnce(response({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = render()
    await openRecovery(wrapper)
    await fillRecovery(wrapper, 'DifferentFixturePassword123')
    await wrapper.get('form').trigger('submit')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('两次输入的新密码不一致')
    await wrapper.get('#recoveryConfirm').setValue('NewFixturePassword123')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('验证码不正确')
    expect(wrapper.find('#recoveryPassword').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('synthetic-request-id')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(fetchMock).toHaveBeenLastCalledWith('/api/auth/reset-password', expect.objectContaining({
      body: JSON.stringify({
        email: 'fixture@example.edu', email_code: '123456', new_password: 'NewFixturePassword123',
      }),
    }))
    expect(wrapper.get('[role="status"]').text()).toContain('密码已更新')
    expect(wrapper.get('#username').element.value).toBe('fixture@example.edu')
    expect(wrapper.get('#password').element.value).toBe('')
    expect(auth.token).toBe('')
  })

  it('hides recovery during a forced password change', () => {
    setAuth('synthetic-token', { id: 'fixture', role: 'student' })
    routing.query = { forceChange: '1' }
    const wrapper = render()
    expect(wrapper.find('.auth-recovery-link').exists()).toBe(false)
    expect(wrapper.find('#newPassword').exists()).toBe(true)
  })

  it('provides English recovery labels', async () => {
    currentLang.value = 'en'
    const wrapper = render()
    expect(wrapper.get('.auth-recovery-link').text()).toBe('Forgot password?')
    await wrapper.get('.auth-recovery-link').trigger('click')
    expect(wrapper.get('h1').text()).toBe('Reset password')
    expect(wrapper.text()).toContain('Contact your advisor or administrator')
  })
})
