import { afterEach, describe, expect, it } from 'vitest'
import { currentLang, translations, useI18n } from '../src/i18n'

describe('i18n', () => {
  afterEach(() => {
    useI18n().setLang('zh')
    localStorage.removeItem('paperguard_lang')
  })

  it('translates interpolated messages and persists the selected language', () => {
    const { setLang, t } = useI18n()

    setLang('en')

    expect(currentLang.value).toBe('en')
    expect(localStorage.getItem('paperguard_lang')).toBe('en')
    expect(t('templates.stylesDetected', { count: 2, styles: 'Body, Heading 1' })).toBe('2 styles detected: Body, Heading 1')
  })

  it('falls back to Chinese and ignores unsupported languages', () => {
    const { setLang, t } = useI18n()

    setLang('en')
    setLang('fr')

    expect(currentLang.value).toBe('en')
    expect(t('missing.translation.key')).toBe('missing.translation.key')
  })

  it('formats dates and numbers with the active locale', () => {
    const { setLang, formatDate, formatNumber } = useI18n()

    setLang('en')

    expect(formatDate('2026-09-12T08:30:00Z', { year: 'numeric' })).toContain('2026')
    expect(formatNumber(1234567)).toContain('1,234,567')
  })

  it('keeps Chinese and English dictionaries in sync', () => {
    expect(Object.keys(translations.zh).sort()).toEqual(Object.keys(translations.en).sort())
  })
})
