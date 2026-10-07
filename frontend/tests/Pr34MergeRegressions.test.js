import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import AppShell from '../src/layouts/AppShell.vue'
import { clearNavigationFailure, navigationFailure, reportNavigationFailure } from '../src/shared/navigationFailure'
import { getAdminAiConfig, saveAdminAiConfig, clearAdminAiConfig, testAdminAiConfig } from '../src/api.js'

vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/workspace', meta: { title: 'Workspace' } }) }))
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); clearNavigationFailure() })

describe('PR34 merge regressions', () => {
  it('navigation failure does not update title; successful navigation clears stale failure', () => {
    const router = read('../src/router.js')
    const body = router.match(/router.afterEach\(\(to, _from, failure\) => \{([\s\S]*?)\n\}\)/)[1]
    const finish = new Function('to', '_from', 'failure', 'document', 'clearNavigationFailure', body)
    const doc = { title: 'Writer' }
    reportNavigationFailure(new Error('old failure'), { fullPath: '/old' })
    finish({ meta: { title: 'Target' } }, null, {}, doc, clearNavigationFailure)
    expect(doc.title).toBe('Writer')
    expect(navigationFailure.value).not.toBeNull()
    finish({ meta: { title: 'Target' } }, null, null, doc, clearNavigationFailure)
    expect(doc.title).toBe('Target · PaperGuard')
    expect(navigationFailure.value).toBeNull()
  })

  it('widening the viewport closes the drawer and unmount removes the listener', async () => {
    let onChange
    const mql = { matches: false, addEventListener: vi.fn((name, fn) => { onChange = fn }), removeEventListener: vi.fn() }
    vi.stubGlobal('matchMedia', vi.fn(() => mql))
    const wrapper = mount(AppShell, { props: { authenticated: true, user: { role: 'teacher', status: 'active' } },
      global: { stubs: { RoleSidebar: true, RouterLink: true } } })
    await wrapper.get('[aria-controls="role-sidebar"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[aria-controls="role-sidebar"]').attributes('aria-expanded')).toBe('true')
    mql.matches = true
    onChange()
    await flushPromises()
    expect(wrapper.get('[aria-controls="role-sidebar"]').attributes('aria-expanded')).toBe('false')
    wrapper.unmount()
    expect(mql.removeEventListener).toHaveBeenCalledWith('change', onChange)
  })

  it('all admin AI calls preserve the encoded target scope', async () => {
    const fetcher = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ configured: false }) }))
    vi.stubGlobal('fetch', fetcher)
    await getAdminAiConfig('school a/b')
    await saveAdminAiConfig({ model: 'fixture' }, 'school a/b')
    await clearAdminAiConfig('school a/b')
    await testAdminAiConfig({ model: 'fixture' }, 'school a/b')
    expect(fetcher.mock.calls).toHaveLength(4)
    for (const [url] of fetcher.mock.calls) expect(url).toContain('organization_id=school%20a%2Fb')
  })

  it('all writing AI content paths confirm server disclosure before dispatch', () => {
    const writer = read('../src/views/Writer.vue')
    expect(writer).toContain('aiDisclosure.value = await getAiDisclosure()')
    expect(writer.match(/if \(!await confirmAiDisclosure\(\)\) return/g)).toHaveLength(3)
    expect(writer).toContain('aiDisclosure.data_recipient_host')
  })

  it('focused panels are not captured by the configuration-only branch', () => {
    expect(read('../src/views/Workflow.vue')).toContain('<template v-if="canConfigure && !panelFocused">')
  })
})
