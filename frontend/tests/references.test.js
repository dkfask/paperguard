import { describe, expect, it } from 'vitest'
import { clearConfirmedReferenceIntents, createPastedReference, createReference, formatReference, referenceNumber, mergeReferenceNumbers, splitPastedReferences, suggestPastedReference } from '../src/utils/references.js'

describe('reference formatting', () => {
  it('formats and numbers structured journal entries', () => {
    expect(formatReference({
      kind: 'journal', authors: '张三', title: '论文题名', source: '计算机学报',
      year: '2024', volume: '10', issue: '2', pages: '20-30',
    }, 1)).toBe('[1] 张三. 论文题名[J]. 计算机学报, 2024, 10(2): 20-30')
  })

  it('preserves legacy text while updating its list number', () => {
    expect(formatReference(createReference('journal', '[9] 旧作者. 旧文献. 2020'), 2))
      .toBe('[2] 旧作者. 旧文献. 2020')
  })

  it('does not preview hidden fields retained from a previous type', () => {
    expect(formatReference({
      kind: 'online', authors: '张三', title: '网页标题', url: 'https://example.test/article',
      organization: '旧标准发布机构', year: '2024',
    }, 1)).toBe('[1] 张三. 网页标题[EB/OL]. 2024. https://example.test/article')
  })

  it('renders online year and standard publication facts', () => {
    expect(formatReference({ kind: 'online', authors: '张三', title: '网页', url: 'https://example.test', year: '2024' }, 1))
      .toBe('[1] 张三. 网页[EB/OL]. 2024. https://example.test')
    expect(formatReference({ kind: 'standard', title: '标准', number: 'S1', place: '北京', publisher: '出版社', year: '2024' }, 2))
      .toBe('[2] 标准[S]. S1，北京: 出版社，2024')
  })

  it('uses stored and historical numbers, without guessing new ones from position', () => {
    expect(referenceNumber({ reference_number: 4, text: '旧文献' })).toBe(4)
    expect(referenceNumber({ text: '[7] 旧文献' })).toBe(7)
    expect(referenceNumber({ text: '未编号文献' })).toBeNull()
  })

  it.each(['[8]', '[ 8 ]', '［8］', ' ［ 8 ］ '])('reads and normalizes legacy number %s', prefix => {
    const text = `${prefix} 李四. 论文乙. 2025.`
    expect(referenceNumber({ text })).toBe(8)
    const reference = { kind: 'unparsed', unformatted_text: text }
    expect(referenceNumber({ reference })).toBe(8)
    expect(formatReference(reference, referenceNumber({ reference }))).toBe('[8] 李四. 论文乙. 2025.')
  })

  it('merges server numbering by identity without replacing newer edits or reusing deleted numbers', () => {
    const target = [{ id: 'two', text: '保存期间新编辑' }, { id: 'new', text: '新增' }]
    mergeReferenceNumbers(target, [{ id: 'one', reference_number: 1 }, { id: 'two', reference_number: 2 }])
    expect(target).toEqual([{ id: 'two', text: '保存期间新编辑', reference_number: 2 }, { id: 'new', text: '新增' }])
  })

  it('clears only confirmation intents acknowledged for the same source facts', () => {
    const reference = {
      kind: 'journal', title: '文献', year: '2024', year_fact: { value: '2024', evidence: '', status: 'unconfirmed' },
      language_fact: { languages: ['en'], evidence: '', status: 'unconfirmed' },
      year_confirmation_requested: true, language_confirmation_requested: true,
    }
    const current = [{ id: 'one', reference: structuredClone(reference) }]
    const sent = [{ id: 'one', reference: structuredClone(reference) }]
    const saved = [{ id: 'one', reference: { ...structuredClone(reference),
      year_fact: { value: '2024', evidence: '', status: 'confirmed' },
      language_fact: { languages: ['en'], evidence: '', status: 'confirmed' } } }]
    clearConfirmedReferenceIntents(current, sent, saved)
    expect(current[0].reference.year_confirmation_requested).toBeUndefined()
    expect(current[0].reference.language_confirmation_requested).toBeUndefined()

    current[0].reference = { ...structuredClone(reference), year: '2025', year_fact: { value: '2025', evidence: '', status: 'unconfirmed' } }
    clearConfirmedReferenceIntents(current, sent, saved)
    expect(current[0].reference.year_confirmation_requested).toBe(true)
    expect(current[0].reference.language_confirmation_requested).toBeUndefined()
  })

  it('splits numbered entries without treating their wrapped lines as new references', () => {
    const raw = '[7] 作者甲.\r\n续行\r\n［3］ 作者乙.\r\n更多内容'
    expect(splitPastedReferences(raw)).toEqual(['[7] 作者甲.\n续行', '［3］ 作者乙.\n更多内容'])
    expect(splitPastedReferences('作者甲\n作者乙', 'lines')).toEqual(['作者甲', '作者乙'])
    expect(() => splitPastedReferences('作者甲', 'numbered')).toThrow('numbering')
    expect(() => splitPastedReferences(Array(201).fill('一条').join('\n'))).toThrow('count')
    expect(() => splitPastedReferences('甲'.repeat(20001))).toThrow('length')
  })

  it('keeps pasted source numbering separate from the assigned number', () => {
    const reference = createPastedReference('［9］ 原文')
    expect(reference.original_pasted_text).toBe('［9］ 原文')
    expect(referenceNumber({ reference })).toBeNull()
    expect(formatReference(reference, 2)).toBe('[2] 原文')
    expect(referenceNumber({ reference, reference_number: 2 })).toBe(2)
  })

  it('suggests facts only for an unambiguous structured journal entry', () => {
    expect(suggestPastedReference('[7] 张三. 研究题名[J]. 示例期刊, 2024, 12(3): 1-9.')).toEqual({
      kind: 'journal', fields: { authors: '张三', title: '研究题名', source: '示例期刊', year: '2024', volume: '12', issue: '3', pages: '1-9' },
    })
    expect(suggestPastedReference('[1] Smith J. A. A title[J]. Journal, 2024.')).toBeNull()
    expect(suggestPastedReference('[1] 无明确文献类型的原文')).toBeNull()
  })

  it('extracts a complete journal entry on paste while retaining its source', () => {
    const raw = '[7] 张三, 李四. 示例研究[J]. 示例期刊, 2024, 12(3): 109-112+118. DOI:10.1234/example.2024.01.'
    const reference = createPastedReference(raw)
    expect(reference).toMatchObject({
      kind: 'journal', authors: '张三, 李四', title: '示例研究', source: '示例期刊',
      year: '2024', volume: '12', issue: '3', pages: '109-112+118', doi: '10.1234/example.2024.01',
      original_pasted_text: raw, unformatted_text: raw,
    })
    expect(reference.year_fact).toBeUndefined()
    expect(reference.language_fact).toBeUndefined()
    expect(referenceNumber({ reference })).toBeNull()
  })

  it('extracts and displays an issue without inventing a volume', () => {
    const reference = createPastedReference('[2] 李四. 另一项研究[J]. 示例期刊, 2025,(17):1-4.')
    expect(reference).toMatchObject({
      kind: 'journal', year: '2025', volume: '', issue: '17', pages: '1-4',
    })
    expect(formatReference(reference, 2)).toBe('[2] 李四. 另一项研究[J]. 示例期刊, 2025, (17): 1-4')
  })

  it('handles commas in a journal name and a DOI without adding missing pages', () => {
    const raw = '[4] A Li. Example research[J]. Journal of Design, Science and Technology, 2025, 7(5):DOI:10.1234/example.'
    expect(createPastedReference(raw)).toMatchObject({
      kind: 'journal', source: 'Journal of Design, Science and Technology', year: '2025',
      volume: '7', issue: '5', pages: '', doi: '10.1234/example', original_pasted_text: raw,
    })
  })

  it('extracts 1101 book samples without inventing a publication place', () => {
    const raw = '[1] 张炯.Unix网络编程实用技术与实例分析[M].清华大学出版社，2002.'
    expect(createPastedReference(raw)).toMatchObject({
      kind: 'book', authors: '张炯', title: 'Unix网络编程实用技术与实例分析',
      place: '', publisher: '清华大学出版社', year: '2002', pages: '',
      original_pasted_text: raw, unformatted_text: raw,
    })
    const withPlaceAndPages = '[2] 赵克佳，赵慧.UNIX程序设计教程[M].北京:清华大学出版社，2001:12-18.'
    expect(createPastedReference(withPlaceAndPages)).toMatchObject({
      kind: 'book', authors: '赵克佳，赵慧', title: 'UNIX程序设计教程',
      place: '北京', publisher: '清华大学出版社', year: '2001', pages: '12-18',
      original_pasted_text: withPlaceAndPages, unformatted_text: withPlaceAndPages,
    })
  })

  it('extracts a thesis institution and year without inventing the place', () => {
    const raw = '[1] 王某. 信息系统研究[D]. 示例大学，2004.'
    expect(createPastedReference(raw)).toMatchObject({
      kind: 'thesis', authors: '王某', title: '信息系统研究', institution: '示例大学',
      place: '', year: '2004', original_pasted_text: raw, unformatted_text: raw,
    })
  })

  it('extracts an explicit DOI after an empty page field or an undelimited volume', () => {
    const withColon = '[1] 李某. 研究甲[J]. Example Journal, 2021, 1972(1):DOI:10.1234/example.2021.'
    const withVolume = '[2] 王某. 研究乙[J]. Example Proceedings, 2020, 11432DOI:10.1234/example.2020.'
    expect(createPastedReference(withColon)).toMatchObject({
      kind: 'journal', volume: '1972', issue: '1', pages: '', doi: '10.1234/example.2021', original_pasted_text: withColon,
    })
    expect(createPastedReference(withVolume)).toMatchObject({
      kind: 'journal', volume: '11432', pages: '', doi: '10.1234/example.2020', original_pasted_text: withVolume,
    })
  })

  it('does not automatically convert ambiguous, partial, or unsupported entries', () => {
    const values = [
      '[1] Smith J. A. A title[J]. Journal, 2024.',
      '[2] 作者. 题名[J]. 示例期刊, 2024, 12(3):1-9. 未知附注',
      '[3] 作者. 题名[J]. 示例期刊, 2024,',
      '[4] 作者. 题名[J]. 示例期刊, 2024. DOI:未知',
      '[5] 作者. 书名[M]. 出版社, 2024. 未知附注',
      '[7] 作者. 书名[M]. 北京::出版社, 2024.',
      '[8] 作者. 书名[M]. 出版社, 2023, 2024.',
      '[6] 作者. 题名[J]. 示例期刊, 2023, 2024.',
      '[9] 作者. 论文[D]. 大学, 2023, 2024.',
      '[10] 作者. 题名[J]. 示例期刊, 2024, 10. 未知附注DOI:10.1234/example.',
    ]
    for (const raw of values) {
      expect(createPastedReference(raw)).toMatchObject({ kind: 'unparsed', original_pasted_text: raw, unformatted_text: raw })
    }
  })
})
