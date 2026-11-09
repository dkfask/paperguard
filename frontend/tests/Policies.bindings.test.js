import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
const api = vi.hoisted(() => ({ fetchPolicies: vi.fn(), fetchPolicy: vi.fn(), fetchOrganizations: vi.fn(), fetchCohorts: vi.fn(), getAdminUsers: vi.fn(), getTeacherStudents: vi.fn(), savePolicy: vi.fn(), archivePolicy: vi.fn(), createPolicyVersion: vi.fn(), deletePolicyDocument: vi.fn(), downloadPolicyDocument: vi.fn(), importPolicyWithAi: vi.fn(), publishPolicy: vi.fn(), submitPolicyReview: vi.fn(), upgradePolicyProjects: vi.fn(), uploadPolicyDocument: vi.fn() }))
vi.mock('../src/api.js', () => api)
vi.mock('../src/auth.js', () => {
  const auth = { user: { id: 'teacher-1', username: 'test-teacher', display_name: '测试教师', role: 'teacher', status: 'active' } }
  return { auth, isManager: () => ['admin', 'college_admin', 'teacher'].includes(auth.user.role) }
})
vi.mock('vue-router', () => ({ RouterLink: { template: '<a><slot /></a>' } }))
import Policies from '../src/views/Policies.vue'
import BaseModal from '../src/components/base/BaseModal.vue'
import { auth } from '../src/auth.js'
let wrapper
beforeEach(() => {
  auth.user.role = 'teacher'
  vi.clearAllMocks(); api.fetchPolicies.mockResolvedValue([]); api.fetchOrganizations.mockResolvedValue([{ id: 'org-school', name: '测试学校', type: 'school' }]); api.fetchCohorts.mockResolvedValue([]); api.getTeacherStudents.mockResolvedValue({ students: [] })
  api.savePolicy.mockImplementation(async data => ({ ...data, id: 'policy-1' }))
})
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })
async function open() {
  mountPage()
  await flushPromises(); await wrapper.findAll('button').find(el => el.text().includes('新建规范')).trigger('click'); await flushPromises()
}
function mountPage() {
  wrapper = mount(Policies, { global: { stubs: { teleport: true, ScreenDeck: { template: '<div><slot /></div>' } } } })
}
describe('named policy scope bindings', () => {
  it('selects an organization by name while preserving internal binding fields in the payload', async () => {
    await open(); await wrapper.findAll('button').find(el => el.text().includes('添加绑定')).trigger('click')
    const row = wrapper.get('.binding-row'); await row.get('select[aria-label="适用范围"]').setValue('school'); await row.get('select[aria-label="适用对象"]').setValue('org-school')
    expect(row.text()).toContain('测试学校'); expect(row.findAll('input:not([type="checkbox"])')).toHaveLength(0)
    await wrapper.get('.policy-editor-panel input').setValue('测试规范')
    await wrapper.get('.policy-editor-panel').findAll('button').find(el => el.text().includes('新建规范')).trigger('click'); await flushPromises()
    const payload = api.savePolicy.mock.calls[0][0]
    expect(payload.bindings[0]).toMatchObject({ scope_type: 'school', scope_id: 'org-school', enabled: true, priority: 0 })
    expect(payload.bindings[0].id).toBeTruthy()
  })
  it('clears the old target when scope changes so a school cannot become a teacher ID', async () => {
    await open(); await wrapper.findAll('button').find(el => el.text().includes('添加绑定')).trigger('click')
    const row = wrapper.get('.binding-row'); await row.get('select[aria-label="适用范围"]').setValue('school'); await row.get('select[aria-label="适用对象"]').setValue('org-school')
    await row.get('select[aria-label="适用范围"]').setValue('teacher')
    expect(row.get('select[aria-label="适用对象"]').element.value).toBe('')
    expect(row.text()).toContain('测试教师'); expect(row.text()).not.toContain('测试学校')
  })
})

describe('policy list states', () => {
  it('offers teachers a single create/import/refresh entry without the empty list heading', async () => {
    mountPage(); await flushPromises()
    expect(wrapper.get('.policy-empty-state').text()).toContain('创建规范或导入已有要求')
    expect(wrapper.findAll('h2').filter(el => el.text() === '可用规范')).toHaveLength(0)
    for (const label of ['新建规范', 'AI 导入规范', '刷新']) {
      expect(wrapper.findAll('button').filter(el => el.text().includes(label))).toHaveLength(1)
    }
  })

  it('gives students appropriate guidance and no management actions', async () => {
    auth.user.role = 'student'; mountPage(); await flushPromises()
    expect(wrapper.get('.policy-empty-state').text()).toContain('联系指导教师或管理员')
    expect(wrapper.findAll('button').some(el => /新建规范|AI 导入规范/.test(el.text()))).toBe(false)
    expect(api.getTeacherStudents).not.toHaveBeenCalled()
  })

  it('keeps loading distinct from a successful empty response', async () => {
    let resolve
    api.fetchPolicies.mockReturnValue(new Promise(done => { resolve = done }))
    mountPage(); await flushPromises()
    expect(wrapper.get('.async-state.loading').text()).toContain('正在加载规范')
    expect(wrapper.find('.policy-empty-state').exists()).toBe(false)
    resolve([]); await flushPromises()
    expect(wrapper.get('.policy-empty-state').text()).toContain('暂无可用规范')
  })

  it('shows one retry on initial failure and recovers to an empty state', async () => {
    api.fetchPolicies.mockRejectedValueOnce(new Error('规范服务暂不可用'))
    mountPage(); await flushPromises()
    expect(wrapper.get('.async-state.error').text()).toContain('规范加载失败')
    expect(wrapper.get('.async-state.error').text()).toContain('规范服务暂不可用')
    expect(wrapper.find('.policy-empty-state').exists()).toBe(false)
    expect(wrapper.find('.error-banner').exists()).toBe(false)
    await wrapper.get('.async-state.error button').trigger('click'); await flushPromises()
    expect(wrapper.find('.async-state.error').exists()).toBe(false)
    expect(wrapper.get('.policy-empty-state').text()).toContain('暂无可用规范')
  })

  it('preserves an existing list when refresh fails and reports the failure', async () => {
    api.fetchPolicies.mockResolvedValueOnce([{ id: 'existing', name: '已发布学校规范', level: 'school', version: '1', status: 'published' }])
    mountPage(); await flushPromises()
    api.fetchPolicies.mockRejectedValueOnce(new Error('刷新暂不可用'))
    await wrapper.get('button[aria-label="刷新"]').trigger('click'); await flushPromises()
    expect(wrapper.get('.policy-list-item').text()).toContain('已发布学校规范')
    expect(wrapper.get('.error-banner').text()).toContain('刷新暂不可用')
    expect(wrapper.find('.policy-empty-state').exists()).toBe(false)
    expect(wrapper.findAll('h2').filter(el => el.text() === '可用规范')).toHaveLength(1)
  })

  it('does not reopen details when a pending refresh finishes after the user closes them', async () => {
    const policy = { id: 'existing', name: '已发布学校规范', level: 'school', version: '1', status: 'published', items: [], documents: [], bindings: [] }
    api.fetchPolicies.mockResolvedValue([policy]); api.fetchPolicy.mockResolvedValue(policy)
    mountPage(); await flushPromises()
    await wrapper.get('.policy-list-item').trigger('click'); await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    let resolve
    api.fetchPolicy.mockReturnValueOnce(new Promise(done => { resolve = done }))
    await wrapper.get('button[aria-label="刷新"]').trigger('click'); await flushPromises()
    wrapper.getComponent(BaseModal).vm.$emit('close'); await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    resolve(policy); await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })

  it('refreshes a previously viewed policy without opening its closed details', async () => {
    const policy = { id: 'existing', name: '已发布学校规范', level: 'school', version: '1', status: 'published', items: [], documents: [], bindings: [] }
    api.fetchPolicies.mockResolvedValue([policy]); api.fetchPolicy.mockResolvedValue(policy)
    mountPage(); await flushPromises()
    await wrapper.get('.policy-list-item').trigger('click'); await flushPromises()
    wrapper.getComponent(BaseModal).vm.$emit('close'); await flushPromises()
    await wrapper.get('button[aria-label="刷新"]').trigger('click'); await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.get('.policy-list-item').text()).toContain('已发布学校规范')
  })

  it('does not replace a new draft with an older policy refresh response', async () => {
    const policy = { id: 'existing', name: '已发布学校规范', level: 'school', version: '1', status: 'published', items: [], documents: [], bindings: [] }
    api.fetchPolicies.mockResolvedValue([policy]); api.fetchPolicy.mockResolvedValue(policy)
    mountPage(); await flushPromises()
    await wrapper.get('.policy-list-item').trigger('click'); await flushPromises()
    let resolve
    api.fetchPolicy.mockReturnValueOnce(new Promise(done => { resolve = done }))
    await wrapper.get('button[aria-label="刷新"]').trigger('click'); await flushPromises()
    wrapper.getComponent(BaseModal).vm.$emit('close'); await flushPromises()
    await wrapper.get('.resource-hero-actions button').trigger('click'); await flushPromises()
    await wrapper.get('.policy-editor-panel input').setValue('新规范草稿')
    resolve(policy); await flushPromises()
    expect(wrapper.get('.policy-editor-panel input').element.value).toBe('新规范草稿')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
  })
})

describe('cover and reference checks', () => {
  it('configures both checks with their required parameters', async () => {
    await open()
    await wrapper.get('.policy-editor-panel input').setValue('1101 测试规范')
    await wrapper.findAll('button').find(el => el.text().includes('添加规则')).trigger('click')
    let row = wrapper.get('.rule-row')
    await row.get('.rule-row-top input').setValue('cover-fields')
    row = wrapper.get('.rule-row')
    await row.get('.rule-fields select').setValue('cover.fields_present')
    expect(row.get('.rule-fields input').element.value).toBe('cover')
    await row.get('input[placeholder="例如：student_name, title"]').setValue(' title， author ')
    await wrapper.findAll('button').find(el => el.text().includes('添加规则')).trigger('click')
    row = wrapper.findAll('.rule-row')[1]
    await row.get('.rule-row-top input').setValue('reference-facts')
    row = wrapper.findAll('.rule-row')[1]
    await row.get('.rule-fields select').setValue('references.list_facts')
    expect(row.get('.rule-fields input').element.value).toBe('references')
    await row.get('.reference-required input').setValue(true)
    await row.get('.reference-sequential input').setValue(true)
    await wrapper.get('.policy-editor-panel').findAll('button').find(el => el.text().includes('新建规范')).trigger('click')
    await flushPromises()
    const items = api.savePolicy.mock.calls[0][0].items
    expect(items[0]).toMatchObject({ checker: 'cover.fields_present', target: 'cover', params: { field_keys: ['title', 'author'] } })
    expect(items[1]).toMatchObject({ checker: 'references.list_facts', target: 'references', params: { required: true, numbering: 'sequential' } })
    expect(items[0]).not.toHaveProperty('fieldKeysText')
    expect(items[1]).not.toHaveProperty('referenceRequired')
    expect(items[1]).not.toHaveProperty('referenceSequential')
  })

  it('rejects empty or duplicate cover keys before saving', async () => {
    await open()
    await wrapper.findAll('button').find(el => el.text().includes('添加规则')).trigger('click')
    const row = wrapper.get('.rule-row')
    await row.get('.rule-fields select').setValue('cover.fields_present')
    const save = wrapper.get('.policy-editor-panel').findAll('button').find(el => el.text().includes('新建规范'))
    await save.trigger('click'); await flushPromises()
    expect(api.savePolicy).not.toHaveBeenCalled()
    expect(wrapper.get('.policy-editor-panel [role="alert"]').text()).toContain('填写不重复的封面字段标识')
    expect(wrapper.findAll('[role="alert"]')).toHaveLength(1)
    await row.get('input[placeholder="例如：student_name, title"]').setValue('title,title')
    await save.trigger('click'); await flushPromises()
    expect(api.savePolicy).not.toHaveBeenCalled()
  })

  it('round trips saved check parameters without losing unrelated fields', async () => {
    const policy = { id: 'existing', name: '草稿规范', level: 'teacher', version: '1', status: 'draft', documents: [], bindings: [], items: [
      { id: 'cover-fields', type: 'auto', target: 'cover', checker: 'cover.fields_present', params: { field_keys: ['title', 'author'], extra: 'keep' } },
      { id: 'reference-facts', type: 'auto', target: 'references', checker: 'references.list_facts', params: { required: true, numbering: 'sequential', extra: 'keep' } },
    ] }
    api.fetchPolicies.mockResolvedValue([policy]); api.fetchPolicy.mockResolvedValue(policy)
    mountPage(); await flushPromises()
    await wrapper.get('.policy-list-item').trigger('click'); await flushPromises()
    const rows = wrapper.findAll('.rule-row')
    expect(rows[0].get('input[placeholder="例如：student_name, title"]').element.value).toBe('title, author')
    expect(rows[1].get('.reference-required input').element.checked).toBe(true)
    expect(rows[1].get('.reference-sequential input').element.checked).toBe(true)
    await rows[0].get('input[placeholder="例如：student_name, title"]').setValue('title')
    await rows[1].get('.reference-required input').setValue(false)
    await rows[1].get('.reference-sequential input').setValue(false)
    await wrapper.get('.policy-editor-panel').findAll('button').find(el => el.text().includes('保存草稿')).trigger('click')
    await flushPromises()
    const items = api.savePolicy.mock.calls[0][0].items
    expect(items[0].params).toMatchObject({ field_keys: ['title'], extra: 'keep' })
    expect(items[1].params).toMatchObject({ required: false, extra: 'keep' })
    expect(items[1].params).not.toHaveProperty('numbering')
  })
})
