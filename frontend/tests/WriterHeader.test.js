import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import WriterHeader from '../src/components/writer/WriterHeader.vue'

describe('WriterHeader', () => {
  it('renders project title', () => {
    const wrapper = mount(WriterHeader, {
      props: { projectTitle: 'Test Paper' }
    })
    expect(wrapper.text()).toContain('Test Paper')
  })

  it('shows saving status', () => {
    const wrapper = mount(WriterHeader, {
      props: { saving: true }
    })
    expect(wrapper.text()).toContain('保存中')
  })

  it('emits go-back event', async () => {
    const wrapper = mount(WriterHeader, {
      props: { projectTitle: 'Test' }
    })
    await wrapper.find('.icon-action').trigger('click')
    expect(wrapper.emitted('go-back')).toBeTruthy()
  })

  it('displays metrics correctly', () => {
    const wrapper = mount(WriterHeader, {
      props: {
        projectTitle: 'Test',
        totalChars: 1500,
        doneCount: 3,
        requiredCount: 5,
      }
    })
    expect(wrapper.text()).toContain('1500')
    expect(wrapper.text()).toContain('3/5')
  })
})
