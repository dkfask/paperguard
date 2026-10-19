import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import ConfirmDialog from '../src/components/ConfirmDialog.vue'
import ReasonDialog from '../src/components/ReasonDialog.vue'
import BaseModal from '../src/components/base/BaseModal.vue'
import CreateProjectModal from '../src/components/home/CreateProjectModal.vue'
import { translate, useI18n } from '../src/i18n'

let wrapper
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = ''; useI18n().setLang('zh') })
const key = (value, shiftKey = false) => document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: value, shiftKey, bubbles: true, cancelable: true }))

describe('dialog keyboard and draft contracts', () => {
  it('traps both directions of Tab and returns focus to its opening control', async () => {
    const opener = document.createElement('button')
    document.body.append(opener)
    opener.focus()
    wrapper = mount(ConfirmDialog, { global: { stubs: { teleport: true } }, attachTo: document.body, props: { open: false, title: '确认删除' } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    const buttons = wrapper.findAll('button')
    expect(document.activeElement).toBe(buttons.at(-1).element)
    key('Tab')
    expect(document.activeElement).toBe(buttons[0].element)
    key('Tab', true)
    expect(document.activeElement).toBe(buttons.at(-1).element)
    key('Escape')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    await wrapper.setProps({ open: false })
    expect(document.activeElement).toBe(opener)
  })

  it('preserves the reason draft during a failed save and blocks closing while busy', async () => {
    wrapper = mount(ReasonDialog, { global: { stubs: { teleport: true } }, attachTo: document.body, props: { open: false, title: '退回修改' } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    await wrapper.get('textarea').setValue('请补充实验依据')
    await wrapper.setProps({ busy: true })
    key('Escape')
    expect(wrapper.emitted('cancel')).toBeUndefined()
    await wrapper.get('textarea').trigger('keydown', { key: 'Enter', ctrlKey: true })
    await wrapper.get('textarea').trigger('keydown', { key: 'Enter', metaKey: true })
    expect(wrapper.emitted('confirm')).toBeUndefined()
    await wrapper.setProps({ busy: false })
    expect(wrapper.get('textarea').element.value).toBe('请补充实验依据')
    await wrapper.get('textarea').trigger('keydown', { key: 'Enter', ctrlKey: true })
    expect(wrapper.emitted('confirm')).toEqual([['请补充实验依据']])
    key('Escape')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('gives a teleported base modal a dialog name and keyboard close behavior', async () => {
    wrapper = mount(BaseModal, { attachTo: document.body, props: { open: false, title: '新建论文' }, slots: { default: '<label>标题<input /></label><button>保存</button>' } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    const dialog = document.querySelector('[role="dialog"]')
    expect(dialog.getAttribute('aria-label')).toBe('新建论文')
    expect(dialog.contains(document.activeElement)).toBe(true)
    key('Escape')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('announces the create-thesis title in the active language', async () => {
    wrapper = mount(CreateProjectModal, { attachTo: document.body, props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    const dialog = document.querySelector('[role="dialog"]')
    expect(dialog.getAttribute('aria-label')).toBe(translate('homeComponents.createTitle', {}, 'zh'))
    useI18n().setLang('en')
    await flushPromises()
    expect(dialog.getAttribute('aria-label')).toBe(translate('homeComponents.createTitle', {}, 'en'))
  })
  it('closes only the top dialog and restores focus through nested drawers', async () => {
    const Host = defineComponent({
      components: { BaseModal, ConfirmDialog },
      setup() { return { outer: ref(false), inner: ref(false) } },
      template: `<button @click="outer = true">打开配置</button>
        <BaseModal :open="outer" presentation="drawer" title="配置" @close="outer = false">
          <input aria-label="配置内容" />
          <button @click="inner = true">确认操作</button>
        </BaseModal>
        <ConfirmDialog :open="inner" title="确认" @cancel="inner = false" />`,
    })
    wrapper = mount(Host, { attachTo: document.body })
    const opener = wrapper.get('button')
    opener.element.focus()
    await opener.trigger('click')
    await flushPromises()
    const trigger = [...document.querySelectorAll('[role="dialog"] button')].find(el => el.textContent === '确认操作')
    trigger.focus()
    trigger.click()
    await flushPromises()
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(2)
    const masks = document.querySelectorAll('.modal-mask')
    expect(Number(masks[1].style.zIndex)).toBeGreaterThan(Number(masks[0].style.zIndex))
    key('Escape')
    await flushPromises()
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1)
    expect(document.activeElement).toBe(trigger)
    key('Escape')
    await flushPromises()
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(0)
    expect(document.activeElement).toBe(opener.element)
  })

})
