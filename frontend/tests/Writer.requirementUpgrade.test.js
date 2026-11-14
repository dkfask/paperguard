import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  fetchProject: vi.fn(), fetchProjectCapabilities: vi.fn(), fetchProjectExportJobs: vi.fn(),
  previewRequirementUpgrade: vi.fn(), upgradeRequirements: vi.fn(),
  checkProject: vi.fn(), checkProjectTemplate: vi.fn(), updateProjectSection: vi.fn(),
}))
vi.mock('../src/api.js', async () => {
  const actual = await vi.importActual('../src/api.js')
  return { ...Object.fromEntries(Object.keys(actual).map(key => [key, vi.fn().mockResolvedValue([])])), ...api }
})

it.each([
  ['image_typography_unverified', 'writer.imageTypographyUnavailable'],
  ['format_rule_unverified', 'writer.formatRuleUnavailable'],
])('reports %s as a system limitation without offering AI review', async (reason, message) => {
  api.fetchProject.mockResolvedValue({ id: 'p1', title: '论文', revision: 1, meta: {},
    sections: [{ key: 'body', type: 'body', title: '正文', paragraphs: [] }],
    requirement_snapshot: { template: { version: '1' }, policy_sets: [], resolved_items: [] },
    template_snapshot: { id: 't1', name: '模板', styles: {}, meta_fields: [], sections: [{ key: 'body', type: 'body', title: '正文' }] } })
  api.fetchProjectCapabilities.mockResolvedValue({ formal_delivery_enabled: true })
  api.fetchProjectExportJobs.mockResolvedValue([])
  api.checkProject.mockResolvedValue({ summary: { total: 0, not_checked: 1 }, findings: [], waived: [],
    not_checked: [{ id: 'image-font', type: 'manual', description: '图中文字字号', source: '学校规范',
      reason, enforce: 'hard' }] })
  api.checkProjectTemplate.mockResolvedValue({ summary: {}, findings: [], context: { template_name: '模板' } })
  wrapper = mount(Writer, { global: { stubs: { BaseModal: { template: '<div><slot /></div>' }, RouterLink: true, BlockEditor: true, WritingSpecificationPreview: true } } })
  await flushPromises()
  await wrapper.find('.writer-review-action').trigger('click')
  await flushPromises()
  const item = wrapper.find('.not-checked-item')
  expect(item.text()).toContain(message)
  expect(item.find('.assist-action').exists()).toBe(false)
})
vi.mock('../src/auth', () => ({ auth: { user: { id: 'teacher', role: 'teacher', status: 'active' } }, isManager: () => true }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 'p1' }, query: {} }), useRouter: () => ({ push: vi.fn() }), onBeforeRouteLeave: vi.fn() }))
vi.mock('../src/i18n', () => ({ useI18n: () => ({ lang: { value: 'zh' }, t: (key, params) => params?.count !== undefined ? `${key} ${params.count}` : key }) }))
import Writer from '../src/views/Writer.vue'

let wrapper
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.clearAllMocks() })

it('locates a manuscript image when its image typography cannot be verified', async () => {
  api.fetchProject.mockResolvedValue({ id: 'p1', title: '论文', revision: 1, meta: {},
    sections: [
      { key: 'intro', type: 'body', title: '绪论', paragraphs: [] },
      { key: 'design', type: 'body', title: '系统设计', paragraphs: [
        { id: 'image-1', type: 'image', image: { url: '/media/uploads/p1-test.png', caption: '系统结构图' } },
      ] },
    ],
    requirement_snapshot: { template: { version: '1' }, policy_sets: [], resolved_items: [] },
    template_snapshot: { id: 't1', name: '模板', styles: {}, meta_fields: [], sections: [
      { key: 'intro', type: 'body', title: '绪论' }, { key: 'design', type: 'body', title: '系统设计' },
    ] } })
  api.fetchProjectCapabilities.mockResolvedValue({ formal_delivery_enabled: true })
  api.fetchProjectExportJobs.mockResolvedValue([])
  api.checkProject.mockResolvedValue({ summary: { total: 0, not_checked: 1 }, findings: [], waived: [],
    not_checked: [{ id: 'image-font', type: 'manual', description: '图中文字字号', source: '学校规范',
      reason: 'image_typography_unverified', enforce: 'hard' }] })
  api.checkProjectTemplate.mockResolvedValue({ summary: {}, findings: [], context: { template_name: '模板' } })
  wrapper = mount(Writer, { global: { stubs: { BaseModal: { template: '<div><slot /></div>' }, RouterLink: true, BlockEditor: true, WritingSpecificationPreview: true } } })
  await flushPromises()
  await wrapper.find('.writer-review-action').trigger('click'); await flushPromises()
  const location = wrapper.get('[data-test="image-location"]')
  expect(location.text()).toContain('系统设计')
  expect(location.text()).toContain('系统结构图')
  await wrapper.findAll('.nav-row').find(row => row.text().includes('绪论')).trigger('click')
  await wrapper.get('.heading-input').setValue('未保存标题')
  api.updateProjectSection.mockRejectedValueOnce(new Error('保存失败'))
    .mockResolvedValueOnce({ revision: 2 })
  await location.trigger('click'); await flushPromises()
  expect(wrapper.get('.heading-input').element.value).toBe('未保存标题')
  expect(wrapper.text()).toContain('保存失败')
  expect(wrapper.find('[data-review-block-id="image-1"]').exists()).toBe(false)
  await location.trigger('click'); await flushPromises()
  expect(wrapper.find('[data-review-block-id="image-1"] .image-block').exists()).toBe(true)
})

it('shows excluded template placeholders as source notes rather than unchecked requirements', async () => {
  api.fetchProject.mockResolvedValue({ id: 'p1', title: '论文', revision: 1, meta: {},
    sections: [{ key: 'body', type: 'body', title: '正文', paragraphs: [] }],
    requirement_snapshot: { template: { version: '1' }, policy_sets: [], resolved_items: [] },
    template_snapshot: { id: 't1', name: '模板', styles: {}, meta_fields: [], sections: [{ key: 'body', type: 'body', title: '正文' }] } })
  api.fetchProjectCapabilities.mockResolvedValue({ formal_delivery_enabled: true })
  api.fetchProjectExportJobs.mockResolvedValue([])
  api.checkProject.mockResolvedValue({ summary: { total: 0, not_checked: 0 }, findings: [], waived: [],
    not_checked: [], source_notes: [{ id: 'example-1', description: '（以下略）', source: '学校模板' }] })
  api.checkProjectTemplate.mockResolvedValue({ summary: {}, findings: [], context: { template_name: '模板' } })
  wrapper = mount(Writer, { global: { stubs: { BaseModal: { template: '<div><slot /></div>' }, RouterLink: true, BlockEditor: true, WritingSpecificationPreview: true } } })
  await flushPromises()
  await wrapper.find('.writer-review-action').trigger('click')
  await flushPromises()
  const notes = wrapper.find('[data-test="template-source-notes"]')
  expect(notes.text()).toContain('writer.templatePlaceholderRemoved')
  expect(notes.text()).toContain('（以下略）')
  expect(wrapper.find('.not-checked-item').exists()).toBe(false)
})

it('shows template content guidance without reporting it as an automatic check', async () => {
  api.fetchProject.mockResolvedValue({ id: 'p1', title: '论文', revision: 1, meta: {},
    sections: [{ key: 'body', type: 'body', title: '正文', paragraphs: [] }],
    requirement_snapshot: { template: { version: '1' }, policy_sets: [], resolved_items: [] },
    template_snapshot: { id: 't1', name: '模板', styles: {}, meta_fields: [], sections: [{ key: 'body', type: 'body', title: '正文' }] } })
  api.fetchProjectCapabilities.mockResolvedValue({ formal_delivery_enabled: true })
  api.fetchProjectExportJobs.mockResolvedValue([])
  api.checkProject.mockResolvedValue({ summary: { total: 0, not_checked: 0 }, findings: [], waived: [],
    not_checked: [], source_guidance: [{ id: 'guide-1', description: '这里要包括硬件选型和电路设计', source: '学校模板' }] })
  api.checkProjectTemplate.mockResolvedValue({ summary: {}, findings: [], context: { template_name: '模板' } })
  wrapper = mount(Writer, { global: { stubs: { BaseModal: { template: '<div><slot /></div>' }, RouterLink: true, BlockEditor: true, WritingSpecificationPreview: true } } })
  await flushPromises()
  await wrapper.find('.writer-review-action').trigger('click'); await flushPromises()
  const guidance = wrapper.get('[data-test="template-content-guidance"]')
  expect(guidance.text()).toContain('writer.templateContentGuidanceExplanation')
  expect(guidance.text()).toContain('这里要包括硬件选型和电路设计')
  expect(guidance.text()).toContain('学校模板')
  expect(wrapper.find('.not-checked-item').exists()).toBe(false)
})

it('shows the old and new values when a requirement changes in the upgrade preview', async () => {
  api.fetchProject.mockResolvedValue({ id: 'p1', title: '论文', revision: 1, meta: {},
    sections: [{ key: 'body', type: 'body', title: '正文', paragraphs: [] }],
    requirement_snapshot: { template: { version: '1' }, policy_sets: [], resolved_items: [] },
    template_snapshot: { id: 't1', name: '模板', styles: {}, meta_fields: [], sections: [{ key: 'body', type: 'body', title: '正文' }] } })
  api.fetchProjectCapabilities.mockResolvedValue({ formal_delivery_enabled: true })
  api.fetchProjectExportJobs.mockResolvedValue([])
  api.previewRequirementUpgrade.mockResolvedValue({ from_snapshot_hash: 'old', to_snapshot_hash: 'new', to_resolution_hash: 'approved-target', diff: {
    changed: true, added_sets: [], removed_sets: [], changed_sets: [], added_items: [], removed_items: [],
    changed_items: [{ before: { id: 'body', description: '正文字数', params: { min: 8000, max: 25 } },
      after: { id: 'body', description: '正文字数', params: { min: 8000 } } }],
  } })
  wrapper = mount(Writer, { global: { stubs: { BaseModal: { template: '<div><slot /></div>' }, RouterLink: true, BlockEditor: true, WritingSpecificationPreview: true } } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === 'writer.updateBasis').trigger('click')
  await flushPromises()
  const modal = document.body.querySelector('.upgrade-modal')
  expect(modal.textContent).toContain('writer.changedRequirements 1')
  expect(modal.textContent).toContain('writer.beforeUpgrade')
  expect(modal.textContent).toContain('writer.requirementMax：25')
  expect(modal.textContent).toContain('writer.afterUpgrade')
  api.upgradeRequirements.mockResolvedValue({ project: { id: 'p1', revision: 2, sections: [], template_snapshot: {} } })
  modal.querySelector('button.primary').click()
  await flushPromises()
  const confirm = [...document.body.querySelectorAll('.modal-mask button.primary')]
    .find(button => button.textContent === 'writer.confirmUpgrade')
  confirm.click()
  await flushPromises()
  expect(api.upgradeRequirements).toHaveBeenCalledWith('p1', expect.objectContaining({
    expected_snapshot_hash: 'old', expected_resolution_hash: 'approved-target', confirm: true,
  }))
})
