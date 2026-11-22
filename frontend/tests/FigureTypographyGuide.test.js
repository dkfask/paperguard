import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FigureTypographyGuide from '../src/components/writer/FigureTypographyGuide.vue'
import { useI18n } from '../src/i18n'
import {
  formatCaptionFontSize,
  hasImageTypographyConstraint,
  hasUnverifiedRasterImages,
  isImageTypographyRule,
  isManualReviewImageBlocked,
  resolveFigureCaptionSizePt,
} from '../src/utils/figureGuide'

describe('figureGuide utilities', () => {
  it('resolves and formats figure caption font sizes with sensible fallback', () => {
    expect(resolveFigureCaptionSizePt({ styles: { figure_caption: { size_pt: 10.5 } } })).toBe(10.5)
    expect(resolveFigureCaptionSizePt({ styles: { figure_caption: { size_pt: 0 } } })).toBeNull()
    expect(resolveFigureCaptionSizePt(null)).toBeNull()

    expect(formatCaptionFontSize(10.5, 'zh')).toBe('五号（10.5pt）')
    expect(formatCaptionFontSize(9, 'zh')).toBe('小五（9pt）')
    expect(formatCaptionFontSize(11, 'zh')).toBe('11pt')
    expect(formatCaptionFontSize(null, 'zh')).toBe('五号（10.5pt）')
    expect(formatCaptionFontSize(10.5, 'en')).toBe('10.5pt')
  })

  it('detects image internal typography rules and export blocking accurately', () => {
    expect(isImageTypographyRule({ reason: 'image_typography_unverified' })).toBe(true)
    expect(isImageTypographyRule({ params: { image_typography_constraint: 'caption_font_size_upper_bound' } })).toBe(true)
    expect(isImageTypographyRule({ description: '图中标注符号文字字号不大于图题的字号' })).toBe(true)
    expect(isImageTypographyRule({ description: 'Figure internal label font size must not exceed caption size' })).toBe(true)
    expect(isImageTypographyRule({ description: '在目标版本 Word/WPS 打开成品，核对分页与目录。' })).toBe(false)

    expect(hasImageTypographyConstraint(
      { resolved_items: [{ params: { image_typography_constraint: 'caption_font_size_upper_bound' } }] },
    )).toBe(true)
    expect(hasImageTypographyConstraint({ resolved_items: [] })).toBe(false)

    expect(isManualReviewImageBlocked({
      status: 'required',
      checklist: [{ id: 'image-font', description: '图中标注符号文字字号不大于图题的字号' }],
    })).toBe(true)
    expect(isManualReviewImageBlocked({
      status: 'required',
      checklist: [{ id: 'office-review:1', description: '在目标版本 Word/WPS 打开成品，核对分页、目录。' }],
    })).toBe(false)
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

  it('renders strict vs gentle inline summary copy and supports English locale', () => {
    const gentle = mount(FigureTypographyGuide, {
      props: { variant: 'inline', captionSizePt: 10.5, strictRuleActive: false },
    })
    expect(gentle.get('summary').text()).toContain('插图建议')
    expect(gentle.classes()).not.toContain('is-strict')
    gentle.unmount()

    const strict = mount(FigureTypographyGuide, {
      props: { variant: 'inline', captionSizePt: 10.5, strictRuleActive: true },
    })
    expect(strict.get('summary').text()).toContain('将在导出交付时阻断下载')
    expect(strict.classes()).toContain('is-strict')
    strict.unmount()

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
