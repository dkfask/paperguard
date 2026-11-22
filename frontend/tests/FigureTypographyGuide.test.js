import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FigureTypographyGuide from '../src/components/writer/FigureTypographyGuide.vue'
import { useI18n } from '../src/i18n'
import {
  formatCaptionFontSize,
  hasImageTypographyConstraint,
  hasUnverifiedRasterImages,
  isHardImageTypographyRule,
  isImageTypographyRule,
  isManualReviewImageBlocked,
  resolveFigureCaptionSizePt,
} from '../src/utils/figureGuide'

describe('figureGuide utilities', () => {
  it('prefers frozen execution profile figure_caption font.size over styles and falls back cleanly', () => {
    expect(resolveFigureCaptionSizePt({
      styles: { figure_caption: { size_pt: 12 } },
      source_execution: {
        object_properties: {
          figure_caption: {
            'font.size': { value: 9, unit: 'pt' },
          },
        },
      },
    })).toBe(9)

    expect(resolveFigureCaptionSizePt({
      styles: { figure_caption: { size_pt: 10.5 } },
      source_execution: {
        object_properties: {
          figure_caption: {
            'font.size': { value: 21, unit: 'half-pt' },
          },
        },
      },
    })).toBe(10.5)

    expect(resolveFigureCaptionSizePt({
      styles: { figure_caption: { size_pt: 10.5 } },
      source_execution: {
        object_properties: {
          figure_caption: {
            'font.size': { value: 0, unit: 'pt' },
          },
        },
      },
    })).toBe(10.5)

    expect(resolveFigureCaptionSizePt({
      styles: { figure_caption: { size_pt: 10.5 } },
      source_execution: {
        object_properties: {
          figure_caption: {
            'font.size': { value: true, unit: 'pt' },
          },
        },
      },
    })).toBe(10.5)

    expect(resolveFigureCaptionSizePt({ styles: { figure_caption: { size_pt: 10.5 } } })).toBe(10.5)
    expect(resolveFigureCaptionSizePt({ styles: { figure_caption: { size_pt: 0 } } })).toBeNull()
    expect(resolveFigureCaptionSizePt(null)).toBeNull()

    expect(formatCaptionFontSize(10.5, 'zh')).toBe('五号（10.5pt）')
    expect(formatCaptionFontSize(9, 'zh')).toBe('小五（9pt）')
    expect(formatCaptionFontSize(11, 'zh')).toBe('11pt')
    expect(formatCaptionFontSize(null, 'zh')).toBe('五号（10.5pt）')
    expect(formatCaptionFontSize(10.5, 'en')).toBe('10.5pt')
  })

  it('detects hard vs soft image internal typography rules and export blocking accurately', () => {
    expect(isImageTypographyRule({ reason: 'image_typography_unverified' })).toBe(true)
    expect(isImageTypographyRule({ params: { image_typography_constraint: 'caption_font_size_upper_bound' } })).toBe(true)
    expect(isImageTypographyRule({ description: '图中标注符号文字字号不大于图题的字号' })).toBe(true)
    expect(isImageTypographyRule({ description: '图题字号必须为五号' })).toBe(false)
    expect(isImageTypographyRule({ description: 'Figure caption font size must be 10.5pt' })).toBe(false)
    expect(isImageTypographyRule({
      enforce: 'hard',
      params: { image_typography_constraint: 'caption_font_size_upper_bound' },
      description: 'Figure internal label font size must not exceed caption size',
    })).toBe(true)
    expect(isImageTypographyRule({ description: '在目标版本 Word/WPS 打开成品，核对分页与目录。' })).toBe(false)
    expect(hasImageTypographyConstraint({
      resolved_items: [{ enforce: 'hard', description: 'Figure caption font size must be 10.5pt' }],
    })).toBe(false)

    expect(isHardImageTypographyRule({ enforce: 'hard', reason: 'image_typography_unverified' })).toBe(true)
    expect(isHardImageTypographyRule({ enforce: 'soft', reason: 'image_typography_unverified' })).toBe(false)
    expect(isHardImageTypographyRule({ params: { image_typography_constraint: 'caption_font_size_upper_bound' } })).toBe(false)

    expect(hasImageTypographyConstraint(
      { resolved_items: [{ enforce: 'hard', params: { image_typography_constraint: 'caption_font_size_upper_bound' } }] },
    )).toBe(true)
    expect(hasImageTypographyConstraint(
      { resolved_items: [{ enforce: 'soft', params: { image_typography_constraint: 'caption_font_size_upper_bound' } }] },
      { not_checked: [{ reason: 'image_typography_unverified', enforce: 'soft' }] },
      { status: 'not_required', checklist: [{ id: 'image-font', description: '图中标注符号文字字号不大于图题的字号' }] },
    )).toBe(false)
    expect(hasImageTypographyConstraint(
      { resolved_items: [] },
      { not_checked: [{ reason: 'image_typography_unverified', enforce: 'hard' }] },
    )).toBe(true)
    expect(hasImageTypographyConstraint({ resolved_items: [] })).toBe(false)

    expect(isManualReviewImageBlocked({
      status: 'required',
      checklist: [{ id: 'image-font', description: '图中标注符号文字字号不大于图题的字号' }],
    })).toBe(true)
    expect(isManualReviewImageBlocked(
      {
        status: 'required',
        checklist: [{ id: 'office-review:1', description: '在目标版本 Word/WPS 打开成品，核对分页、目录。' }],
      },
      { not_checked: [{ reason: 'image_typography_unverified', enforce: 'soft' }] },
    )).toBe(false)
    expect(isManualReviewImageBlocked(
      {
        status: 'required',
        checklist: [{ id: 'office-review:1', description: '在目标版本 Word/WPS 打开成品，核对分页、目录。' }],
      },
      { not_checked: [{ reason: 'image_typography_unverified', enforce: 'hard' }] },
    )).toBe(true)
    expect(isManualReviewImageBlocked({
      status: 'not_required',
      checklist: [{ id: 'image-font', description: '图中标注符号文字字号不大于图题的字号' }],
    })).toBe(false)
  })

  it('distinguishes unverified raster images from preserved Word SmartArt blocks', () => {
    const onlySmartArt = [{
      key: 'ch4',
      paragraphs: [{ id: 'sa-1', type: 'image', image: { caption: '架构图', smartart: { dm: '<d/>' } } }],
    }]
    expect(hasUnverifiedRasterImages(onlySmartArt)).toBe(false)

    const withRaster = [{
      key: 'ch4',
      paragraphs: [{ id: 'img-1', type: 'image', image: { url: '/media/uploads/shot.png', caption: '截图' } }],
    }]
    expect(hasUnverifiedRasterImages(withRaster)).toBe(true)
  })
})

describe('FigureTypographyGuide component', () => {
  afterEach(() => {
    useI18n().setLang('zh')
    localStorage.removeItem('paperguard_lang')
  })

  it('renders non-intrusive collapsed SmartArt examples and M1 closed-import boundary in Chinese', () => {
    const wrapper = mount(FigureTypographyGuide, {
      props: {
        variant: 'blocked',
        captionSizePt: 10.5,
        manuscriptImportEnabled: false,
        strictRuleActive: true,
      },
    })

    const details = wrapper.get('[data-test="figure-typography-guide"]')
    expect(details.attributes('open')).toBeUndefined()
    expect(details.attributes('data-guide-variant')).toBe('blocked')
    expect(wrapper.findAll('button, input')).toHaveLength(0)

    const text = wrapper.text()
    expect(text).toContain('五号（10.5pt）')
    expect(text).toContain('小五（9pt）')
    expect(text).toContain('插入 → SmartArt')
    expect(text).toContain('image_typography_unverified')
    expect(text).toContain('M1 试点默认关闭整篇 Word 导入')
    expect(text).toContain('查看待核验详情')
    expect(wrapper.find('[data-test="figure-guide-examples"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="figure-guide-steps"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="figure-guide-actions"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('adapts student action guidance when whole-document import is enabled', () => {
    const wrapper = mount(FigureTypographyGuide, {
      props: {
        variant: 'check',
        captionSizePt: 10.5,
        manuscriptImportEnabled: true,
        strictRuleActive: true,
        defaultOpen: true,
      },
    })

    expect(wrapper.get('[data-test="figure-typography-guide"]').attributes('open')).toBeDefined()
    expect(wrapper.text()).toContain('导入正文')
    expect(wrapper.text()).not.toContain('M1 试点默认关闭整篇 Word 导入')
    wrapper.unmount()
  })

  it('keeps non-strict inline guidance advisory without export-blocking or screenshot-removal claims', () => {
    const gentleZh = mount(FigureTypographyGuide, {
      props: { variant: 'inline', captionSizePt: 10.5, strictRuleActive: false, defaultOpen: true },
    })
    expect(gentleZh.get('summary').text()).toContain('插图建议')
    expect(gentleZh.classes()).not.toContain('is-strict')
    expect(gentleZh.find('.figure-guide-card.is-blocked').exists()).toBe(false)
    expect(gentleZh.find('.figure-guide-card.is-advisory').exists()).toBe(true)
    expect(gentleZh.find('[data-test="figure-guide-actions"]').exists()).toBe(false)
    expect(gentleZh.text()).toContain('未启用图内字号硬性核验规则')
    expect(gentleZh.text()).not.toContain('image_typography_unverified')
    expect(gentleZh.text()).not.toContain('阻断')
    expect(gentleZh.text()).not.toContain('移除')
    gentleZh.unmount()

    useI18n().setLang('en')
    const gentleEn = mount(FigureTypographyGuide, {
      props: { variant: 'inline', captionSizePt: 10.5, strictRuleActive: false, defaultOpen: true },
    })
    expect(gentleEn.get('summary').text()).toContain('Figure tip:')
    expect(gentleEn.find('.figure-guide-card.is-blocked').exists()).toBe(false)
    expect(gentleEn.find('[data-test="figure-guide-actions"]').exists()).toBe(false)
    expect(gentleEn.text()).toContain('does not enforce an in-figure typography gate')
    expect(gentleEn.text()).not.toContain('image_typography_unverified')
    expect(gentleEn.text()).not.toMatch(/\bblock(?:ed|ing)?\b/i)
    expect(gentleEn.text()).not.toMatch(/\bremove\b/i)
    gentleEn.unmount()

    useI18n().setLang('zh')
    const softCheckZh = mount(FigureTypographyGuide, {
      props: { variant: 'check', captionSizePt: 10.5, strictRuleActive: false, defaultOpen: true },
    })
    expect(softCheckZh.get('summary').text()).toContain('为什么普通位图暂无法自动核验图中字号')
    expect(softCheckZh.get('summary').text()).not.toContain('阻断')
    expect(softCheckZh.classes()).not.toContain('is-strict')
    expect(softCheckZh.find('.figure-guide-card.is-blocked').exists()).toBe(false)
    expect(softCheckZh.find('[data-test="figure-guide-actions"]').exists()).toBe(false)
    softCheckZh.unmount()
  })

  it('renders strict inline summary copy, safe recommended sizes, and English blocked locale', () => {
    const strict = mount(FigureTypographyGuide, {
      props: { variant: 'inline', captionSizePt: 10.5, strictRuleActive: true, defaultOpen: true },
    })
    expect(strict.get('summary').text()).toContain('将在导出交付时阻断下载')
    expect(strict.classes()).toContain('is-strict')
    expect(strict.find('.figure-guide-card.is-blocked').exists()).toBe(true)
    expect(strict.find('[data-test="figure-guide-actions"]').exists()).toBe(true)
    expect(strict.text()).toContain('image_typography_unverified')
    strict.unmount()

    const smallCaption = mount(FigureTypographyGuide, {
      props: { variant: 'blocked', captionSizePt: 7.5, strictRuleActive: true, defaultOpen: true },
    })
    expect(smallCaption.text()).toContain('7.5pt')
    expect(smallCaption.text()).not.toContain('8pt')
    smallCaption.unmount()

    useI18n().setLang('en')
    const english = mount(FigureTypographyGuide, {
      props: { variant: 'blocked', captionSizePt: 10.5, manuscriptImportEnabled: false, strictRuleActive: true },
    })
    expect(english.text()).toContain('Insert -> SmartArt')
    expect(english.text()).toContain('10.5pt')
    expect(english.text()).toContain('Whole-document Word import remains closed')
    english.unmount()
  })
})
