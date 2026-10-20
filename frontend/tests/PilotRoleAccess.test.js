import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { canAccessRoute, createPermissionResolver, homePath } from '../src/shared/auth/permissions'

const mocks = vi.hoisted(() => ({
  replace: vi.fn(), updateUser: vi.fn(),
  getCurrentUser: vi.fn(), getRoleCertification: vi.fn(), submitRoleCertification: vi.fn(),
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/workspace', fullPath: '/workspace', query: {} }), useRouter: () => ({ replace: mocks.replace }) }))
vi.mock('../src/auth', () => ({ auth: { user: { role: 'user' }, updateUser: mocks.updateUser } }))
vi.mock('../src/features/auth/api', () => ({ authApi: mocks }))
import RoleSidebar from '../src/components/RoleSidebar.vue'
import RoleCertification from '../src/views/RoleCertification.vue'

let wrapper
afterEach(() => { wrapper?.unmount(); vi.clearAllMocks() })

describe('pilot role admission', () => {
  it.each(['college_admin', 'reviewer', 'unknown'])('closes menus, routes and capabilities for %s', role => {
    const user = { role, status: 'active' }
    wrapper = mount(RoleSidebar, { props: { user }, global: { stubs: { ScreenDeck: { template: '<div><slot /></div>' }, RouterLink: true } } })
    expect(wrapper.findAll('.sidebar-link, .sidebar-tool')).toHaveLength(0)
    expect(homePath(user)).toBe('/login')
    expect(canAccessRoute({ meta: {} }, user)).toBe(false)
    expect(canAccessRoute({ meta: { roles: [role] } }, user)).toBe(false)
    expect(createPermissionResolver(user).can('manage', 'workflow')).toBe(false)
  })

  it.each(['unselected', 'pending', 'rejected', 'approved'])('keeps a %s user in certification', status => {
    const user = { role: 'user', role_application_status: status }
    expect(homePath(user)).toBe('/role-certification')
    expect(canAccessRoute({ meta: {} }, user)).toBe(false)
    expect(canAccessRoute({ meta: { certification: true } }, user)).toBe(true)
  })

  it.each(['admin', 'teacher', 'student'])('retains generic document routes for %s', role => {
    expect(canAccessRoute({ meta: {} }, { role })).toBe(true)
  })

  it('offers only teacher and student applications', async () => {
    mocks.getCurrentUser.mockResolvedValue({ role: 'user' })
    mocks.getRoleCertification.mockResolvedValue({ organizations: [{ id: 'school', name: '测试学校', type: 'school' }] })
    mocks.submitRoleCertification.mockResolvedValue({ role: 'user', role_application_status: 'pending' })
    wrapper = mount(RoleCertification)
    await flushPromises()
    expect(wrapper.findAll('.role-option')).toHaveLength(2)
    expect(wrapper.text()).not.toContain('社会用户')
    await wrapper.get('select').setValue('school')
    await wrapper.get('.certification-actions button').trigger('click')
    await flushPromises()
    expect(mocks.submitRoleCertification).toHaveBeenCalledWith('student', { school_id: 'school' })
    expect(wrapper.text()).toContain('申请已提交')
  })

  it('refreshes a newly approved identity and enters its workspace', async () => {
    mocks.getCurrentUser.mockResolvedValue({ role: 'teacher', status: 'active' })
    wrapper = mount(RoleCertification)
    await flushPromises()
    expect(mocks.updateUser).toHaveBeenCalledWith({ role: 'teacher', status: 'active' })
    expect(mocks.replace).toHaveBeenCalledWith('/workspace')
    expect(mocks.getRoleCertification).not.toHaveBeenCalled()
  })
})
