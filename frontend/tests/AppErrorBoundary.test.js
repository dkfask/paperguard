import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'

const router = vi.hoisted(() => ({ push: vi.fn() }))
const authState = vi.hoisted(() => ({ user: { role: 'teacher' } }))

vi.mock('vue-router', () => ({ useRouter: () => router }))
vi.mock('../src/auth.js', () => ({ auth: authState }))
vi.mock('../src/i18n', () => ({
  useI18n: () => ({ t: (key) => key }),
}))

import AppErrorBoundary from '../src/components/AppErrorBoundary.vue'

const Boom = defineComponent({
  render() {
    throw new Error('rate is not defined')
  },
})

describe('AppErrorBoundary', () => {
  it('returns an open role to the role workspace', async () => {
    router.push.mockReset()
    authState.user = { role: 'teacher' }
    const wrapper = mount(AppErrorBoundary, {
      slots: { default: Boom },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('errorBoundary.home')
    await wrapper.get('button:not(.primary)').trigger('click')
    expect(router.push).toHaveBeenCalledWith('/workspace')
  })
})
