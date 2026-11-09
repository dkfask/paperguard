import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
vi.mock('../src/i18n', () => ({ useI18n: () => ({ lang: { value: 'zh' } }) }))
vi.mock('../src/api', () => ({ proposeWritingProfile: vi.fn(), publishWritingProfile: vi.fn(), prepareCandidateWritingProfile: vi.fn() }))
import { proposeWritingProfile, publishWritingProfile, prepareCandidateWritingProfile } from '../src/api'
import WritingProfileConfirmation from '../src/components/WritingProfileConfirmation.vue'
const template = { id: 't1', source_docx: 'source.docx', writing_specification: { specification_sha256: 'spec-hash', rules: [] } }
const plan = { fields: [], headings: [], object_properties: {} }
const proposal = { proposal_id: 'proposal-1', template: { source_execution: plan }, trials: { normal: {status:'pass'}, long: {status:'pass'} } }
describe('writing profile publication', () => {
  beforeEach(() => { vi.clearAllMocks(); proposeWritingProfile.mockResolvedValue(proposal); publishWritingProfile.mockResolvedValue({template:{...template,version:'1+word.test'}}) })
  it('requires a server preview and all three acknowledgements before publication', async () => {
    const wrapper = mount(WritingProfileConfirmation, { props: { template } })
    await wrapper.get('button').trigger('click'); await flushPromises()
    const publish = wrapper.get('button.primary')
    expect(publish.element.disabled).toBe(true)
    for (const checkbox of wrapper.get('[data-test="writing-profile-plan"]').findAll('input[type="checkbox"]')) await checkbox.setValue(true)
    expect(publish.element.disabled).toBe(false)
    await publish.trigger('click'); await flushPromises()
    expect(publishWritingProfile).toHaveBeenCalledWith('t1', expect.objectContaining({proposal_id:'proposal-1', expected_specification_sha256:'spec-hash', accept_recommended:true,remove_template_instructions:true,accept_new_object_defaults:true}))
    expect(wrapper.emitted('published')).toHaveLength(1)
  })
  it('shows the source-evidenced heading corrections for review', async () => {
    const correctedPlan = { ...plan, headings: [{ key: 'parent', title: '5.1 硬件实现', properties: {}, title_properties: {} }],
      heading_semantic_corrections: [{ key: 'child', title: '5.1.1 模块实现', parent_key: 'parent' }] }
    proposeWritingProfile.mockResolvedValueOnce({ ...proposal, template: { source_execution: correctedPlan } })
    const wrapper = mount(WritingProfileConfirmation, { props: { template } })
    await wrapper.get('button').trigger('click'); await flushPromises()
    const corrections = wrapper.get('[data-test="heading-semantic-corrections"]')
    expect(corrections.text()).toContain('5.1.1 模块实现')
    expect(corrections.text()).toContain('二级调整为三级')
    expect(corrections.text()).toContain('5.1 硬件实现')
  })
  it('invalidates a preview when the template becomes dirty', async () => {
    const wrapper = mount(WritingProfileConfirmation, { props: { template } })
    await wrapper.get('button').trigger('click'); await flushPromises()
    await wrapper.setProps({dirty:true})
    expect(wrapper.find('[data-test="writing-profile-plan"]').exists()).toBe(false)
    expect(wrapper.get('button').element.disabled).toBe(true)
    expect(publishWritingProfile).not.toHaveBeenCalled()
  })
  it('uses the same review path for generic and personal candidates', async () => {
    const candidate = {candidate_id:'candidate-1'}
    prepareCandidateWritingProfile.mockResolvedValue({...proposal,candidate})
    const wrapper = mount(WritingProfileConfirmation, { props: {template,candidate} })
    await wrapper.get('[data-test="writing-profile-prepare"]').trigger('click'); await flushPromises()
    expect(prepareCandidateWritingProfile).toHaveBeenCalledWith(candidate,expect.objectContaining({expected_specification_sha256:'spec-hash'}))
    for (const checkbox of wrapper.get('[data-test="writing-profile-plan"]').findAll('input[type="checkbox"]')) await checkbox.setValue(true)
    await wrapper.get('[data-test="writing-profile-publish"]').trigger('click'); await flushPromises()
    expect(prepareCandidateWritingProfile).toHaveBeenLastCalledWith(candidate,expect.objectContaining({accept_recommended:true}),false)
    expect(prepareCandidateWritingProfile.mock.invocationCallOrder[0]).toBeLessThan(prepareCandidateWritingProfile.mock.invocationCallOrder[1])
    expect(publishWritingProfile).not.toHaveBeenCalled()
  })
  it('shows readable labels instead of internal role and property keys', () => {
    const conflictTemplate = {
      ...template,
      writing_specification: {
        ...template.writing_specification,
        rules: [
          { id: 'keywords-font', confidence: 'conflict', property: 'font.eastAsia', scope: { region_title: '摘要', role: 'keywords' }, expected: '宋体', alternatives: [] },
          { id: 'english-keywords-font', confidence: 'conflict', property: 'font.ascii', scope: { region_title: 'Abstract', role: 'english_keywords' }, expected: 'Times New Roman', alternatives: [] },
        ],
      },
    }
    const wrapper = mount(WritingProfileConfirmation, { props: { template: conflictTemplate } })

    expect(wrapper.text()).toContain('摘要 · 中文关键词 · 中文字体')
    expect(wrapper.text()).toContain('Abstract · 英文关键词 · 西文字体')
    expect(wrapper.text()).not.toMatch(/english_abstract|english_keywords|\bkeywords\b/)
  })
  it('requires another server preview after changing the manuscript hierarchy policy', async () => {
    const wrapper = mount(WritingProfileConfirmation, { props: { template } })
    await wrapper.get('button').trigger('click'); await flushPromises()
    await wrapper.get('[data-test="manuscript-structure-policy"]').setValue(true)
    expect(wrapper.find('[data-test="writing-profile-plan"]').exists()).toBe(false)
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(proposeWritingProfile).toHaveBeenLastCalledWith('t1', expect.objectContaining({preserve_manuscript_structure: true}))
  })
  it('previews confirmed body page-number settings and invalidates a changed preview', async () => {
    const wrapper = mount(WritingProfileConfirmation, { props: { template } })
    await wrapper.get('[data-test="body-page-number-enabled"]').setValue(true)
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(proposeWritingProfile).toHaveBeenLastCalledWith('t1', expect.objectContaining({
      body_page_number: { font: 'Times New Roman', size_pt: 9 }
    }))
    expect(wrapper.find('[data-test="writing-profile-plan"]').exists()).toBe(true)
    await wrapper.get('[data-test="body-page-number-settings"] input[type="number"]').setValue(10)
    expect(wrapper.find('[data-test="writing-profile-plan"]').exists()).toBe(false)
  })
  it('binds the reference quality rule to a chosen cover field in a new preview', async () => {
    const qualityPlan = { ...plan, confirmed_content_rule_ids: [], defense_date_field_key: '', content_rule_review: {
      candidates: [{ rule_id: 'quality', target: 'references', source_text: '至少15篇近3年，其中5篇是英文', checker: 'references.recent_language_summary', requires_defense_date_field: true }], manual_items: []
    } }
    proposeWritingProfile.mockResolvedValueOnce({ ...proposal, template: { source_execution: qualityPlan } })
    const wrapper = mount(WritingProfileConfirmation, { props: { template: { ...template, meta_fields: [{ key: 'date', label: '日期' }] } } })
    await wrapper.get('button').trigger('click'); await flushPromises()
    await wrapper.get('[data-test="content-rule-review"] input[type="checkbox"]').setValue(true)
    await wrapper.get('[data-test="defense-date-field"]').setValue('date')
    expect(wrapper.get('button.primary').element.disabled).toBe(true)
    proposeWritingProfile.mockResolvedValueOnce({ ...proposal, template: { source_execution: { ...qualityPlan, confirmed_content_rule_ids: ['quality'], defense_date_field_key: 'date' } } })
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(proposeWritingProfile).toHaveBeenLastCalledWith('t1', expect.objectContaining({ confirmed_content_rule_ids: ['quality'], defense_date_field_key: 'date' }))
  })
  it('requires confirming the digit rule and previewing it before publication', async () => {
    const digitPlan = { ...plan, confirmed_digit_font_rule_ids: [], digit_font_review: { candidates: [
      { rule_id: 'digits', source_text: '标题、正文数字使用 Times New Roman', source_part: 'word/comments.xml', source_path: '/comment' }
    ] } }
    proposeWritingProfile.mockResolvedValueOnce({ ...proposal, trials: null, template: { source_execution: digitPlan } })
    const wrapper = mount(WritingProfileConfirmation, { props: { template } })
    await wrapper.get('button').trigger('click'); await flushPromises()
    const review = wrapper.get('[data-test="digit-font-review"]')
    expect(review.text()).toContain('Times New Roman')
    for (const checkbox of wrapper.get('[data-test="writing-profile-plan"]').findAll('input[type="checkbox"]')) {
      if (!review.findAll('input').some((input) => input.element === checkbox.element)) await checkbox.setValue(true)
    }
    expect(wrapper.get('button.primary').element.disabled).toBe(true)
    await review.get('input').setValue(true)
    expect(wrapper.get('button.primary').element.disabled).toBe(true)
    proposeWritingProfile.mockResolvedValueOnce({ ...proposal, template: { source_execution: { ...digitPlan, confirmed_digit_font_rule_ids: ['digits'] } } })
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(proposeWritingProfile).toHaveBeenLastCalledWith('t1', expect.objectContaining({ confirmed_digit_font_rule_ids: ['digits'] }))
    for (const checkbox of wrapper.get('[data-test="writing-profile-plan"]').findAll('input[type="checkbox"]')) await checkbox.setValue(true)
    expect(wrapper.get('button.primary').element.disabled).toBe(false)
  })
  it('requires a fresh preview after a keyword label is changed', async () => {
    const heading = { key: 'english_keywords', title: 'Key Words', display_label: 'Key Words',
      inline: true, separator: ': ', properties: {}, title_properties: { 'font.bold': true } }
    const keywordPlan = { ...plan, headings: [heading], inline_label_overrides: {} }
    proposeWritingProfile.mockResolvedValueOnce({ ...proposal, template: { source_execution: keywordPlan } })
    const wrapper = mount(WritingProfileConfirmation, { props: { template } })
    await wrapper.get('button').trigger('click'); await flushPromises()
    const editor = wrapper.get('[data-test="inline-label-english_keywords"]')
    await editor.get('input[maxlength]').setValue('Key words')
    expect(wrapper.get('button.primary').element.disabled).toBe(true)
    expect(wrapper.text()).toContain('关键词标签已修改')
    const labels = { english_keywords: { text: 'Key words', separator: ': ', bold: true } }
    proposeWritingProfile.mockResolvedValueOnce({ ...proposal, template: { source_execution: { ...keywordPlan, inline_label_overrides: labels } } })
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(proposeWritingProfile).toHaveBeenLastCalledWith('t1', expect.objectContaining({ inline_label_overrides: labels }))
    for (const checkbox of wrapper.get('[data-test="writing-profile-plan"]').findAll('input[type="checkbox"]')) await checkbox.setValue(true)
    expect(wrapper.get('button.primary').element.disabled).toBe(false)
    await wrapper.get('button.primary').trigger('click'); await flushPromises()
    expect(publishWritingProfile).toHaveBeenCalledWith('t1', expect.objectContaining({ inline_label_overrides: labels, proposal_id: 'proposal-1' }))
  })
  it('previews explicit cover pagination and invalidates changes', async () => {
    const wrapper = mount(WritingProfileConfirmation, { props: { template } })
    await wrapper.get('[data-test="cover-pagination-enabled"]').setValue(true)
    await wrapper.get('[data-test="cover-blank-offsets"]').setValue('1, 1')
    expect(wrapper.get('button').element.disabled).toBe(true)
    await wrapper.get('[data-test="cover-blank-offsets"]').setValue('2, 1')
    const pagination = { allowed_blank_offsets: [1, 2], require_boundary_new_page: true }
    proposeWritingProfile.mockResolvedValueOnce({ ...proposal, template: { source_execution: {
      ...plan, headings: [{ key: 'abstract', title: '摘要', properties: {}, title_properties: {} }],
      cover_pagination: { ...pagination, boundary_key: 'abstract' }
    } } })
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(proposeWritingProfile).toHaveBeenLastCalledWith('t1', expect.objectContaining({ cover_pagination: pagination }))
    expect(wrapper.get('[data-test="cover-pagination-preview"]').text()).toContain('摘要')
    await wrapper.get('[data-test="cover-boundary-new-page"]').setValue(false)
    expect(wrapper.find('[data-test="writing-profile-plan"]').exists()).toBe(false)
  })

})
