const REFERENCE_PREFIX = /^\s*(?:\[\s*(\d+)\s*\]|［\s*(\d+)\s*］)/

const MARKERS = {
  journal: 'J', book: 'M', thesis: 'D', conference: 'C', online: 'EB/OL', standard: 'S',
}

export const REFERENCE_FIELDS = {
  journal: ['authors', 'title', 'source', 'year', 'volume', 'issue', 'pages', 'doi'],
  book: ['authors', 'title', 'place', 'publisher', 'year', 'pages', 'doi'],
  thesis: ['authors', 'title', 'place', 'institution', 'year', 'pages'],
  conference: ['authors', 'title', 'collection', 'place', 'publisher', 'year', 'pages'],
  online: ['authors', 'title', 'source', 'url', 'publish_date', 'year', 'access_date'],
  standard: ['organization', 'title', 'number', 'place', 'publisher', 'year'],
}

export const REFERENCE_FIELD_LABELS = {
  authors: 'writer.refAuthors', title: 'writer.refTitle', source: 'writer.refSource',
  collection: 'writer.refCollection', place: 'writer.refPlace', publisher: 'writer.refPublisher',
  institution: 'writer.refInstitution', organization: 'writer.refOrganization', number: 'writer.refStandardNumber',
  year: 'writer.refYear', volume: 'writer.refVolume', issue: 'writer.refIssue', pages: 'writer.refPages',
  url: 'writer.refUrl', publish_date: 'writer.refPublishDate', access_date: 'writer.refAccessDate', doi: 'writer.refDoi',
}

export function referenceNumber(block) {
  const stored = block?.reference_number
  if (Number.isInteger(stored) && stored > 0) return stored
  if (block?.reference?.original_pasted_text != null) return null
  const raw = block?.reference?.unformatted_text || block?.text || ''
  const match = String(raw).match(REFERENCE_PREFIX)
  const number = match ? Number(match[1] || match[2]) : 0
  return Number.isSafeInteger(number) && number > 0 ? number : null
}

export function mergeReferenceNumbers(target, source) {
  const numbers = new Map((source || [])
    .filter(block => block?.id && Number.isInteger(block.reference_number) && block.reference_number > 0)
    .map(block => [block.id, block.reference_number]))
  for (const block of target || []) {
    if (numbers.has(block?.id)) block.reference_number = numbers.get(block.id)
  }
}

export function mergeReferenceLanguageFacts(target, source) {
  const saved = new Map((source || []).filter(block => block?.id).map(block => [block.id, block.reference?.language_fact || null]))
  for (const block of target || []) {
    if (block?.reference && saved.has(block.id)) block.reference.language_fact = saved.get(block.id)
  }
}

export function mergeReferenceYearFacts(target, source) {
  const saved = new Map((source || []).filter(block => block?.id).map(block => [block.id, block.reference?.year_fact || null]))
  for (const block of target || []) {
    if (block?.reference && saved.has(block.id)) block.reference.year_fact = saved.get(block.id)
  }
}

const languageIdentityFields = ['kind', 'authors', 'title', 'source', 'collection', 'publisher', 'institution',
  'organization', 'number', 'doi', 'url', 'original_pasted_text', 'unformatted_text']
const yearIdentityFields = [...languageIdentityFields, 'year', 'place', 'volume', 'issue', 'pages', 'publish_date', 'access_date']

function confirmationIdentity(reference, kind) {
  if (!reference) return ''
  const fields = kind === 'year' ? yearIdentityFields : languageIdentityFields
  const fact = reference[`${kind}_fact`] || {}
  return JSON.stringify([
    fields.map(field => reference[field] ?? ''),
    kind === 'year' ? String(fact.value || '').trim() : [...(fact.languages || [])].sort(),
    fact.evidence || '',
  ])
}

export function clearConfirmedReferenceIntents(current, sent, saved) {
  const sentBlocks = new Map((sent || []).map(block => [block.id, block.reference]))
  const savedBlocks = new Map((saved || []).map(block => [block.id, block.reference]))
  for (const block of current || []) {
    const reference = block.reference
    if (!reference) continue
    const sentReference = sentBlocks.get(block.id)
    const savedReference = savedBlocks.get(block.id)
    for (const kind of ['language', 'year']) {
      const flag = `${kind}_confirmation_requested`
      if (reference[flag] && sentReference?.[flag] && savedReference?.[`${kind}_fact`]?.status === 'confirmed'
          && confirmationIdentity(reference, kind) === confirmationIdentity(sentReference, kind)
          && confirmationIdentity(savedReference, kind) === confirmationIdentity(sentReference, kind)) delete reference[flag]
    }
  }
}

export function formatReference(reference, number) {
  if (!reference) return ''
  const value = key => String(reference[key] || '').trim()
  const raw = value('unformatted_text')
  const stripNumber = text => String(text || '').trim().replace(REFERENCE_PREFIX, '').trimStart()
  const numberText = text => number == null || !text ? text : `[${number}] ${stripNumber(text)}`
  if (reference.kind === 'unparsed') return numberText(raw)
  if (!['authors', 'title', 'source', 'collection', 'publisher', 'institution', 'organization'].some(key => value(key))) {
    return numberText(raw)
  }
  const authors = value('authors').replace(/[ .。]+$/, '')
  const title = value('title').replace(/[ .。]+$/, '')
  const marker = MARKERS[reference.kind] || 'J'
  const parts = []
  if (authors) parts.push(authors)
  if (title) parts.push(`${title}[${marker}]`)

  if (marker === 'J') {
    let tail = value('source') || value('collection')
    if (value('year')) tail += `${tail ? ', ' : ''}${value('year')}`
    if (value('volume') || value('issue')) tail += `, ${value('volume')}${value('issue') ? `(${value('issue')})` : ''}`
    if (value('pages')) tail += `: ${value('pages')}`
    if (tail) parts.push(tail)
  } else if (['M', 'C'].includes(marker)) {
    const publication = [value('place'), value('publisher')].filter(Boolean).join(': ')
    const tail = [value('collection') || value('source'), publication, value('year')].filter(Boolean).join('，')
    const final = tail && value('pages') ? `${tail}: ${value('pages')}` : tail || value('pages')
    if (final) parts.push(final)
  } else if (marker === 'D') {
    const tail = [value('place'), value('institution') || value('source'), value('year')].filter(Boolean).join('，')
    const final = tail && value('pages') ? `${tail}: ${value('pages')}` : tail || value('pages')
    if (final) parts.push(final)
  } else if (marker === 'S') {
    const tail = [value('organization') || value('source'), value('number'), value('year')].filter(Boolean).join('，')
    if (tail) parts.push(tail)
  } else {
    let tail = [value('source'), value('publish_date')].filter(Boolean).join('，')
    if (value('url')) tail += `${tail ? '. ' : ''}${value('url')}`
    if (value('access_date')) tail += `${tail ? ' ' : ''}（引用日期：${value('access_date')}）`
    if (tail) parts.push(tail)
  }
  if (value('doi')) parts.push(`DOI: ${value('doi')}`)
  const citation = parts.join('. ')
  return number == null ? (citation || raw) : `[${number}] ${citation || stripNumber(raw)}`.trim()
}

export function createReference(kind = 'journal', unformattedText = '') {
  return {
    kind,
    authors: '', title: '', source: '', collection: '', place: '', publisher: '',
    institution: '', organization: '', number: '', year: '', volume: '', issue: '',
    pages: '', url: '', publish_date: '', access_date: '', doi: '',
    unformatted_text: String(unformattedText || ''),
  }
}

function extractPastedReference(value) {
  const text = String(value || '').replace(REFERENCE_PREFIX, '').replace(/\r\n?/g, '\n').replace(/\s*\n\s*/g, ' ').trim()
  const match = text.match(/^([^。.]+)[.。]\s*([^。.]+?)\s*\[(EB\/OL|J|M|D|C|S)\](?:\s*[.。]\s*|\s*$)(.*)$/i)
  if (!match) return null
  const kinds = { J: 'journal', M: 'book', D: 'thesis', C: 'conference', 'EB/OL': 'online', S: 'standard' }
  const kind = kinds[match[3].toUpperCase()]
  const fields = { authors: match[1].trim(), title: match[2].trim() }
  if (!fields.authors || !fields.title) return null
  let fullyConsumed = false
  if (kind === 'journal') {
    let tail = match[4].trim()
    const doi = tail.match(/(?:[.。:：]\s*|\s+|(?<=\d))DOI\s*[:：]\s*(10\.\d{4,9}\/\S+?)\s*[.。]?\s*$/i)
    if (doi) {
      tail = tail.slice(0, doi.index).trim()
    }
    const candidates = []
    const yearSeparators = [...tail.matchAll(/[,，]\s*(?=(?:19|20)\d{2}(?=\s*(?:[,，.:：]|$)))/g)]
    for (const separator of yearSeparators) {
      const source = tail.slice(0, separator.index).trim()
      const journal = tail.slice(separator.index + separator[0].length).match(/^((?:19|20)\d{2})(?:\s*[,，]\s*(?:(\d+)(?:\(([^()]+)\))?|\(([^()]+)\)))?(?:\s*[:：]\s*([a-z]?\d+(?:[-–—][a-z]?\d+)?(?:\s*\+\s*[a-z]?\d+(?:[-–—][a-z]?\d+)?)*)\s*)?\s*[.。]?$/iu)
      if (source && journal) candidates.push({ source, journal })
    }
    if (candidates.length === 1 && yearSeparators.length === 1) {
      const { source, journal } = candidates[0]
      fields.source = source
      fields.year = journal[1]
      if (journal[2]) fields.volume = journal[2]
      if (journal[3] || journal[4]) fields.issue = journal[3] || journal[4]
      if (journal[5]) fields.pages = journal[5]
      if (doi) fields.doi = doi[1]
      const limits = { authors: 2000, title: 2000, source: 2000, year: 32, volume: 100, issue: 100, pages: 200, doi: 500 }
      fullyConsumed = Object.entries(fields).every(([field, value]) => value.length <= limits[field])
    }
  } else if (kind === 'book' || kind === 'thesis') {
    const tail = match[4].trim()
    const candidates = []
    const yearSeparators = [...tail.matchAll(/[,，]\s*(?=(?:19|20)\d{2})/g)]
    for (const separator of yearSeparators) {
      const publication = tail.slice(0, separator.index).trim()
      const remainder = tail.slice(separator.index + separator[0].length)
        .match(/^((?:19|20)\d{2})(?:\s*[:：]\s*([a-z]?\d+(?:[-–—][a-z]?\d+)?(?:\s*\+\s*[a-z]?\d+(?:[-–—][a-z]?\d+)?)*)\s*)?\s*[.。]?$/iu)
      const publisherParts = publication.split(/[:：]/).map(part => part.trim())
      if (remainder && publisherParts.length <= 2 && publisherParts.every(Boolean)) {
        candidates.push({ publisherParts, remainder })
      }
    }
    if (candidates.length === 1 && yearSeparators.length === 1) {
      const { publisherParts, remainder } = candidates[0]
      if (publisherParts.length === 2) fields.place = publisherParts[0]
      if (kind === 'book') fields.publisher = publisherParts[publisherParts.length - 1]
      else fields.institution = publisherParts[publisherParts.length - 1]
      fields.year = remainder[1]
      if (remainder[2]) fields.pages = remainder[2]
      const limits = { authors: 2000, title: 2000, place: 500, publisher: 1000, institution: 1000, year: 32, pages: 200 }
      fullyConsumed = Object.entries(fields).every(([field, value]) => value.length <= limits[field])
    }
  }
  return { kind, fields, fullyConsumed }
}

export function suggestPastedReference(value) {
  const result = extractPastedReference(value)
  return result ? { kind: result.kind, fields: result.fields } : null
}

export function splitPastedReferences(value, mode = 'auto') {
  const lines = String(value || '').replace(/\r\n?/g, '\n').split('\n')
  const first = lines.find(line => line.trim())
  if (!first) return []
  const numbered = mode === 'numbered' || (mode === 'auto' && REFERENCE_PREFIX.test(first))
  if (numbered && !REFERENCE_PREFIX.test(first)) throw new Error('numbering')

  const entries = []
  if (numbered) {
    let current = []
    const append = () => {
      while (current.length && !current[current.length - 1].trim()) current.pop()
      if (current.length) entries.push(current.join('\n'))
    }
    for (const line of lines) {
      if (REFERENCE_PREFIX.test(line)) {
        append()
        current = [line]
      } else if (current.length) {
        current.push(line)
      }
    }
    append()
  } else {
    entries.push(...lines.filter(line => line.trim()))
  }
  if (entries.length > 200) throw new Error('count')
  if (entries.some(entry => entry.length > 20000)) throw new Error('length')
  return entries
}

export function createPastedReference(text) {
  const reference = { ...createReference('unparsed', text), original_pasted_text: String(text) }
  const result = extractPastedReference(text)
  if (result?.fullyConsumed) Object.assign(reference, { kind: result.kind, ...result.fields })
  return reference
}
