import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({ fetchProject: vi.fn(), fetchProjectCapabilities: vi.fn(), fetchSubmission: vi.fn() }))
const route = vi.hoisted(() => ({ params: { id: 'p1' }, query: {} }))
vi.mock('../src/api.js', async () => {
  const actual = await vi.importActual('../src/api.js')
  return { ...Object.fromEntries(Object.keys(actual).map(key => [key, vi.fn().mockResolvedValue([])])), ...api }
})
vi.mock('../src/auth', () => ({ auth: { user: { id: 'student', role: 'student', status: 'active' } }, isManager: () => false }))
vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => ({ push: vi.fn() }), onBeforeRouteLeave: vi.fn() }))
vi.mock('../src/i18n', () => ({ useI18n: () => ({ t: key => key, formatDate: value => value }) }))

import Writer from '../src/views/Writer.vue'

const chapter = { key: 'chapter', type: 'heading1', title: '研究方法', paragraphs: [{ id: 'target', type: 'paragraph', text: '现在的内容' }] }
const project = () => ({ id: 'p1', title: '论文', revision: 2, meta: {}, sections: [structuredClone(chapter)],
  template_snapshot: { id: 't1', styles: {}, meta_fields: [], sections: [{ key: 'chapter', type: 'heading1', title: '研究方法' }] } })
let wrapper

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  route.query = { review_block: 'target', review_submission: 's1' }
  api.fetchProject.mockResolvedValue(project())
  api.fetchProjectCapabilities.mockResolvedValue({ formal_delivery_enabled: false })
})
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })
async function open() {
  wrapper = mount(Writer, { attachTo: document.body, global: { stubs: { BaseModal: true, RouterLink: true } } })
  await flushPromises()
}

describe('review comment location', () => {
  it('opens and highlights the matching block in the current draft', async () => {
    await open()
    expect(wrapper.get('[data-test="review-location-status"]').text()).toContain('writer.reviewLocation.found')
    expect(wrapper.get('.review-target').attributes('data-review-block-id')).toBe('target')
    expect(wrapper.get('.review-target textarea').element.value).toBe('现在的内容')
  })

  it('selects the exact quoted occurrence when the submitted paragraph is unchanged', async () => {
    route.query.review_comment = 'comment-1'
    const current = project()
    current.sections[0].paragraphs[0].text = '结果😀结果'
    api.fetchProject.mockResolvedValue(current)
    api.fetchSubmission.mockResolvedValue({ submission: { id: 's1' }, participant: { project_id: 'p1' },
      version: { content_snapshot: { sections: current.sections } },
      comments: [{ id: 'comment-1', submission_id: 's1', block_id: 'target', quoted_text: '结果', text_start: 3, text_end: 5 }] })
    await open()
    const textarea = wrapper.get('.review-target textarea').element
    expect(wrapper.get('[data-test="review-location-status"]').text()).toContain('writer.reviewLocation.textFound')
    expect([textarea.selectionStart, textarea.selectionEnd]).toEqual([4, 6])
    expect(textarea.value.slice(textarea.selectionStart, textarea.selectionEnd)).toBe('结果')
  })

  it('does not select an old offset after the draft paragraph changes', async () => {
    route.query.review_comment = 'comment-1'
    const current = project()
    current.sections[0].paragraphs[0].text = '结果😀结果（已修改）'
    api.fetchProject.mockResolvedValue(current)
    api.fetchSubmission.mockResolvedValue({ submission: { id: 's1' }, participant: { project_id: 'p1' },
      version: { content_snapshot: { sections: [{ key: 'chapter', paragraphs: [{ id: 'target', text: '结果😀结果' }] }] } },
      comments: [{ id: 'comment-1', submission_id: 's1', block_id: 'target', quoted_text: '结果', text_start: 3, text_end: 5 }] })
    await open()
    expect(wrapper.get('[data-test="review-location-status"]').text()).toContain('writer.reviewLocation.textChanged')
    expect(document.activeElement).not.toBe(wrapper.get('.review-target textarea').element)
  })

  it('refuses a text location from an unrelated or invalid submission', async () => {
    route.query.review_comment = 'comment-1'
    api.fetchSubmission.mockResolvedValue({ submission: { id: 's1' }, participant: { project_id: 'other-project' },
      version: { content_snapshot: { sections: [chapter] } },
      comments: [{ id: 'comment-1', block_id: 'target', quoted_text: '现在', text_start: 0, text_end: 2 }] })
    await open()
    expect(wrapper.get('[data-test="review-location-status"]').text()).toContain('writer.reviewLocation.textUnavailable')
    expect(document.activeElement).not.toBe(wrapper.get('.review-target textarea').element)
  })

  it('reports a removed or ambiguous block instead of pointing to another location', async () => {
    route.query.review_block = 'removed'
    await open()
    expect(wrapper.get('[data-test="review-location-status"]').text()).toContain('writer.reviewLocation.missing')
    expect(wrapper.find('.review-target').exists()).toBe(false)
    wrapper.unmount()
    route.query.review_block = 'target'
    const duplicate = project()
    duplicate.sections.push({ ...structuredClone(chapter), key: 'other' })
    api.fetchProject.mockResolvedValue(duplicate)
    await open()
    expect(wrapper.get('[data-test="review-location-status"]').text()).toContain('writer.reviewLocation.missing')
    expect(wrapper.find('.review-target').exists()).toBe(false)
  })

  it('preserves a pending recovery draft before navigating', async () => {
    const saved = { project_id: 'p1', user_id: 'student', base_revision: 1, saved_at: '2026-10-30',
      snapshot: { title: '待恢复的论文', meta: {}, sections: [chapter] } }
    localStorage.setItem('pg_recovery:student:p1', JSON.stringify(saved))
    await open()
    expect(wrapper.get('[data-test="review-location-status"]').text()).toContain('writer.reviewLocation.pendingDraft')
    expect(wrapper.find('.review-target').exists()).toBe(false)
    expect(JSON.parse(localStorage.getItem('pg_recovery:student:p1'))).toEqual(saved)
  })
})
