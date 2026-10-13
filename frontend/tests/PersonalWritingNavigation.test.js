import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { pageTitle } from '../src/shared/pageTitles'

vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/document-projects', fullPath: '/document-projects', query: {} }) }))
import RoleSidebar from '../src/components/RoleSidebar.vue'

describe('teacher personal writing and student guidance', () => {
  it('puts personal configuration and student thesis templates in separate groups', () => {
    const wrapper = mount(RoleSidebar, { props: { user: { role: 'teacher' } }, global: { stubs: {
      ScreenDeck: { template: '<div><slot /></div>' }, RouterLink: { props: ['to'], template: '<a :href="typeof to === \'string\' ? to : to.path"><slot /></a>' },
    } } })
    const groups = wrapper.findAll('.sidebar-group')
    expect(groups).toHaveLength(2)
    expect(groups[0].text()).toContain('学生指导')
    expect(groups[0].get('[href="/templates"]').text()).toContain('发布论文模板')
    expect(groups[0].find('[href="/document-packages/publish"]').exists()).toBe(false)
    expect(groups[1].text()).toContain('个人写作')
    expect(groups[1].get('[href="/document-projects"]').text()).toContain('我的通用文档')
    expect(groups[1].get('[href="/document-packages/publish"]').text()).toContain('设置自用文档模板')
    wrapper.unmount()
  })

  it('keeps teacher breadcrumbs consistent with the purpose without changing other roles', () => {
    const route = { path: '/document-packages/publish', meta: { titleKey: 'route.documentPackagePublisher' } }
    expect(pageTitle(route, { role: 'teacher' }, key => key)).toBe('personalTemplates.title')
    expect(pageTitle(route, { role: 'admin' }, key => key)).toBe('route.documentPackagePublisher')
    expect(pageTitle({ path: '/templates', meta: {} }, { role: 'teacher' }, key => key)).toBe('sidebar.publishThesisTemplate')
  })
})
