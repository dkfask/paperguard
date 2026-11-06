import { describe, expect, it } from 'vitest'
import { clearConfirmedReferenceIntents, createPastedReference, createReference, formatReference, REFERENCE_FIELDS, referenceNumber, mergeReferenceNumbers, splitPastedReferences, suggestPastedReference } from '../src/utils/references.js'

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

  it('matches exported text for access dates and trailing punctuation', () => {
    expect(formatReference({
      kind: 'online', authors: '张三', title: '测试资料', year: '2024',
      url: 'https://example.test/a', access_date: '2026-11-01',
    }, 1)).toBe('[1] 张三. 测试资料[EB/OL]. 2024. https://example.test/a（引用日期：2026-11-01）')
    expect(formatReference({
      kind: 'journal', authors: '张三', title: '测试资料', source: '期刊',
      year: '2024', pages: '4-9。',
    }, 1)).toBe('[1] 张三. 测试资料[J]. 期刊, 2024: 4-9')
  })

  it('uses stored and historical numbers, without guessing new ones from position', () => {
    expect(referenceNumber({ reference_number: 4, text: '旧文献' })).toBe(4)
    expect(referenceNumber({ text: '[7] 旧文献' })).toBe(7)
    expect(referenceNumber({ text: '未编号文献' })).toBeNull()
  })

  it.each(['[8]', '[ 8 ]', '［8］', ' ［ 8 ］ ', '[８]', '［８］'])('reads and normalizes legacy number %s', prefix => {
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

  it('preserves wrapped references and their source when pasted with fullwidth digits', () => {
    const first = '［１］ 张三. 第一条[J]. 示例期刊, 2024.\n续行内容'
    const second = '［２］ 李四. 第二条[J]. 示例期刊, 2025.'
    const raw = `${first}\n${second}`
    expect(splitPastedReferences(raw)).toEqual([first, second])
    expect(splitPastedReferences(raw, 'numbered')).toEqual([first, second])
    const reference = createPastedReference(first)
    expect(reference.original_pasted_text).toBe(first)
    expect(referenceNumber({ reference })).toBeNull()
    expect(formatReference(reference, 1)).toMatch(/^\[1\] 张三/)
    expect(formatReference(reference, 1)).not.toContain('［１］')
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

describe('pasted abbreviated authors', () => {
  it.each([
    ['A. Smith. System study[C]. Proceedings, London: Press, 2024: 1-9. DOI: 10.1234/conf.24',
      { kind: 'conference', authors: 'A. Smith', title: 'System study', doi: '10.1234/conf.24', pages: '1-9' }],
    ['A. Smith. Online Study[EB/OL]. Example, 2024-10-01. https://Example.test/Case?q=One（引用日期：2026-11-05）. DOI: 10.1234/Web.26',
      { kind: 'online', authors: 'A. Smith', title: 'Online Study', doi: '10.1234/Web.26', url: 'https://Example.test/Case?q=One' }],
    ['A. Group. Standard Name[S]. ISO 1234, London: Press, 2025. DOI: 10.1234/std.25',
      { kind: 'standard', authors: 'A. Group', title: 'Standard Name', doi: '10.1234/std.25', number: 'ISO 1234' }],
    ['J. R. R. Tolkien. Example[J]. Some Journal, 2024: 1-9. DOI: 10.1234/j.24',
      { kind: 'journal', authors: 'J. R. R. Tolkien', title: 'Example', doi: '10.1234/j.24' }],
    ['A. Smith. Study[D]. London: University, 2023: 1-9. DOI: 10.1234/thesis.23',
      { kind: 'thesis', authors: 'A. Smith', title: 'Study', doi: '10.1234/thesis.23' }],
    ['A. Smith. Book[M]. London: Publisher, 2022: 1-9. DOI: 10.1234/book.22',
      { kind: 'book', authors: 'A. Smith', title: 'Book', doi: '10.1234/book.22' }],
  ])('retains the author and source facts from %s', (raw, fields) => {
    expect(createPastedReference(raw)).toMatchObject({ ...fields, original_pasted_text: raw })
  })

  it('keeps an ambiguous title containing a period unparsed', () => {
    const raw = 'A. Smith. Mr. Title[J]. Some Journal, 2024: 1-9'
    expect(createPastedReference(raw)).toMatchObject({ kind: 'unparsed', original_pasted_text: raw })
  })
})

describe('pasted conference, online, and standard references', () => {
  const cases = [
    ['[1] 张三. 系统研究[C]. 智能会议论文集, 北京: 示例出版社, 2024: 12-18.',
      { kind: 'conference', authors: '张三', title: '系统研究', collection: '智能会议论文集', place: '北京', publisher: '示例出版社', year: '2024', pages: '12-18' },
      '[1] 张三. 系统研究[C]. 智能会议论文集，北京: 示例出版社，2024: 12-18'],
    ['[2] 李四. 学术资料[EB/OL]. 示例机构，2024-10-01. https://example.test/path(a)?x=1.2&y=3（引用日期：2026-11-05）',
      { kind: 'online', authors: '李四', title: '学术资料', source: '示例机构', publish_date: '2024-10-01', url: 'https://example.test/path(a)?x=1.2&y=3', access_date: '2026-11-05' },
      '[1] 李四. 学术资料[EB/OL]. 示例机构，2024-10-01. https://example.test/path(a)?x=1.2&y=3（引用日期：2026-11-05）'],
    ['[3] 文献著录规则[S]. GB/T 7714-2025，北京: 示例出版社，2025.',
      { kind: 'standard', title: '文献著录规则', number: 'GB/T 7714-2025', place: '北京', publisher: '示例出版社', year: '2025' },
      '[1] 文献著录规则[S]. GB/T 7714-2025，北京: 示例出版社，2025'],
  ]
  it.each(cases)('extracts all verifiable facts and keeps the source: %s', (raw, fields, formatted) => {
    const reference = createPastedReference(raw)
    expect(reference).toMatchObject({ ...fields, original_pasted_text: raw, unformatted_text: raw })
    expect(formatReference(reference, 1)).toBe(formatted)
  })
  it.each([
    '[1] 张三. 系统研究[C]. 智能会议论文集, 北京: 示例出版社, 2024: 12-18. 额外事实',
    '[2] 李四. 学术资料[EB/OL]. 示例机构，2024-10-01. https://example.test/a 额外事实',
    '[3] 文献著录规则[S]. GB/T 7714-2025，北京: 示例出版社，2025. 额外事实',
  ])('keeps unsupported additional information unparsed: %s', raw => {
    expect(createPastedReference(raw)).toMatchObject({ kind: 'unparsed', original_pasted_text: raw, unformatted_text: raw })
  })
})

describe('incomplete supported pasted references', () => {
  it.each([
    ['张三. 系统研究[C]. 会议论文集', { kind: 'conference', collection: '会议论文集' }],
    ['李四. 学术资料[EB/OL].', { kind: 'online', authors: '李四', title: '学术资料' }],
    ['文献著录规则[S].', { kind: 'standard', title: '文献著录规则' }],
    ['文献著录规则[S]. GB/T 7714-2025.', { kind: 'standard', number: 'GB/T 7714-2025' }],
  ])('retains identified facts for student completion: %s', (raw, fields) => {
    expect(createPastedReference(raw)).toMatchObject({ ...fields, original_pasted_text: raw })
  })
})

describe('reference fact fields', () => {
  it('exposes the author of a standard and DOI when the format supports it', () => {
    expect(REFERENCE_FIELDS.standard).toContain('authors')
    for (const kind of ['conference', 'thesis', 'online', 'standard']) expect(REFERENCE_FIELDS[kind]).toContain('doi')
  })
})

describe('DOI on pasted non-journal references', () => {
  it.each([
    ['张三. 系统研究[C]. 会议录, 北京: 出版社, 2024: 1-9. DOI: 10.1234/conf.24', 'conference'],
    ['李四. 学位论文[D]. 成都: 大学, 2023: 1-9. DOI: 10.1234/thesis.23', 'thesis'],
    ['王五. 专著[M]. 成都: 出版社, 2022: 2-5. DOI: 10.1234/book.22', 'book'],
    ['标准名称[S]. ISO 1234, London: Press, 2025. DOI: 10.1234/std.25', 'standard'],
    ['李四. 网页[EB/OL]. https://example.test/a（引用日期：2026-11-05）. DOI: 10.1234/web.26', 'online'],
  ])('keeps DOI and original for %s', (raw, kind) => {
    const reference = createPastedReference(raw)
    expect(reference.kind).toBe(kind)
    expect(reference.original_pasted_text).toBe(raw)
    expect(reference.doi).toMatch(/^10\.1234\//)
    expect(formatReference(reference, 1)).toContain(`DOI: ${reference.doi}`)
  })
  it('does not treat DOI-looking text within a URL as a separate identifier', () => {
    const raw = '作者. 网页[EB/OL]. https://example.test/path.DOI:10.1234/value'
    expect(createPastedReference(raw)).toMatchObject({ kind: 'online', url: 'https://example.test/path.DOI:10.1234/value', doi: '', original_pasted_text: raw })
  })
})
