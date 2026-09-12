import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  getDocumentPackages: vi.fn(),
  getDocumentProjects: vi.fn(),
  createDocumentProject: vi.fn(),
}))
const router = vi.hoisted(() => ({ push: vi.fn() }))

vi.mock('../src/api.js', () => api)
vi.mock('../src/i18n', () => ({
  useI18n: () => ({
    t: (key, params = {}) => key === 'documentProjects.saved' ? `saved ${params.revision}` : key,
  }),
}))
vi.mock('vue-router', () => ({ useRouter: () => router }))

import DocumentProjects from '../src/views/DocumentProjects.vue'

describe('DocumentProjects', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDocumentPackages.mockResolvedValue([{
      package: { id: 'package-1', template_version_id: 'template-v1', spec_version_id: 'spec-v1' },
      template: { name: '实验报告模板' },
      spec_set: { name: '实验报告规范' },
    }])
    api.getDocumentProjects.mockResolvedValue([{
      id: 'project-1', title: '我的报告', document_type_id: 'lab-report', section_count: 2, revision: 1,
    }])
    api.createDocumentProject.mockResolvedValue({ id: 'project-2' })
  })

  it('renders packages and projects, then opens a created project', async () => {
    const wrapper = mount(DocumentProjects, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('实验报告模板')
    expect(wrapper.text()).toContain('我的报告')
    await wrapper.find('input').setValue('新报告')
    await wrapper.find('button.primary').trigger('click')
    await flushPromises()

    expect(api.createDocumentProject).toHaveBeenCalledWith({ package_id: 'package-1', title: '新报告' })
    expect(router.push).toHaveBeenCalledWith('/document-projects/project-2')
  })
})