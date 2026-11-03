import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../src/i18n', () => ({
  useI18n: () => ({ t: key => key }),
}))

import ReferencesEditor from '../src/components/writer/ReferencesEditor.vue'

describe('ReferencesEditor', () => {
  it('reconciles legacy reference text edits while retaining unaffected inline formatting', async () => {
    const paragraphs = [{ id: 'legacy', type: 'paragraph', text: '作者. 学报[J]', inline_runs: [
      { text: '作者. ' }, { text: '学报', italic: true }, { text: '[J]' },
    ] }]
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })
    await wrapper.get('.reference-card textarea').setValue('作者甲. 学报[J]')
    expect(paragraphs[0].text).toBe('作者甲. 学报[J]')
    expect(paragraphs[0].inline_runs).toMatchObject([
      { text: '作者甲. ', italic: false }, { text: '学报', italic: true }, { text: '[J]', italic: false },
    ])
    expect(paragraphs[0].inline_runs.map(run => run.text).join('')).toBe(paragraphs[0].text)
    expect(wrapper.emitted('change')).toHaveLength(1)
    wrapper.unmount()
  })

  it('does not schedule an edit when stored inline formatting is inconsistent', async () => {
    const paragraphs = [{ id: 'legacy', type: 'paragraph', text: '原文', inline_runs: [{ text: '不匹配', italic: true }] }]
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })
    await wrapper.get('.reference-card textarea').setValue('修改')
    expect(paragraphs[0].text).toBe('原文')
    expect(wrapper.get('.reference-card textarea').element.value).toBe('原文')
    expect(wrapper.emitted('change')).toBeUndefined()
    expect(wrapper.get('[role="alert"]').text()).toContain('blockList.inlineFormatInvalid')
    wrapper.unmount()
  })

  it('keeps legacy inline text and formatting when choosing a structured reference type', async () => {
    const paragraphs = [{ id: 'legacy', type: 'paragraph', text: '作者. 学报[J]', inline_runs: [
      { text: '作者. ' }, { text: '学报', italic: true }, { text: '[J]' },
    ] }]
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })
    await wrapper.get('.reference-card select').setValue('journal')
    expect(paragraphs[0].reference.unformatted_text).toBe('作者. 学报[J]')
    expect(paragraphs[0].text).toBe('作者. 学报[J]')
    expect(paragraphs[0].inline_runs.map(run => run.text).join('')).toBe(paragraphs[0].text)
    expect(paragraphs[0].inline_runs[1].italic).toBe(true)
    wrapper.unmount()
  })

  it('does not convert a legacy entry whose inline text cannot be reconciled', async () => {
    const paragraphs = [{ id: 'legacy', type: 'paragraph', text: '原文', inline_runs: [{ text: '不匹配', italic: true }] }]
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })
    await wrapper.get('.reference-card select').setValue('journal')
    expect(paragraphs[0].reference).toBeUndefined()
    expect(wrapper.get('.reference-card select').element.value).toBe('legacy')
    expect(wrapper.emitted('change')).toBeUndefined()
    expect(wrapper.get('[role="alert"]').text()).toContain('blockList.inlineFormatInvalid')
    wrapper.unmount()
  })

  it('converts legacy text to a structured entry and regenerates its preview', async () => {
    const paragraphs = [{ id: 'legacy-1', type: 'paragraph', text: '[7] 张三. 旧题名. 2020.', reference_number: 7 }]
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })

    await wrapper.find('select').setValue('journal')
    expect(paragraphs[0].reference.unformatted_text).toContain('旧题名')
    expect(paragraphs[0].reference.original_pasted_text).toBe('[7] 张三. 旧题名. 2020.')
    await wrapper.findAll('input')[0].setValue('张三')
    await wrapper.findAll('input')[1].setValue('新题名')
    await flushPromises()

    expect(wrapper.text()).toContain('[7] 张三. 新题名[J]')
    expect(wrapper.emitted('change').length).toBeGreaterThan(1)
    wrapper.unmount()
  })

  it('reorders entries and keeps legacy entries available for editing', async () => {
    const paragraphs = [
      { id: 'one', type: 'paragraph', text: '[1] 作者甲. 文献甲. 2020.' },
      { id: 'two', type: 'paragraph', text: '[2] 作者乙. 文献乙. 2021.' },
    ]
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })

    await wrapper.findAll('.reference-card')[1].findAll('.block-move-btn')[0].trigger('click')

    expect(paragraphs[0].id).toBe('two')
    expect(wrapper.findAll('.reference-card')[0].text()).toContain('[2]')
    expect(wrapper.findAll('.reference-card')[1].text()).toContain('[1]')
    expect(wrapper.findAll('textarea')).toHaveLength(2)
    expect(wrapper.emitted('change')).toHaveLength(1)
    wrapper.unmount()
  })

  it('keeps entered fields when changing reference types', async () => {
    const paragraphs = [{ id: 'ref', type: 'paragraph', text: '', reference: {
      kind: 'journal', authors: '张三', title: '已有题名', source: '期刊', unformatted_text: '',
    } }]
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })
    await wrapper.find('select').setValue('book')
    expect(paragraphs[0].reference.authors).toBe('张三')
    expect(paragraphs[0].reference.title).toBe('已有题名')
    expect(paragraphs[0].reference.source).toBe('期刊')
    expect(wrapper.find('option[value="legacy"]').exists()).toBe(false)
    await wrapper.find('select').setValue('journal')
    expect(paragraphs[0].reference.title).toBe('已有题名')
    wrapper.unmount()
  })

  it('shows saved numbers and leaves new entries unnumbered until saved', () => {
    const paragraphs = [
      { id: 'saved', type: 'paragraph', text: '旧文献', reference_number: 4 },
      { id: 'new', type: 'paragraph', text: '新文献' },
    ]
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })
    expect(wrapper.findAll('.reference-card')[0].get('strong').text()).toBe('[4]')
    expect(wrapper.findAll('.reference-card')[1].get('strong').text()).toBe('writer.refNumberPending')
    wrapper.unmount()
  })

  it('previews pasted references and keeps the original after choosing a type', async () => {
    const paragraphs = []
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })
    await wrapper.find('.reference-actions button').trigger('click')
    await wrapper.get('[data-test="reference-paste-input"]').setValue('[1] 原文甲\n续行\n[1] 原文乙')
    expect(wrapper.findAll('.reference-paste-preview li')).toHaveLength(2)
    await wrapper.get('[data-test="reference-paste-add"]').trigger('click')
    expect(paragraphs.map(block => block.reference.original_pasted_text)).toEqual(['[1] 原文甲\n续行', '[1] 原文乙'])
    expect(new Set(paragraphs.map(block => block.id)).size).toBe(2)
    expect(wrapper.findAll('.reference-card strong').map(item => item.text())).toEqual(['writer.refNumberPending', 'writer.refNumberPending'])
    await wrapper.findAll('.reference-card')[0].get('select').setValue('journal')
    expect(paragraphs[0].reference.kind).toBe('journal')
    expect(paragraphs[0].reference.original_pasted_text).toBe('[1] 原文甲\n续行')
    expect(wrapper.emitted('change')).toHaveLength(2)
    wrapper.unmount()
  })

  it('extracts supported pasted journals in a batch and keeps unresolved entries editable', async () => {
    const paragraphs = []
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })
    const raw = '[1] 张三. 研究题名[J]. 示例期刊, 2024, 12(3): 1-9. DOI:10.1234/example.\n[2] 未知条目'
    await wrapper.find('.reference-actions button').trigger('click')
    await wrapper.get('[data-test="reference-paste-input"]').setValue(raw)
    expect(wrapper.get('[data-test="reference-paste-recognition"]').exists()).toBe(true)
    await wrapper.get('[data-test="reference-paste-add"]').trigger('click')
    expect(paragraphs[0].reference).toMatchObject({ kind: 'journal', title: '研究题名', source: '示例期刊', doi: '10.1234/example' })
    expect(paragraphs[0].reference.original_pasted_text).toBe(raw.split('\n')[0])
    expect(paragraphs[1].reference).toMatchObject({ kind: 'unparsed', original_pasted_text: '[2] 未知条目' })
    expect(wrapper.findAll('.reference-card')[0].find('[data-test="reference-apply-suggestion"]').exists()).toBe(false)
    expect(wrapper.findAll('.reference-card')[1].text()).toContain('writer.refNeedsFacts')
    expect(wrapper.emitted('change')).toHaveLength(1)
    wrapper.unmount()
  })

  it('recognizes and keeps the original of a pasted 1101 book entry', async () => {
    const paragraphs = []
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })
    const raw = '[1] 张炯.Unix网络编程实用技术与实例分析[M].清华大学出版社，2002.'
    await wrapper.find('.reference-actions button').trigger('click')
    await wrapper.get('[data-test="reference-paste-input"]').setValue(raw)
    await wrapper.get('[data-test="reference-paste-add"]').trigger('click')
    expect(paragraphs[0].reference).toMatchObject({
      kind: 'book', authors: '张炯', title: 'Unix网络编程实用技术与实例分析',
      place: '', publisher: '清华大学出版社', year: '2002', original_pasted_text: raw,
    })
    expect(wrapper.findAll('.reference-card')[0].get('details.reference-original pre').text()).toBe(raw)
    expect(wrapper.emitted('change')).toHaveLength(1)
    wrapper.unmount()
  })

  it('extracts thesis facts from a pasted entry and keeps the original', async () => {
    const paragraphs = []
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })
    const raw = '[1] 王某. 信息系统研究[D]. 示例大学，2004.'
    await wrapper.find('.reference-actions button').trigger('click')
    await wrapper.get('[data-test="reference-paste-input"]').setValue(raw)
    await wrapper.get('[data-test="reference-paste-add"]').trigger('click')
    expect(paragraphs[0].reference).toMatchObject({
      kind: 'thesis', authors: '王某', title: '信息系统研究', institution: '示例大学',
      place: '', year: '2004', original_pasted_text: raw,
    })
    expect(wrapper.findAll('.reference-card')[0].get('details.reference-original pre').text()).toBe(raw)
    expect(wrapper.emitted('change')).toHaveLength(1)
    wrapper.unmount()
  })

  it('lets the student apply a factual suggestion without overwriting edits or the source', async () => {
    const original = '[1] 张三. 研究题名[J]. 示例期刊, 2024, 12(3): 1-9.'
    const paragraphs = [{ id: 'pasted', type: 'paragraph', text: '', reference: {
      kind: 'unparsed', authors: '已修正作者', unformatted_text: original, original_pasted_text: original,
    } }]
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })
    await wrapper.get('[data-test="reference-apply-suggestion"]').trigger('click')
    expect(paragraphs[0].reference).toMatchObject({
      kind: 'journal', authors: '已修正作者', title: '研究题名', source: '示例期刊', year: '2024',
      volume: '12', issue: '3', pages: '1-9', original_pasted_text: original,
    })
    expect(wrapper.emitted('change')).toHaveLength(1)
    wrapper.unmount()
  })

  it('records language selections separately from the title and requests explicit confirmation', async () => {
    const paragraphs = [{ id: 'ref', type: 'paragraph', text: '', reference: {
      kind: 'journal', title: 'An English title', language_fact: null,
    } }]
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })
    expect(wrapper.get('[data-test="reference-language-status"]').text()).toBe('writer.refLanguageUnconfirmed')
    expect(paragraphs[0].reference.language_fact).toBeNull()
    await wrapper.get('[data-test="reference-language-en"]').setValue(true)
    await wrapper.get('[data-test="reference-language-zh"]').setValue(true)
    expect(paragraphs[0].reference.language_fact.languages).toEqual(['en', 'zh'])
    expect(paragraphs[0].reference.language_fact.status).toBe('unconfirmed')
    await wrapper.get('[data-test="reference-language-confirm"]').trigger('click')
    expect(wrapper.emitted('confirm-language')[0][0]).toEqual(paragraphs[0])
    expect(paragraphs[0].reference.language_fact.status).toBe('unconfirmed')
    wrapper.unmount()
  })

  it('invalidates the displayed confirmation when the reference identity changes', async () => {
    const paragraphs = [{ id: 'ref', type: 'paragraph', text: '', reference: {
      kind: 'journal', title: '旧题名', language_fact: {
        languages: ['en'], evidence: '', status: 'confirmed', confirmed_by: 'student', confirmed_at: 'now',
      },
    } }]
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })
    expect(wrapper.get('[data-test="reference-language-status"]').text()).toBe('writer.refLanguageConfirmed')
    await wrapper.get('[data-test="reference-field-title"]').setValue('新题名')
    expect(paragraphs[0].reference.language_fact.status).toBe('unconfirmed')
    expect(wrapper.get('[data-test="reference-language-status"]').text()).toBe('writer.refLanguageUnconfirmed')
    wrapper.unmount()
  })

  it('requires an explicit publication-year confirmation and invalidates it after edits', async () => {
    const paragraphs = [{ id: 'ref', type: 'paragraph', text: '', reference: {
      kind: 'journal', title: '论文题名', year: '2024', year_fact: null,
    } }]
    const wrapper = mount(ReferencesEditor, { props: { paragraphs } })
    expect(wrapper.get('[data-test="reference-year-status"]').text()).toBe('writer.refYearUnconfirmed')
    await wrapper.get('[data-test="reference-year-confirm"]').trigger('click')
    expect(wrapper.emitted('confirm-year')[0][0]).toEqual(paragraphs[0])
    expect(paragraphs[0].reference.year_fact).toMatchObject({ value: '2024', status: 'unconfirmed' })
    Object.assign(paragraphs[0].reference.year_fact, { status: 'confirmed', confirmed_by: 'student', confirmed_at: 'now' })
    await wrapper.get('[data-test="reference-field-year"]').setValue('2025')
    expect(paragraphs[0].reference.year_fact).toMatchObject({ value: '2025', status: 'unconfirmed', confirmed_by: '' })
    wrapper.unmount()
  })
})
