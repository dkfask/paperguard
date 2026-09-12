import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ProjectList from '../src/components/home/ProjectList.vue'

describe('ProjectList', () => {
  const mockProjects = [
    {
      id: 1,
      title: 'Test Paper',
      template_name: 'Default',
      done_count: 3,
      required_count: 5,
      total_chars: 1500,
      updated_at: '2026-01-01',
    }
  ]

  it('renders project list', () => {
    const wrapper = mount(ProjectList, {
      props: { projects: mockProjects }
    })
    expect(wrapper.text()).toContain('Test Paper')
  })

  it('calculates progress correctly', () => {
    const wrapper = mount(ProjectList, {
      props: { projects: mockProjects }
    })
    expect(wrapper.text()).toContain('3/5')
  })

  it('emits open event on card click', async () => {
    const wrapper = mount(ProjectList, {
      props: { projects: mockProjects }
    })
    await wrapper.find('.project-card').trigger('click')
    expect(wrapper.emitted('open')).toBeTruthy()
    expect(wrapper.emitted('open')[0]).toEqual([1])
  })
})
