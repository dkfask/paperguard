import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  fetchProject: vi.fn(), fetchProjectExportJobs: vi.fn(), fetchProjectCapabilities: vi.fn(),
  updateProjectSection: vi.fn(), updateProjectMeta: vi.fn(), previewProjectDocx: vi.fn(),
  fetchProjectDocxCandidate: vi.fn(), reviseProjectDocxCandidate: vi.fn(),
  applyProjectDocxCandidate: vi.fn(), restoreProjectSnapshot: vi.fn(),
}))

vi.mock('../src/api.js', async () => {
  const actual = await vi.importActual('../src/api.js')
  return { ...Object.fromEntries(Object.keys(actual).map(key => [key, vi.fn().mockResolvedValue([])])), ...api }
})
vi.mock('../src/auth', () => ({ auth: { user: { id: 'student', role: 'student', status: 'active' } }, isManager: () => false }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 'p1' }, query: {} }), useRouter: () => ({ push: vi.fn() }), onBeforeRouteLeave: vi.fn() }))
vi.mock('../src/i18n', () => ({ useI18n: () => ({ t: key => key, formatDate: value => value }) }))

import Writer from '../src/views/Writer.vue'
import TiptapSectionEditor from '../src/features/writer/components/TiptapSectionEditor.vue'
import { auth } from '../src/auth'

const baseSection = { key: 'chapter', type: 'heading1', title: '原章节', paragraphs: [{ id: 'old', type: 'paragraph', text: '原有草稿' }] }
function project() {
  return { id: 'p1', title: '原论文', revision: 3, meta: {}, sections: [structuredClone(baseSection)],
    template_snapshot: { id: 't1', styles: {}, meta_fields: [], sections: [{ key: 'chapter', type: 'heading1', title: '原章节' }] } }
}
function candidate(overrides = {}) {
  return { candidate_id: 'candidate-1', candidate_sha256: 'digest-1', source_sha256: 'source-1', project_revision: 3,
    filename: 'paper.docx', stats: { paragraphs: 1, tables: 0, images: 0 }, warnings: [],
    heading_candidates: [{ id: 'p304', text: '5.3 标题', suggested: 'heading2', decision: null }],
    sections: [{ key: 'new', title: '新章节', paragraphs: [] }], ...overrides }
}
function open() {
  return mount(Writer, { attachTo: document.body, global: { stubs: {
    Teleport: true, BaseModal: true, RouterLink: true, BlockList: true,
    SectionTextEditor: { props: ['modelValue'], emits: ['update:modelValue', 'apply'],
      template: '<textarea data-test="section-text-draft" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />' },
  } } })
}
function openWithSectionEditor() {
  return mount(Writer, { attachTo: document.body, global: { stubs: {
    Teleport: true, BaseModal: true, RouterLink: true, BlockList: true,
  } } })
}
async function selectFile(wrapper) {
  const input = wrapper.get('input[type="file"][accept=".docx"]')
  Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['docx'], 'paper.docx')] })
  await input.trigger('change')
  await flushPromises()
}
async function selectChapter(wrapper) {
  await wrapper.findAll('.nav-row').find(row => row.text().includes('原章节')).trigger('click')
  await flushPromises()
}
function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  api.fetchProject.mockResolvedValue(project())
  api.fetchProjectExportJobs.mockResolvedValue([])
  api.fetchProjectCapabilities.mockResolvedValue({ manuscript_import_enabled: true, template_migration_enabled: true, formal_delivery_enabled: true })
  api.previewProjectDocx.mockResolvedValue(candidate())
  api.fetchProjectDocxCandidate.mockResolvedValue(candidate())
})
afterEach(() => { document.body.innerHTML = ''; vi.useRealTimers() })

describe('whole Word manuscript import', () => {
  it('keeps the editor usable and preserves recovery when an explicit cover save fails', async () => {
    const source = project()
    source.sections = [{ key: 'cover', type: 'cover', title: '封面', paragraphs: [] }]
    source.template_snapshot.sections = [{ key: 'cover', type: 'cover', title: '封面' }]
    source.template_snapshot.meta_fields = [{ key: 'class', label: '班级' }]
    api.fetchProject.mockResolvedValue(source)
    api.updateProjectMeta.mockRejectedValue(new Error('项目已归档'))
    const onError = vi.fn()
    const wrapper = mount(Writer, { attachTo: document.body, global: { config: { errorHandler: onError }, stubs: {
      Teleport: true, BaseModal: true, RouterLink: true, BlockList: true,
    } } })
    await flushPromises()
    await wrapper.get('.meta-row input').setValue('未保存班级')
    await wrapper.findAll('button').find(button => button.text().includes('writer.saveCover')).trigger('click')
    await flushPromises()
    expect(onError).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('项目已归档')
    expect(wrapper.find('.save-failure').exists()).toBe(true)
    expect(JSON.parse(localStorage.getItem('pg_recovery:student:p1')).snapshot.meta.class).toBe('未保存班级')
    wrapper.unmount()
  })

  it('hides the import entry when its capability is closed or unavailable', async () => {
    api.fetchProjectCapabilities.mockResolvedValueOnce({ manuscript_import_enabled: false })
    const closed = open(); await flushPromises()
    expect(closed.find('.writer-import-action').exists()).toBe(false)
    expect(closed.find('[data-test="resume-docx-import"]').exists()).toBe(false)
    expect(closed.find('input[type="file"][accept=".docx"]').exists()).toBe(false)
    closed.unmount()

    api.fetchProjectCapabilities.mockRejectedValueOnce(new Error('unavailable'))
    const unavailable = open(); await flushPromises()
    expect(unavailable.find('.writer-import-action').exists()).toBe(false)
    expect(unavailable.find('[data-test="resume-docx-import"]').exists()).toBe(false)
    unavailable.unmount()
  })

  it('requires a new preview after decisions and applies that exact candidate', async () => {
    const revised = candidate({ candidate_id: 'candidate-2', candidate_sha256: 'digest-2',
      heading_candidates: [{ id: 'p304', text: '5.3 标题', suggested: 'heading2', decision: 'heading2' }] })
    api.reviseProjectDocxCandidate.mockResolvedValue(revised)
    api.applyProjectDocxCandidate.mockResolvedValue({ project: { ...project(), revision: 4 }, stats: { paragraphs: 1, tables: 0 } })
    const wrapper = open(); await flushPromises(); await selectFile(wrapper)
    expect(api.previewProjectDocx).toHaveBeenCalledOnce()
    expect(wrapper.get('[data-test="apply-docx-import"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-test="import-heading-p304"]').setValue('heading2')
    await wrapper.get('[data-test="update-docx-preview"]').trigger('click'); await flushPromises()
    expect(api.reviseProjectDocxCandidate).toHaveBeenCalledWith('p1', 'candidate-1', { p304: 'heading2' })
    expect(wrapper.get('[data-test="apply-docx-import"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-test="apply-docx-import"]').trigger('click'); await flushPromises()
    expect(api.applyProjectDocxCandidate).toHaveBeenCalledWith('p1', revised)
    expect(wrapper.find('[data-test="apply-docx-import"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('resumes a server candidate with its decisions and digest', async () => {
    const saved = candidate({ heading_candidates: [{ id: 'p304', text: '5.3 标题', suggested: 'heading2', decision: 'body' }] })
    api.fetchProjectDocxCandidate.mockResolvedValue(saved)
    const wrapper = open(); await flushPromises()
    await wrapper.get('[data-test="resume-docx-import"]').trigger('click'); await flushPromises()
    expect(wrapper.get('[data-test="import-heading-p304"]').element.value).toBe('body')
    expect(wrapper.get('[data-test="apply-docx-import"]').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('preserves an unresolved local recovery draft and does not upload', async () => {
    const draft = { project_id: 'p1', user_id: 'student', base_revision: 2, saved_at: '2026-10-28',
      snapshot: { title: '需要恢复的稿件', meta: {}, sections: [baseSection] } }
    localStorage.setItem('pg_recovery:student:p1', JSON.stringify(draft))
    const wrapper = open(); await flushPromises(); await selectFile(wrapper)
    expect(api.previewProjectDocx).not.toHaveBeenCalled()
    expect(JSON.parse(localStorage.getItem('pg_recovery:student:p1'))).toEqual(draft)
    expect(wrapper.text()).toContain('writer.importResolveRecovery')
    wrapper.unmount()
  })

  it('does not offer another account’s local recovery draft', async () => {
    const draft = { project_id: 'p1', user_id: 'student', base_revision: 2, saved_at: '2026-10-28',
      snapshot: { title: '其他用户的草稿', meta: {}, sections: [baseSection] } }
    localStorage.setItem('pg_recovery:student:p1', JSON.stringify(draft))
    auth.user = { id: 'another-student', role: 'student', status: 'active' }
    const wrapper = open()
    try {
      await flushPromises()
      expect(wrapper.text()).not.toContain('writer.recoveryFound')
      expect(JSON.parse(localStorage.getItem('pg_recovery:student:p1'))).toEqual(draft)
    } finally {
      auth.user = { id: 'student', role: 'student', status: 'active' }
      wrapper.unmount()
    }
  })

  it('does not import while section text has not been applied', async () => {
    const wrapper = open(); await flushPromises(); await selectChapter(wrapper)
    await wrapper.findAll('.editor-head button').find(button => button.text().includes('writer.sectionModeBlocks')).trigger('click')
    await wrapper.get('[data-test="section-text-draft"]').setValue('尚未应用的整节文字')
    await selectFile(wrapper)
    expect(api.previewProjectDocx).not.toHaveBeenCalled()
    expect(wrapper.get('[data-test="section-text-draft"]').element.value).toBe('尚未应用的整节文字')
    expect(wrapper.text()).toContain('writer.importApplySectionText')
    wrapper.unmount()
  })

  it('does not upload when saving the current draft fails', async () => {
    api.updateProjectSection.mockRejectedValue(new Error('保存失败'))
    const wrapper = open(); await flushPromises(); await selectChapter(wrapper)
    await wrapper.get('.heading-input').setValue('未保存标题')
    await selectFile(wrapper)
    expect(api.previewProjectDocx).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('保存失败')
    expect(JSON.parse(localStorage.getItem('pg_recovery:student:p1')).snapshot.sections[0].title).toBe('未保存标题')
    wrapper.unmount()
  })

  it('keeps the original user recovery key when authentication expires during a save', async () => {
    api.updateProjectSection.mockImplementationOnce(async () => {
      auth.user = null
      throw new Error('会话已过期')
    })
    const wrapper = open(); await flushPromises(); await selectChapter(wrapper)
    try {
      await wrapper.get('.heading-input').setValue('会话过期前的未保存标题')
      await selectFile(wrapper)
      const draft = JSON.parse(localStorage.getItem('pg_recovery:student:p1'))
      expect(draft.user_id).toBe('student')
      expect(draft.snapshot.sections[0].title).toBe('会话过期前的未保存标题')
      expect(localStorage.getItem('pg_recovery:anonymous:p1')).toBeNull()
    } finally {
      auth.user = { id: 'student', role: 'student', status: 'active' }
      wrapper.unmount()
    }
  })

  it.each([
    ['阶段已归档', undefined, false],
    ['服务器版本已变化', 'project_revision_conflict', true],
  ])('only shows the revision conflict recovery for a revision conflict: %s', async (message, code, conflict) => {
    api.updateProjectSection.mockRejectedValue(Object.assign(new Error(message), { status: 409, code }))
    const wrapper = open(); await flushPromises(); await selectChapter(wrapper)
    await wrapper.get('.heading-input').setValue('未保存标题')
    await selectFile(wrapper)
    expect(wrapper.text()).toContain(message)
    expect(wrapper.find('.conflict-banner').exists()).toBe(conflict)
    expect(JSON.parse(localStorage.getItem('pg_recovery:student:p1')).snapshot.sections[0].title).toBe('未保存标题')
    wrapper.unmount()
  })

  it('rejects a preview if the local draft changes during upload', async () => {
    const upload = deferred()
    api.previewProjectDocx.mockReturnValue(upload.promise)
    const wrapper = open(); await flushPromises(); await selectChapter(wrapper)
    const input = wrapper.get('input[type="file"][accept=".docx"]')
    Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['docx'], 'paper.docx')] })
    await input.trigger('change'); await flushPromises()
    await wrapper.get('.heading-input').setValue('上传中修改')
    upload.resolve(candidate()); await flushPromises()
    expect(wrapper.find('[data-test="apply-docx-import"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('writer.importDraftChanged')
    wrapper.unmount()
  })

  it('preserves local edits made while the application request is in flight', async () => {
    const application = deferred()
    const ready = candidate({ heading_candidates: [] })
    api.previewProjectDocx.mockResolvedValue(ready)
    api.applyProjectDocxCandidate.mockReturnValue(application.promise)
    const wrapper = open(); await flushPromises(); await selectChapter(wrapper); await selectFile(wrapper)
    await wrapper.get('[data-test="apply-docx-import"]').trigger('click'); await flushPromises()
    await wrapper.get('.heading-input').setValue('应用期间的本地修改')
    application.resolve({ project: { ...project(), revision: 4 }, stats: { paragraphs: 1, tables: 0 } }); await flushPromises()
    expect(wrapper.get('.heading-input').element.value).toBe('应用期间的本地修改')
    expect(JSON.parse(localStorage.getItem('pg_recovery:student:p1')).snapshot.sections[0].title).toBe('应用期间的本地修改')
    expect(wrapper.text()).toContain('writer.importApplyConflict')
    wrapper.unmount()
  })

  it('keeps unapplied section text available after an import finishes and the page reloads', async () => {
    const application = deferred()
    api.previewProjectDocx.mockResolvedValue(candidate({ heading_candidates: [] }))
    api.applyProjectDocxCandidate.mockReturnValue(application.promise)
    const wrapper = open(); await flushPromises(); await selectChapter(wrapper); await selectFile(wrapper)
    await wrapper.get('[data-test="apply-docx-import"]').trigger('click'); await flushPromises()
    await wrapper.findAll('.editor-head button').find(button => button.text().includes('writer.sectionModeBlocks')).trigger('click')
    await wrapper.get('[data-test="section-text-draft"]').setValue('请求期间尚未应用的整节文字')
    const imported = { ...project(), revision: 4 }
    application.resolve({ project: imported, stats: { paragraphs: 1, tables: 0 } }); await flushPromises()
    expect(JSON.parse(localStorage.getItem('pg_recovery:student:p1')).section_text_draft)
      .toEqual({ section_key: 'chapter', text: '请求期间尚未应用的整节文字' })
    wrapper.unmount()

    api.fetchProject.mockResolvedValue(imported)
    const reloaded = open(); await flushPromises()
    expect(reloaded.get('[data-test="recovery-section-text"]').element.value).toBe('请求期间尚未应用的整节文字')
    reloaded.unmount()
  })

  it('restores unapplied section text when its underlying draft has not changed', async () => {
    const wrapper = open(); await flushPromises(); await selectChapter(wrapper)
    await wrapper.findAll('.editor-head button').find(button => button.text().includes('writer.sectionModeBlocks')).trigger('click')
    await wrapper.get('[data-test="section-text-draft"]').setValue('等待应用的整节文字')
    wrapper.unmount()

    const reloaded = open(); await flushPromises()
    expect(reloaded.get('[data-test="recovery-section-text"]').element.value).toBe('等待应用的整节文字')
    await reloaded.findAll('.recovery-banner button').find(button => button.text().includes('writer.restoreDraft')).trigger('click')
    await flushPromises()
    expect(reloaded.get('[data-test="section-text-draft"]').element.value).toBe('等待应用的整节文字')
    expect(JSON.parse(localStorage.getItem('pg_recovery:student:p1')).section_text_draft.text).toBe('等待应用的整节文字')
    reloaded.unmount()
  })

  it('restores and applies structured section edits without moving media or losing formatting', async () => {
    const source = project()
    source.sections[0].paragraphs = [
      { id: 'p1', type: 'paragraph', text: '正文', inline_runs: [{ text: '正文', bold: true }] },
      { id: 'img1', type: 'image', image: { url: '/media/p.png', caption: '图' } },
      { id: 'p2', type: 'paragraph', text: '图片之后' },
    ]
    api.fetchProject.mockResolvedValue(source)
    let wrapper = openWithSectionEditor(); await flushPromises(); await selectChapter(wrapper)
    await wrapper.findAll('.editor-head button').find(button => button.text().includes('writer.sectionModeBlocks')).trigger('click')
    await flushPromises()
    let editor = wrapper.findComponent(TiptapSectionEditor).vm.$.setupState.editor
    editor.commands.insertContentAt(2, '新')
    await flushPromises()
    const draft = JSON.parse(localStorage.getItem('pg_recovery:student:p1')).section_text_draft
    expect(draft.document.content.map(node => node.type)).toEqual(['paragraph', 'paperMedia', 'paragraph'])
    wrapper.unmount()

    wrapper = openWithSectionEditor(); await flushPromises()
    await wrapper.findAll('.recovery-banner button').find(button => button.text().includes('writer.restoreDraft')).trigger('click')
    await flushPromises()
    editor = wrapper.findComponent(TiptapSectionEditor).vm.$.setupState.editor
    expect(editor.getText()).toContain('正新文')
    expect(JSON.parse(localStorage.getItem('pg_recovery:student:p1')).section_text_draft.document).toBeTruthy()
    api.updateProjectSection.mockResolvedValue({ ...source, revision: 4 })
    vi.useFakeTimers()
    await wrapper.get('.section-text-editor-head button').trigger('click')
    await vi.advanceTimersByTimeAsync(900)
    await flushPromises()
    const sent = api.updateProjectSection.mock.calls.at(-1)[2].paragraphs
    expect(sent.map(block => block.id)).toEqual(['p1', 'img1', 'p2'])
    expect(sent[0].text).toBe('正新文')
    expect(sent[0].inline_runs.every(run => run.bold)).toBe(true)
    expect(sent[1]).toEqual(source.sections[0].paragraphs[1])
    wrapper.unmount()
  })

  it('keeps pending section text in place when switching modes or chapters', async () => {
    const wrapper = open(); await flushPromises(); await selectChapter(wrapper)
    await wrapper.findAll('.editor-head button').find(button => button.text().includes('writer.sectionModeBlocks')).trigger('click')
    await wrapper.get('[data-test="section-text-draft"]').setValue('尚未应用')
    await wrapper.findAll('.editor-head button').find(button => button.text().includes('writer.sectionModeParagraphs')).trigger('click')
    await wrapper.findAll('.nav-row').find(row => row.text().includes('writer.coverSection')).trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="section-text-draft"]').element.value).toBe('尚未应用')
    expect(wrapper.text()).toContain('writer.sectionTextPending')
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    wrapper.unmount()
  })

  it('preserves even an empty section draft until it is applied', async () => {
    const wrapper = open(); await flushPromises(); await selectChapter(wrapper)
    await wrapper.findAll('.editor-head button').find(button => button.text().includes('writer.sectionModeBlocks')).trigger('click')
    await wrapper.get('[data-test="section-text-draft"]').setValue('')
    expect(JSON.parse(localStorage.getItem('pg_recovery:student:p1')).section_text_draft.text).toBe('')
    await wrapper.findAll('.nav-row').find(row => row.text().includes('writer.coverSection')).trigger('click')
    expect(wrapper.get('[data-test="section-text-draft"]').element.value).toBe('')
    wrapper.unmount()
  })

  it('retains the new draft and updated base revision when a save finishes during editing', async () => {
    const save = deferred()
    api.updateProjectSection.mockReturnValue(save.promise)
    const wrapper = open(); await flushPromises(); await selectChapter(wrapper)
    await wrapper.get('.heading-input').setValue('新标题')
    const input = wrapper.get('input[type="file"][accept=".docx"]')
    Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['docx'], 'paper.docx')] })
    await input.trigger('change'); await flushPromises()
    await wrapper.findAll('.editor-head button').find(button => button.text().includes('writer.sectionModeBlocks')).trigger('click')
    await wrapper.get('[data-test="section-text-draft"]').setValue('保存期间输入')
    save.resolve({ revision: 4 }); await flushPromises()
    const saved = JSON.parse(localStorage.getItem('pg_recovery:student:p1'))
    expect(saved.base_revision).toBe(4)
    expect(saved.section_text_draft.text).toBe('保存期间输入')
    expect(api.previewProjectDocx).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('keeps section text copyable when restoring a conflicting server revision fails', async () => {
    const saved = { project_id: 'p1', user_id: 'student', base_revision: 2, saved_at: '2026-10-28',
      snapshot: { title: '未同步标题', meta: {}, sections: [baseSection] },
      section_text_draft: { section_key: 'chapter', text: '待合并文字' } }
    localStorage.setItem('pg_recovery:student:p1', JSON.stringify(saved))
    api.restoreProjectSnapshot.mockRejectedValue(Object.assign(new Error('版本冲突'), { status: 409 }))
    const wrapper = open(); await flushPromises()
    await wrapper.findAll('.recovery-banner button').find(button => button.text().includes('writer.restoreDraft')).trigger('click')
    await flushPromises()
    expect(api.restoreProjectSnapshot).toHaveBeenCalledWith('p1', saved.snapshot, 2)
    expect(wrapper.get('[data-test="recovery-section-text"]').element.value).toBe('待合并文字')
    expect(JSON.parse(localStorage.getItem('pg_recovery:student:p1'))).toEqual(saved)
    expect(wrapper.text()).toContain('版本冲突')
    wrapper.unmount()
  })
})
