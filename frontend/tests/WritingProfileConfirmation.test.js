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
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(prepareCandidateWritingProfile).toHaveBeenCalledWith(candidate,expect.objectContaining({expected_specification_sha256:'spec-hash'}))
    for (const checkbox of wrapper.get('[data-test="writing-profile-plan"]').findAll('input[type="checkbox"]')) await checkbox.setValue(true)
    await wrapper.get('button.primary').trigger('click'); await flushPromises()
    expect(prepareCandidateWritingProfile).toHaveBeenLastCalledWith(candidate,expect.objectContaining({accept_recommended:true}),false)
    expect(publishWritingProfile).not.toHaveBeenCalled()
  })
  it('requires another server preview after changing the manuscript hierarchy policy', async () => {
    const wrapper = mount(WritingProfileConfirmation, { props: { template } })
    await wrapper.get('button').trigger('click'); await flushPromises()
    await wrapper.get('[data-test="manuscript-structure-policy"]').setValue(true)
    expect(wrapper.find('[data-test="writing-profile-plan"]').exists()).toBe(false)
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(proposeWritingProfile).toHaveBeenLastCalledWith('t1', expect.objectContaining({preserve_manuscript_structure: true}))
  })
})
