import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({ getSubmissionImageUrl: vi.fn() }))
vi.mock('../src/api', () => api)
import ReviewEvidence from '../src/components/ReviewEvidence.vue'

afterEach(() => { vi.clearAllMocks(); vi.unstubAllGlobals() })

describe('review check summary', () => {
  it('exposes the submitted Word action only when enabled by the parent', async () => {
    const wrapper = mount(ReviewEvidence, { props: {
      submission: { version: { content_snapshot: { sections: [] } } },
      formatDate: () => '', recordState: () => '', documentDownloadable: true,
    } })
    await wrapper.get('[data-test="download-submission-document"]').trigger('click')
    expect(wrapper.emitted('download-document')).toHaveLength(1)
    await wrapper.setProps({ documentDownloadable: false })
    expect(wrapper.find('[data-test="download-submission-document"]').exists()).toBe(false)
  })

  it('offers PDF only when both the checked Word and the optional converter are available', async () => {
    const wrapper = mount(ReviewEvidence, { props: {
      submission: { version: { content_snapshot: { sections: [] } } },
      formatDate: () => '', recordState: () => '', documentDownloadable: true, pdfDownloadable: true,
    } })
    await wrapper.get('[data-test="download-submission-pdf"]').trigger('click')
    expect(wrapper.emitted('download-pdf')).toHaveLength(1)
    await wrapper.setProps({ documentDownloadable: false })
    expect(wrapper.find('[data-test="download-submission-pdf"]').exists()).toBe(false)
    await wrapper.setProps({ documentDownloadable: true, pdfDownloadable: false })
    expect(wrapper.find('[data-test="download-submission-pdf"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="download-submission-document"]').exists()).toBe(true)
  })

  it('shows findings and unchecked requirements without treating them as a check count', () => {
    const wrapper = mount(ReviewEvidence, {
      props: {
        submission: {
          version: { content_snapshot: { title: '论文', sections: [] }, attachment_refs: [] },
          candidate: { check_report: { summary: { total: 0, hard_failures: 0, warning: 0, not_checked: 2 } } },
        },
        formatDate: () => '',
        recordState: () => '',
      },
    })
    expect(wrapper.get('.check-summary').text()).toContain('检查发现 0 项')
    expect(wrapper.get('.check-summary').text()).toContain('未检查 2 项')
    expect(wrapper.get('.check-summary').text()).not.toContain('自动检查 0 项')
  })

  it('shows the submitted version’s content advisories without carrying them into another submission', async () => {
    const version = { content_snapshot: { title: '论文', sections: [] }, attachment_refs: [] }
    const wrapper = mount(ReviewEvidence, { props: {
      submission: {
        version,
        candidate: { check_report: { quality_advisories: [
          { rule_id: 'references-recent', message: '近三年文献 8 篇，英文 2 篇。' },
        ] } },
      },
      formatDate: () => '', recordState: () => '',
    } })

    const advisory = wrapper.get('[data-test="review-quality-advisories"]')
    expect(advisory.text()).toContain('文献内容提示')
    expect(advisory.text()).toContain('近三年文献 8 篇，英文 2 篇。')
    expect(advisory.find('input').exists()).toBe(false)
    await wrapper.setProps({ submission: { version, candidate: { check_report: { quality_advisories: [] } } } })
    expect(wrapper.find('[data-test="review-quality-advisories"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows cover fields from each frozen submission rather than the current draft', async () => {
    const snapshot = {
      title: '论文', meta: { author: '张同学', student_number: '2027001', extra: '补充信息' },
      template_snapshot: { meta_fields: [
        { key: 'author', label: '作者' }, { key: 'student_number', label: '学号' },
        { key: 'supervisor', label: '指导教师' },
      ] },
      sections: [{ key: 'cover', type: 'cover', title: '封面', paragraphs: [] }],
    }
    const wrapper = mount(ReviewEvidence, { props: {
      submission: { submission: { id: 'first' }, version: { content_snapshot: snapshot } },
      formatDate: () => '', recordState: () => '',
    } })
    const cover = wrapper.get('[data-test="review-cover"]')
    expect(cover.text()).toContain('作者')
    expect(cover.text()).toContain('张同学')
    expect(cover.text()).toContain('学号')
    expect(cover.text()).toContain('2027001')
    expect(cover.text()).toContain('补充信息')
    expect(cover.text()).toContain('指导教师')

    await wrapper.setProps({ submission: { submission: { id: 'second' }, version: {
      content_snapshot: { ...snapshot, meta: { author: '李同学' } },
    } } })
    expect(wrapper.get('[data-test="review-cover"]').text()).toContain('李同学')
    expect(wrapper.get('[data-test="review-cover"]').text()).not.toContain('张同学')
    wrapper.unmount()
  })

  it('displays structured reference content and selects its block for a comment', async () => {
    const wrapper = mount(ReviewEvidence, { props: {
      submission: { version: { content_snapshot: { sections: [{ key: 'references', type: 'references', title: '参考文献', paragraphs: [
        { id: 'ref-1', type: 'paragraph', text: '', reference_number: 2,
          reference: { kind: 'journal', authors: '王某', title: '实验研究', source: '学报', year: '2026' } },
      ] }] } } },
      formatDate: () => '', recordState: () => '', selectable: true,
    } })
    expect(wrapper.get('.snapshot-block').text()).toContain('[2] 王某. 实验研究[J]. 学报, 2026')
    expect(wrapper.get('.snapshot-block').text()).not.toContain('（空段落）')
    await wrapper.get('[data-test="select-review-block"]').trigger('click')
    expect(wrapper.emitted('select-block')[0][0]).toEqual({
      blockId: 'ref-1', sectionTitle: '参考文献', text: '[2] 王某. 实验研究[J]. 学报, 2026',
    })
    wrapper.unmount()
  })

  it('lets the teacher select a block from the frozen submission', async () => {
    const wrapper = mount(ReviewEvidence, { props: {
      submission: { version: { content_snapshot: { sections: [{ key: 'chapter', title: '研究方法', paragraphs: [
        { id: 'p1', type: 'paragraph', text: '实验内容' },
      ] }] } } },
      formatDate: () => '', recordState: () => '', selectable: true,
    } })
    await wrapper.get('[data-test="select-review-block"]').trigger('click')
    expect(wrapper.emitted('select-block')[0][0]).toEqual({ blockId: 'p1', sectionTitle: '研究方法', text: '实验内容' })
    await wrapper.setProps({ selectedBlockId: 'p1' })
    expect(wrapper.get('.snapshot-block').classes()).toContain('selected')
    await wrapper.setProps({ selectable: false })
    expect(wrapper.find('[data-test="select-review-block"]').exists()).toBe(false)
  })

  it('preserves inline formatting and anchors a selection across formatted text', async () => {
    const wrapper = mount(ReviewEvidence, { attachTo: document.body, props: {
      submission: { submission: { id: 'submission-1' }, version: { id: 'version-1', content_snapshot: { sections: [
        { key: 'chapter', title: '实验', paragraphs: [{ id: 'p1', type: 'paragraph', text: 'H2O😀是10', inline_runs: [
          { text: 'H', highlight: 'green' }, { text: '2', vert_align: 'subscript' }, { text: 'O😀是' },
          { text: '1', bold: true, highlight: 'yellow' },
          { text: '0', vert_align: 'superscript', italic: true, underline: true },
        ] }] },
      ] } } },
      formatDate: () => '', recordState: () => '', selectable: true,
    } })
    const paragraph = wrapper.get('.snapshot-block p')
    expect(paragraph.text()).toBe('H2O😀是10')
    expect(paragraph.get('sub').text()).toBe('2')
    expect(paragraph.get('sup').text()).toBe('0')
    expect(paragraph.get('sup').classes()).toEqual(expect.arrayContaining(['review-italic', 'review-underline']))
    expect(paragraph.findAll('span')[0].attributes('style')).toContain('background-color: lime')
    expect(paragraph.get('.review-bold').attributes('style')).toContain('background-color: yellow')

    const selection = window.getSelection()
    const range = document.createRange()
    range.setStart(paragraph.get('sub').element.firstChild, 0)
    range.setEnd(paragraph.get('.review-bold').element.firstChild, 1)
    selection.removeAllRanges()
    selection.addRange(range)
    await paragraph.trigger('mouseup')
    await wrapper.get('[data-test="select-review-text"]').trigger('click')
    expect(wrapper.emitted('select-block')[0][0]).toMatchObject({
      blockId: 'p1', quotedText: '2O😀是1', textStart: 1, textEnd: 6,
    })
    selection.removeAllRanges()
    wrapper.unmount()
  })

  it('renders submitted tables as cells without interpreting cell content as markup', () => {
    const wrapper = mount(ReviewEvidence, { props: {
      submission: { version: { content_snapshot: { sections: [{ key: 'chapter', paragraphs: [
        { id: 'table-1', type: 'table', table: { caption: '实验结果', cells: [
          ['项目', '数值'], ['A | B', '<script>'],
        ] } },
      ] }] } } },
      formatDate: () => '', recordState: () => '',
    } })
    const rows = wrapper.findAll('.snapshot-table tr')
    expect(rows).toHaveLength(2)
    expect(rows[1].findAll('td').map(cell => cell.text())).toEqual(['A | B', '<script>'])
    expect(wrapper.find('script').exists()).toBe(false)
    wrapper.unmount()
  })

  it('anchors a selection to the correct occurrence using Unicode code point offsets', async () => {
    const wrapper = mount(ReviewEvidence, { attachTo: document.body, props: {
      submission: { submission: { id: 'submission-1' }, version: { id: 'version-1', content_snapshot: { sections: [
        { key: 'chapter', title: '研究方法', paragraphs: [{ id: 'p1', type: 'paragraph', text: '结果😀结果\n实验' }] },
      ] } } },
      formatDate: () => '', recordState: () => '', selectable: true,
    } })
    const paragraph = wrapper.get('.snapshot-block p')
    const selection = window.getSelection()
    const range = document.createRange()
    range.setStart(paragraph.element.firstChild, 4)
    range.setEnd(paragraph.element.firstChild, 6)
    selection.removeAllRanges()
    selection.addRange(range)
    await paragraph.trigger('mouseup')
    await wrapper.get('[data-test="select-review-text"]').trigger('click')
    expect(wrapper.emitted('select-block')[0][0]).toEqual({
      blockId: 'p1', sectionTitle: '研究方法', text: '结果', quotedText: '结果', textStart: 3, textEnd: 5,
    })

    range.setStart(paragraph.element.firstChild, 2)
    range.setEnd(paragraph.element.firstChild, 4)
    selection.removeAllRanges()
    selection.addRange(range)
    await paragraph.trigger('mouseup')
    await wrapper.get('[data-test="select-review-text"]').trigger('click')
    expect(wrapper.emitted('select-block')[1][0]).toMatchObject({ quotedText: '😀', textStart: 2, textEnd: 3 })
    selection.removeAllRanges()
    wrapper.unmount()
  })

  it('does not accept selections across blocks or retain a selection across versions', async () => {
    const version = { id: 'version-1', content_snapshot: { sections: [{ key: 's', paragraphs: [
      { id: 'p1', type: 'paragraph', text: '第一段' }, { id: 'p2', type: 'paragraph', text: '第二段' },
    ] }] } }
    const wrapper = mount(ReviewEvidence, { attachTo: document.body, props: {
      submission: { submission: { id: 'submission-1' }, version }, formatDate: () => '', recordState: () => '', selectable: true,
    } })
    const paragraphs = wrapper.findAll('.snapshot-block p')
    const selection = window.getSelection()
    const range = document.createRange()
    range.setStart(paragraphs[0].element.firstChild, 0)
    range.setEnd(paragraphs[1].element.firstChild, 2)
    selection.removeAllRanges()
    selection.addRange(range)
    await paragraphs[0].trigger('mouseup')
    expect(wrapper.find('[data-test="select-review-text"]').exists()).toBe(false)
    range.setEnd(paragraphs[0].element.firstChild, 2)
    selection.removeAllRanges()
    selection.addRange(range)
    await paragraphs[0].trigger('mouseup')
    expect(wrapper.find('[data-test="select-review-text"]').exists()).toBe(true)
    await wrapper.setProps({ submission: { submission: { id: 'submission-2' }, version: { ...version, id: 'version-2' } } })
    expect(wrapper.find('[data-test="select-review-text"]').exists()).toBe(false)
    selection.removeAllRanges()
    wrapper.unmount()
  })

  it('loads images from the selected submission and never reuses another version', async () => {
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { revokeObjectURL })
    api.getSubmissionImageUrl.mockResolvedValueOnce('blob:first').mockResolvedValueOnce('blob:second')
    const snapshot = { sections: [{ key: 'chapter', paragraphs: [
      { id: 'figure-1', type: 'image', image: { url: '/media/uploads/current.png', caption: '结果图' } },
    ] }] }
    const wrapper = mount(ReviewEvidence, { props: {
      submission: { submission: { id: 'first' }, version: { content_snapshot: snapshot } },
      formatDate: () => '', recordState: () => '',
    } })
    await flushPromises()
    expect(api.getSubmissionImageUrl).toHaveBeenCalledWith('first', 'figure-1')
    expect(wrapper.get('img').attributes('src')).toBe('blob:first')
    await wrapper.setProps({ submission: { submission: { id: 'second' }, version: { content_snapshot: snapshot } } })
    await flushPromises()
    expect(api.getSubmissionImageUrl).toHaveBeenCalledWith('second', 'figure-1')
    expect(wrapper.get('img').attributes('src')).toBe('blob:second')
    wrapper.unmount()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:first')
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:second')
  })

  it('identifies submitted SmartArt without requesting a raster image', async () => {
    api.getSubmissionImageUrl.mockClear()
    const wrapper = mount(ReviewEvidence, { props: {
      submission: { submission: { id: 'first' }, version: { content_snapshot: { sections: [
        { key: 'chapter', paragraphs: [{ id: 'diagram', type: 'image', image: { caption: '结构图', smartart: { dm: 'data' } } }] },
      ] } } },
      formatDate: () => '', recordState: () => '',
    } })
    await flushPromises()
    expect(wrapper.get('.snapshot-image').text()).toContain('Word SmartArt')
    expect(api.getSubmissionImageUrl).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('shows a recovery message when the submitted image cannot be verified', async () => {
    api.getSubmissionImageUrl.mockRejectedValueOnce(new Error('图片已变化'))
    const wrapper = mount(ReviewEvidence, { props: {
      submission: { submission: { id: 'first' }, version: { content_snapshot: { sections: [
        { key: 'chapter', paragraphs: [{ id: 'figure-1', type: 'image', image: { caption: '结果图' } }] },
      ] } } },
      formatDate: () => '', recordState: () => '',
    } })
    await flushPromises()
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.get('[role="status"]').text()).toContain('请联系系统管理员恢复')
    wrapper.unmount()
  })
})
