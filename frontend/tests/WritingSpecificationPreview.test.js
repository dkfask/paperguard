import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('../src/i18n', () => ({ useI18n: () => ({ lang: { value: 'zh' } }) }))
vi.mock('../src/api', () => ({ fetchWritingSpecification: vi.fn() }))
import WritingSpecificationPreview from '../src/components/WritingSpecificationPreview.vue'

const specification = {
  source_sha256: 'a'.repeat(64), specification_sha256: 'b'.repeat(64),
  nodes: [{ node_id: 'body-1', role: 'body', part: 'word/document.xml', path: '/w:document/w:body/w:p[2]',
    text: '正文示例 <script>bad()</script>', properties: { 'font.size': { value: 12, unit: 'pt' } }, property_sources: { 'font.size': ['word/styles.xml:/w:styles/w:style[1]'] } }],
  rules: [{ id: 'body-size', scope: { role: 'body', region: 'chapter-1' }, property: 'font.size', expected: { value: 12, unit: 'pt' },
    confidence: 'conflict', evidence_node_ids: ['body-1'], supporting_count: 1, observed_count: 2 }],
  review_items: [{ code: 'variation', message: '请确认正文中的特殊字号。', node_ids: ['body-1'], rule_ids: ['body-size'] }],
  coverage: { limitations: ['实际分页尚未通过排版核验。'] },
}

describe('WritingSpecificationPreview', () => {
  it('shows candidates with their uncertainty and source rather than claiming enforcement', async () => {
    const wrapper = mount(WritingSpecificationPreview, { props: { specification } })
    expect(wrapper.text()).toContain('小四 · 12 磅')
    expect(wrapper.text()).toContain('格式有差异')
    expect(wrapper.text()).toContain('尚未启用为写作或导出检查规则')
    await wrapper.get('.spec-evidence-actions button').trigger('click')
    const source = wrapper.get('[data-test="specification-source"]')
    expect(source.text()).toContain('正文示例 <script>bad()</script>')
    expect(source.find('script').exists()).toBe(false)
    expect(source.text()).toContain('word/styles.xml')
  })

  it('does not render when a legacy template has no source specification', () => {
    const wrapper = mount(WritingSpecificationPreview)
    expect(wrapper.find('[data-test="writing-specification"]').exists()).toBe(false)
  })

  it('keeps image dimensions visibly scoped to one example', async () => {
    const value = structuredClone(specification)
    value.rules = [{ ...value.rules[0], id: 'image-width', scope: { role: 'image' }, property: 'image.width', expected: { value: 8, unit: 'cm', kind: 'display' }, confidence: 'single' }]
    const wrapper = mount(WritingSpecificationPreview, { props: { specification: value } })
    const detail = wrapper.get('.spec-group')
    detail.element.open = true
    await detail.trigger('toggle')
    expect(wrapper.text()).toContain('8 cm')
    expect(wrapper.text()).toContain('不代表所有同类对象都采用该尺寸')
  })
})
