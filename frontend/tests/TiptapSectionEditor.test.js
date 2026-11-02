import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import TiptapSectionEditor from '../src/features/writer/components/TiptapSectionEditor.vue'
import { blocksToSectionDocument, sectionDocumentToBlocks } from '../src/utils/sectionDocument.js'

const blocks = [
  { id: 'p1', type: 'paragraph', text: '甲乙丙', inline_runs: [
    { text: '甲', bold: true }, { text: '乙', vert_align: 'superscript' }, { text: '丙' },
  ] },
  { id: 'img1', type: 'image', image: { url: '/media/p.png', caption: '示意图' } },
  { id: 'p2', type: 'paragraph', text: '图后正文' },
]

let wrapper
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })

describe('Tiptap section editing', () => {
  it('preserves existing marks and media when a paragraph is split', async () => {
    wrapper = mount(TiptapSectionEditor, { attachTo: document.body, props: {
      document: blocksToSectionDocument(blocks), modelValue: '甲乙丙\n\n图后正文',
    } })
    await flushPromises()
    const editor = wrapper.vm.$.setupState.editor
    expect(wrapper.find('.paper-media').text()).toContain('示意图')
    editor.commands.setTextSelection(2)
    editor.commands.splitBlock()
    await flushPromises()
    const edited = wrapper.emitted('update:document').at(-1)[0]
    const result = sectionDocumentToBlocks(edited, blocks)
    expect(result.map(block => block.type)).toEqual(['paragraph', 'paragraph', 'image', 'paragraph'])
    expect(result[0].id).toBe('p1')
    expect(result[1].id).not.toBe('p1')
    expect(result[0].text).toBe('甲')
    expect(result[1].text).toBe('乙丙')
    expect(result[1].inline_runs[0].vert_align).toBe('superscript')
    expect(result[2]).toEqual(blocks[1])
  })

  it('prevents a pasted table from being silently reduced to text', async () => {
    wrapper = mount(TiptapSectionEditor, { attachTo: document.body, props: {
      document: blocksToSectionDocument(blocks), modelValue: '甲乙丙\n\n图后正文',
    } })
    await flushPromises()
    const editor = wrapper.vm.$.setupState.editor
    const event = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'clipboardData', { value: {
      files: [], getData: type => type === 'text/html' ? '<table><tr><td>内容</td></tr></table>' : '内容',
    } })
    editor.view.dom.dispatchEvent(event)
    await flushPromises()
    expect(event.defaultPrevented).toBe(true)
    expect(wrapper.get('[role="alert"]').text()).toContain('剪贴板')
    expect(sectionDocumentToBlocks(editor.getJSON(), blocks)).toEqual(blocks)
  })

  it('retains Word-style bold, underline and subscript when pasting rich text', async () => {
    wrapper = mount(TiptapSectionEditor, { attachTo: document.body, props: {
      document: blocksToSectionDocument([{ id: 'p1', type: 'paragraph', text: '原文' }]), modelValue: '原文',
    } })
    await flushPromises()
    const editor = wrapper.vm.$.setupState.editor
    editor.commands.setTextSelection(3)
    const event = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'clipboardData', { value: {
      items: [], files: [], getData: type => type === 'text/html'
        ? '<p><span style="font-weight:700">粗体</span><span style="text-decoration:underline">下划线</span>H<span style="vertical-align:sub">2</span>O</p>'
        : '粗体下划线H2O',
    } })
    editor.view.dom.dispatchEvent(event)
    await flushPromises()
    expect(event.defaultPrevented).toBe(true)
    const edited = sectionDocumentToBlocks(editor.getJSON(), [{ id: 'p1', type: 'paragraph', text: '原文' }])
    expect(edited[0].text).toBe('原文粗体下划线H2O')
    expect(edited[0].inline_runs).toMatchObject([
      { text: '原文' }, { text: '粗体', bold: true }, { text: '下划线', underline: true },
      { text: 'H' }, { text: '2', vert_align: 'subscript' }, { text: 'O' },
    ])
  })

  it('rejects unknown Word highlight instead of changing its color', async () => {
    wrapper = mount(TiptapSectionEditor, { attachTo: document.body, props: {
      document: blocksToSectionDocument(blocks), modelValue: '甲乙丙\n\n图后正文',
    } })
    await flushPromises()
    const editor = wrapper.vm.$.setupState.editor
    const event = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'clipboardData', { value: {
      items: [], files: [], getData: type => type === 'text/html'
        ? '<p><span style="background-color:rgb(12,34,56)">文字</span></p>' : '文字',
    } })
    editor.view.dom.dispatchEvent(event)
    await flushPromises()
    expect(event.defaultPrevented).toBe(true)
    expect(wrapper.get('[role="alert"]').text()).toContain('剪贴板')
    expect(sectionDocumentToBlocks(editor.getJSON(), blocks)).toEqual(blocks)
  })
})
