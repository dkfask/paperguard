import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import ScreenDeck from '../src/components/ScreenDeck.vue'
import BaseModal from '../src/components/base/BaseModal.vue'

let wrapper
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })

describe('screen tab navigation', () => {
  it('preserves the same input and unsaved value when a section is hidden and revealed', async () => {
    wrapper = mount(defineComponent({
      components: { ScreenDeck, BaseModal },
      setup() { return { draft: ref(''), open: ref(false) } },
      template: `<ScreenDeck>
        <section><h2><span aria-hidden="true">tune</span>基本信息</h2><input v-model="draft" /></section>
        <section><h2>规则</h2><button @click="open = true">详情</button></section>
        <BaseModal :open="open" title="规则详情" @close="open = false"><input /></BaseModal>
      </ScreenDeck>`,
    }), { attachTo: document.body })
    const tabs = wrapper.findAll('[role="tab"]')
    expect(tabs.map(tab => tab.text())).toEqual(['基本信息', '规则'])
    const input = wrapper.get('input')
    await input.setValue('未保存配置')
    await tabs[1].trigger('click')
    expect(input.element.closest('[role="tabpanel"]').hidden).toBe(true)
    await tabs[0].trigger('click')
    expect(wrapper.get('input').element).toBe(input.element)
    expect(input.element.value).toBe('未保存配置')
  })

  it('uses arrow keys and Home/End to move focus with selection', async () => {
    wrapper = mount(ScreenDeck, { attachTo: document.body, slots: {
      default: '<section aria-label="一">A</section><section aria-label="二">B</section><section aria-label="三">C</section>',
    } })
    const tabs = wrapper.findAll('[role="tab"]')
    tabs[0].element.focus()
    await tabs[0].trigger('keydown', { key: 'ArrowLeft' })
    await flushPromises()
    expect(tabs[2].attributes('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(tabs[2].element)
    await tabs[2].trigger('keydown', { key: 'Home' })
    expect(tabs[0].attributes('aria-selected')).toBe('true')
    await tabs[0].trigger('keydown', { key: 'End' })
    expect(tabs[2].attributes('aria-selected')).toBe('true')
  })
})
