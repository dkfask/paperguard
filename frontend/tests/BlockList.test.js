import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  getPrivateImageUrl: vi.fn(),
  uploadProjectImage: vi.fn(),
}))

vi.mock('../src/api.js', () => api)
vi.mock('../src/i18n', () => ({
  useI18n: () => ({
    t: (key, params = {}) => {
      const values = {
        'blockList.figure': `图 ${params.chapter}.${params.number}`,
        'blockList.tableNumber': `表 ${params.chapter}.${params.number}`,
        'blockList.block': `第 ${params.number} 块`,
        'blockList.image': '图片',
        'blockList.equation': '公式',
        'blockList.table': '表格',
        'blockList.paragraphPlaceholder': `第 ${params.number} 段正文`,
        'blockList.addParagraph': '段落',
        'blockList.addImage': '图片',
        'blockList.addTable': '表格',
      }
      return values[key] || key
    },
  }),
}))

import BlockList from '../src/components/BlockList.vue'

function dispatchPaste(wrapper, data, target) {
  if (!target) {
    target = wrapper.find('textarea').element
    target.setSelectionRange(target.value.length, target.value.length)
  }
  const event = new Event('paste', { bubbles: true, cancelable: true })
  Object.defineProperty(event, 'clipboardData', { value: data })
  target.dispatchEvent(event)
  return event
}

describe('BlockList image paste handling', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getPrivateImageUrl.mockResolvedValue('blob:image')
    api.uploadProjectImage.mockResolvedValue({ url: '/media/uploads/pasted.png' })
  })

  it('retains unaffected inline formatting when a paragraph is edited', async () => {
    const paragraphs = [{ id: 'p1', type: 'paragraph', text: '粗体H2O', inline_runs: [
      { text: '粗体', bold: true }, { text: 'H', italic: true },
      { text: '2', vert_align: 'subscript' }, { text: 'O' },
    ] }]
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })
    await wrapper.get('textarea').setValue('粗体文字H2O')
    expect(paragraphs[0].text).toBe('粗体文字H2O')
    expect(paragraphs[0].inline_runs).toMatchObject([
      { text: '粗体文字', bold: true }, { text: 'H', italic: true },
      { text: '2', vert_align: 'subscript' }, { text: 'O' },
    ])
    wrapper.unmount()
  })

  it('does not save edits when stored inline runs are inconsistent', async () => {
    const paragraphs = [{ id: 'p1', type: 'paragraph', text: '正文', inline_runs: [{ text: '不同内容', bold: true }] }]
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })
    await wrapper.get('textarea').setValue('修改')
    expect(paragraphs[0].text).toBe('正文')
    expect(wrapper.get('textarea').element.value).toBe('正文')
    expect(wrapper.emitted('change')).toBeUndefined()
    expect(wrapper.get('[role="alert"]').text()).toContain('blockList.inlineFormatInvalid')
    wrapper.unmount()
  })

  it('uses different identifiers for consecutive new paragraphs', async () => {
    const paragraphs = []
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })
    const add = wrapper.findAll('.insert-row button')[0]
    await add.trigger('click'); await add.trigger('click')
    expect(paragraphs[0].id).not.toBe(paragraphs[1].id)
    wrapper.unmount()
  })

  it('does not turn a Word text-only clipboard image representation into a figure', async () => {
    const paragraphs = [{ id: 'p1', type: 'paragraph', text: '原文' }]
    const imageItem = { kind: 'file', type: 'image/png', getAsFile: () => new File(['png'], 'word.png', { type: 'image/png' }) }
    const clipboardData = {
      items: [imageItem],
      getData: (type) => type === 'text/plain' ? '从 Word 复制的整段文字' : '<p>从 Word 复制的整段文字</p>',
    }
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })

    const event = dispatchPaste(wrapper, clipboardData)
    await flushPromises()

    expect(event.defaultPrevented).toBe(true)
    expect(paragraphs).toHaveLength(1)
    expect(paragraphs[0].type).toBe('paragraph')
    expect(paragraphs[0].text).toBe('原文从 Word 复制的整段文字')
    expect(api.uploadProjectImage).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('pastes an HTML image while preserving accompanying HTML text', async () => {
    const paragraphs = [{ id: 'p1', type: 'paragraph', text: '原文' }]
    const imageItem = { kind: 'file', type: 'image/png', getAsFile: () => new File(['png'], 'word.png', { type: 'image/png' }) }
    const clipboardData = {
      items: [imageItem],
      getData: (type) => type === 'text/html' ? '<p>Word 段落文字<img src="figure.png"></p>' : '',
    }
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })

    const event = dispatchPaste(wrapper, clipboardData)
    await flushPromises()

    expect(event.defaultPrevented).toBe(true)
    expect(paragraphs).toHaveLength(2)
    expect(paragraphs[0].text).toBe('原文Word 段落文字')
    expect(paragraphs[1].type).toBe('image')
    expect(api.uploadProjectImage).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('preserves mixed Word text, semantic formatting, images and tables at the caret', async () => {
    const paragraphs = [{ id: 'p1', type: 'paragraph', text: '起终', inline_runs: [
      { text: '起', italic: true }, { text: '终', underline: true },
    ] }]
    const file = new File(['png'], 'figure.png', { type: 'image/png' })
    const clipboardData = {
      items: [{ kind: 'file', type: file.type, getAsFile: () => file }],
      getData: type => type === 'text/html'
        ? '<p>前<strong>重点</strong>H<span style="vertical-align:sub">2</span>O</p><p><img src="file:///figure.png"></p><p>图后</p><table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table><p>末尾</p>'
        : '前重点H2O\n图后\nA\tB\nC\tD\n末尾',
    }
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })
    const textarea = wrapper.get('textarea').element
    textarea.setSelectionRange(1, 1)

    const event = dispatchPaste(wrapper, clipboardData, textarea)
    await flushPromises()

    expect(event.defaultPrevented).toBe(true)
    expect(paragraphs.map(block => block.type)).toEqual(['paragraph', 'image', 'paragraph', 'table', 'paragraph'])
    expect(paragraphs[0].text).toBe('起前重点H2O')
    expect(paragraphs[0].inline_runs).toMatchObject([
      { text: '起', italic: true }, { text: '前' }, { text: '重点', bold: true },
      { text: 'H' }, { text: '2', vert_align: 'subscript' }, { text: 'O' },
    ])
    expect(paragraphs[1].image.url).toBe('/media/uploads/pasted.png')
    expect(paragraphs[2].text).toBe('图后')
    expect(paragraphs[3].table.cells).toEqual([['A', 'B'], ['C', 'D']])
    expect(paragraphs[4].text).toBe('末尾终')
    expect(paragraphs[4].inline_runs.at(-1)).toMatchObject({ text: '终', underline: true })
    wrapper.unmount()
  })

  it.each([
    ['merged cells', '<table><tr><td colspan="2">合并</td></tr></table>'],
    ['nested table', '<table><tr><td><table><tr><td>内层</td></tr></table></td></tr></table>'],
    ['styled table text', '<table><tr><td><b>粗体</b></td></tr></table>'],
    ['unavailable picture', '<p>文字<img src="file:///unavailable.png"></p>'],
    ['unsupported highlight', '<p><span style="background-color: rgb(12, 34, 56)">文字</span></p>'],
    ['unresolved stylesheet semantics', '<style>.important { font-weight: 700 }</style><p class="important">重要文字</p>'],
  ])('rejects %s without changing the draft', async (_name, html) => {
    const paragraphs = [{ id: 'p1', type: 'paragraph', text: '原文' }]
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })
    const event = dispatchPaste(wrapper, { items: [], files: [], getData: type => type === 'text/html' ? html : '源文字' })
    await flushPromises()
    expect(event.defaultPrevented).toBe(true)
    expect(paragraphs).toEqual([{ id: 'p1', type: 'paragraph', text: '原文' }])
    expect(wrapper.get('[role="alert"]').text()).toContain('blockList.pasteUnsupported')
    expect(api.uploadProjectImage).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does not partially insert a mixed paste when an image upload fails', async () => {
    api.uploadProjectImage.mockRejectedValueOnce(new Error('upload failed'))
    const paragraphs = [{ id: 'p1', type: 'paragraph', text: '原文' }]
    const file = new File(['png'], 'figure.png', { type: 'image/png' })
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })
    dispatchPaste(wrapper, { items: [{ kind: 'file', type: file.type, getAsFile: () => file }],
      getData: type => type === 'text/html' ? '<p>正文<img src="file:///figure.png"></p>' : '正文' })
    await flushPromises()
    expect(paragraphs).toEqual([{ id: 'p1', type: 'paragraph', text: '原文' }])
    expect(wrapper.get('[role="alert"]').text()).toContain('blockList.imageUploadFailed')
    wrapper.unmount()
  })

  it('keeps edits made while the clipboard image is uploading', async () => {
    let resolveUpload
    api.uploadProjectImage.mockReturnValueOnce(new Promise(resolve => { resolveUpload = resolve }))
    const paragraphs = [{ id: 'p1', type: 'paragraph', text: '原文' }]
    const file = new File(['png'], 'figure.png', { type: 'image/png' })
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })
    dispatchPaste(wrapper, { items: [{ kind: 'file', type: file.type, getAsFile: () => file }], getData: () => '' })
    await wrapper.get('textarea').setValue('编辑后')
    resolveUpload({ url: '/media/uploads/pasted.png' })
    await flushPromises()
    expect(paragraphs).toEqual([{ id: 'p1', type: 'paragraph', text: '编辑后' }])
    expect(wrapper.get('[role="alert"]').text()).toContain('blockList.pasteChanged')
    wrapper.unmount()
  })

  it('still inserts a block for a clipboard containing only an image', async () => {
    const paragraphs = [{ id: 'p1', type: 'paragraph', text: '原文' }]
    const imageItem = { kind: 'file', type: 'image/png', getAsFile: () => new File(['png'], 'figure.png', { type: 'image/png' }) }
    const clipboardData = { items: [imageItem], getData: () => '' }
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })

    const event = dispatchPaste(wrapper, clipboardData)
    await flushPromises()

    expect(event.defaultPrevented).toBe(true)
    expect(paragraphs).toHaveLength(2)
    expect(paragraphs[1].type).toBe('image')
    expect(paragraphs[1].image.url).toBe('/media/uploads/pasted.png')
    expect(api.uploadProjectImage).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('falls back to clipboard files when the browser omits image items', async () => {
    const paragraphs = [{ id: 'p1', type: 'paragraph', text: '原文' }]
    const file = new File(['png'], 'figure.png', { type: 'image/png' })
    const clipboardData = { items: [], files: [file], getData: () => '' }
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })

    const event = dispatchPaste(wrapper, clipboardData)
    await flushPromises()

    expect(event.defaultPrevented).toBe(true)
    expect(paragraphs).toHaveLength(2)
    expect(paragraphs[1].type).toBe('image')
    expect(api.uploadProjectImage).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('uploads a data URL image pasted into an existing image block', async () => {
    const paragraphs = [{ id: 'img1', type: 'image', image: { url: '', caption: '' } }]
    const clipboardData = {
      items: [],
      files: [],
      getData: (type) => type === 'text/html'
        ? '<p><img src="data:image/png;base64,cG5n"></p>'
        : '',
    }
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })

    const event = dispatchPaste(wrapper, clipboardData, wrapper.find('.image-block').element)
    await flushPromises()

    expect(event.defaultPrevented).toBe(true)
    expect(paragraphs[0].image.url).toBe('/media/uploads/pasted.png')
    expect(api.uploadProjectImage).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('replaces an existing image by pasting a clipboard file onto its block', async () => {
    const paragraphs = [{ id: 'img1', type: 'image', image: { url: '/media/old.png', caption: '' } }]
    const imageItem = { kind: 'file', type: 'image/png', getAsFile: () => new File(['png'], 'new.png', { type: 'image/png' }) }
    const clipboardData = { items: [imageItem], files: [], getData: () => '' }
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })

    const event = dispatchPaste(wrapper, clipboardData, wrapper.find('.image-block').element)
    await flushPromises()

    expect(event.defaultPrevented).toBe(true)
    expect(paragraphs[0].image.url).toBe('/media/uploads/pasted.png')
    expect(api.uploadProjectImage).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('keeps the original image when any image in a replacement paste fails', async () => {
    api.uploadProjectImage.mockResolvedValueOnce({ url: '/media/new.png' }).mockRejectedValueOnce(new Error('failed'))
    const paragraphs = [{ id: 'img1', type: 'image', image: { url: '/media/old.png', caption: '旧图' } }]
    const files = ['first.png', 'second.png'].map(name => new File(['png'], name, { type: 'image/png' }))
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })
    dispatchPaste(wrapper, { items: files.map(file => ({ kind: 'file', type: file.type, getAsFile: () => file })),
      getData: () => '' }, wrapper.find('.image-block').element)
    await flushPromises()
    expect(paragraphs).toEqual([{ id: 'img1', type: 'image', image: { url: '/media/old.png', caption: '旧图' } }])
    expect(wrapper.get('[role="alert"]').text()).toContain('blockList.imageUploadFailed')
    wrapper.unmount()
  })

  it('creates a multi-row table when a tab-separated table is pasted into a paragraph', async () => {
    const paragraphs = [{ id: 'p1', type: 'paragraph', text: '原文' }]
    const clipboardData = {
      items: [],
      files: [],
      getData: (type) => type === 'text/plain' ? '姓名\t成绩\n张三\t95\n李四\t88' : '',
    }
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })

    const event = dispatchPaste(wrapper, clipboardData)
    await flushPromises()

    expect(event.defaultPrevented).toBe(true)
    expect(paragraphs[1].type).toBe('table')
    expect(paragraphs[1].table.rows).toBe(3)
    expect(paragraphs[1].table.cols).toBe(2)
    expect(paragraphs[1].table.cells).toEqual([['姓名', '成绩'], ['张三', '95'], ['李四', '88']])
    wrapper.unmount()
  })

  it('expands an existing table and fills pasted rows and columns from the active cell', async () => {
    const paragraphs = [{
      id: 'tbl1',
      type: 'table',
      table: { rows: 1, cols: 1, cells: [['原值']], caption: '' },
    }]
    const clipboardData = {
      items: [],
      files: [],
      getData: (type) => type === 'text/html'
        ? '<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>'
        : '',
    }
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })

    const event = dispatchPaste(wrapper, clipboardData, wrapper.find('table input').element)
    await flushPromises()

    expect(event.defaultPrevented).toBe(true)
    expect(paragraphs[0].table.rows).toBe(2)
    expect(paragraphs[0].table.cols).toBe(2)
    expect(paragraphs[0].table.cells).toEqual([['A', 'B'], ['C', 'D']])
    wrapper.unmount()
  })

  it('does not silently discard text around a table pasted into a cell', async () => {
    const paragraphs = [{ id: 'tbl1', type: 'table', table: { rows: 1, cols: 1, cells: [['原值']], caption: '' } }]
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1' } })
    const event = dispatchPaste(wrapper, { getData: type => type === 'text/html'
      ? '<p>表前</p><table><tr><td>A</td><td>B</td></tr></table><p>表后</p>' : '表前\nA\tB\n表后' },
    wrapper.find('table input').element)
    await flushPromises()
    expect(event.defaultPrevented).toBe(true)
    expect(paragraphs[0].table.cells).toEqual([['原值']])
    expect(wrapper.get('[role="alert"]').text()).toContain('blockList.pasteUnsupported')
    wrapper.unmount()
  })

  it('numbers multiple images from one paste in their insertion order', async () => {
    const paragraphs = [{ id: 'p1', type: 'paragraph', text: '原文' }]
    const files = ['first.png', 'second.png'].map((name) => new File(['png'], name, { type: 'image/png' }))
    const items = files.map((file) => ({ kind: 'file', type: file.type, getAsFile: () => file }))
    const clipboardData = { items, getData: () => '' }
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1', chapter: 3 } })

    dispatchPaste(wrapper, clipboardData)
    await flushPromises()

    expect(paragraphs.slice(1).map((block) => block.type)).toEqual(['image', 'image'])
    expect(wrapper.findAll('.caption-num').map((node) => node.text())).toEqual(['图 3.1', '图 3.2'])
    expect(api.uploadProjectImage).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('continues figure numbers from the preceding section offset', () => {
    const paragraphs = [
      { id: 'img1', type: 'image', image: { url: '/media/one.png', caption: '' } },
      { id: 'img2', type: 'image', image: { url: '/media/two.png', caption: '' } },
    ]
    const wrapper = mount(BlockList, {
      props: { paragraphs, projectId: 'p1', chapter: 3, figureOffset: 1 },
    })

    expect(wrapper.findAll('.caption-num').map((node) => node.text())).toEqual(['图 3.2', '图 3.3'])
    wrapper.unmount()
  })

  it('renumbers figures after moving, removing and adding blocks without changing their captions', async () => {
    const paragraphs = ['甲', '乙', '丙'].map((caption, index) => ({
      id: `img${index}`, type: 'image', image: { url: `/media/${index}.png`, caption },
    }))
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1', chapter: 3 } })
    const captions = () => wrapper.findAll('.image-block').map((block) => [
      block.get('.caption-num').text(), block.get('.caption-input').element.value,
    ])

    await wrapper.findAll('.para-toolbar')[2].findAll('button')[0].trigger('click')
    expect(captions()).toEqual([['图 3.1', '甲'], ['图 3.2', '丙'], ['图 3.3', '乙']])
    await wrapper.findAll('.para-toolbar')[1].get('button.danger').trigger('click')
    expect(captions()).toEqual([['图 3.1', '甲'], ['图 3.2', '乙']])
    await wrapper.findAll('.insert-row button')[1].trigger('click')
    expect(captions()).toEqual([['图 3.1', '甲'], ['图 3.2', '乙'], ['图 3.3', '']])
    wrapper.unmount()
  })

  it('continues table numbers from preceding sections of the same chapter', () => {
    const paragraphs = [{ id: 'tbl1', type: 'table', table: { rows: 1, cols: 1, cells: [['值']], caption: '' } }]
    const wrapper = mount(BlockList, { props: { paragraphs, projectId: 'p1', chapter: 3, tableOffset: 2 } })
    expect(wrapper.find('.caption-num').text()).toBe('表 3.3')
    wrapper.unmount()
  })

  it('shows inline SmartArt typography guidance on raster image blocks without nagging preserved SmartArt', () => {
    const paragraphs = [
      { id: 'img-raster', type: 'image', image: { url: '/media/shot.png', caption: '运行截图' } },
      { id: 'img-smartart', type: 'image', image: { caption: '架构图', smartart: { dm: '<d/>' } } },
    ]
    const wrapper = mount(BlockList, {
      props: {
        paragraphs,
        projectId: 'p1',
        chapter: 4,
        figureCaptionSizePt: 10.5,
        manuscriptImportEnabled: false,
        imageTypographyRestricted: true,
      },
    })
    const imageBlocks = wrapper.findAll('.image-block')
    expect(imageBlocks).toHaveLength(2)
    const rasterGuide = imageBlocks[0].find('[data-test="figure-typography-guide"]')
    expect(rasterGuide.exists()).toBe(true)
    expect(rasterGuide.attributes('data-guide-variant')).toBe('inline')
    expect(rasterGuide.classes()).toContain('is-strict')
    expect(imageBlocks[1].find('[data-test="figure-typography-guide"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
