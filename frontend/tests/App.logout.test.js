import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  replace: vi.fn(), logout: vi.fn(), clearAuth: vi.fn(),
  auth: { token: 'session-token', user: { id: 'student', role: 'student' } },
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ replace: mocks.replace }) }))
vi.mock('../src/auth', () => ({ auth: mocks.auth, clearAuth: mocks.clearAuth }))
vi.mock('../src/features/auth/api', () => ({ authApi: { logout: mocks.logout } }))

import App from '../src/App.vue'

function open() {
  return shallowMount(App, { global: { stubs: {
    AppShell: { emits: ['logout'], template: '<button @click="$emit(\'logout\')">退出</button>' },
    AppErrorBoundary: true, RouterView: true,
  } } })
}

describe('logout with a pending writer save', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.logout.mockResolvedValue(undefined)
  })

  it('keeps the session when the writer prevents navigation after a failed save', async () => {
    mocks.replace.mockResolvedValueOnce(new Error('navigation aborted'))
    const wrapper = open()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(mocks.logout).not.toHaveBeenCalled()
    expect(mocks.clearAuth).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('keeps the session if navigation itself fails', async () => {
    mocks.replace.mockRejectedValueOnce(new Error('navigation failed'))
    const wrapper = open()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(mocks.logout).not.toHaveBeenCalled()
    expect(mocks.clearAuth).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('ends the session only after the current page allows navigation', async () => {
    mocks.replace.mockResolvedValue(undefined)
    const wrapper = open()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(mocks.replace).toHaveBeenNthCalledWith(1, { path: '/login', query: { logout: '1' } })
    expect(mocks.logout).toHaveBeenCalledOnce()
    expect(mocks.clearAuth).toHaveBeenCalledOnce()
    expect(mocks.replace.mock.invocationCallOrder[0]).toBeLessThan(mocks.logout.mock.invocationCallOrder[0])
    expect(mocks.logout.mock.invocationCallOrder[0]).toBeLessThan(mocks.clearAuth.mock.invocationCallOrder[0])
    wrapper.unmount()
  })

  it('clears local authentication even if the remote logout fails', async () => {
    mocks.replace.mockResolvedValue(undefined)
    mocks.logout.mockRejectedValueOnce(new Error('offline'))
    const wrapper = open()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(mocks.clearAuth).toHaveBeenCalledOnce()
    expect(mocks.replace).toHaveBeenLastCalledWith('/login')
    wrapper.unmount()
  })
})
