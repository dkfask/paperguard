import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ConfirmDialog from '../src/components/ConfirmDialog.vue'
import ReasonDialog from '../src/components/ReasonDialog.vue'
import BaseModal from '../src/components/base/BaseModal.vue'

let wrapper
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })
const key = (value, shiftKey = false) => document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: value, shiftKey, bubbles: true, cancelable: true }))

describe('dialog keyboard and draft contracts', () => {
  it('traps both directions of Tab and returns focus to its opening control', async () => {
    const opener = document.createElement('button')
    document.body.append(opener)
    opener.focus()
    wrapper = mount(ConfirmDialog, { attachTo: document.body, props: { open: false, title: '确认删除' } })
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
    wrapper = mount(ReasonDialog, { attachTo: document.body, props: { open: false, title: '退回修改' } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    await wrapper.get('textarea').setValue('请补充实验依据')
    await wrapper.setProps({ busy: true })
    key('Escape')
    expect(wrapper.emitted('cancel')).toBeUndefined()
    await wrapper.setProps({ busy: false })
    expect(wrapper.get('textarea').element.value).toBe('请补充实验依据')
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
})
