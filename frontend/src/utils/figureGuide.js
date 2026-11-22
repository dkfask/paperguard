/** Shared constants and helpers for M1 SmartArt authoring and raster-image export blocking guidance. */

export const IMAGE_TYPOGRAPHY_REASON = 'image_typography_unverified'
export const IMAGE_TYPOGRAPHY_CONSTRAINT = 'caption_font_size_upper_bound'
export const DEFAULT_CAPTION_SIZE_PT = 10.5

export const CHINESE_FONT_SIZE_NAMES = Object.freeze({
  42: '初号',
  36: '小初',
  26: '一号',
  24: '小一',
  22: '二号',
  18: '小二',
  16: '三号',
  15: '小三',
  14: '四号',
  12: '小四',
  10.5: '五号',
  9: '小五',
})

const ZH_IMAGE_TYPOGRAPHY_SUBJECT_RE = /(?:插图|图片|图像|图形|图)(?:中|内|里)/
const ZH_IMAGE_TYPOGRAPHY_SIZE_RE = /字号|字体大小/
const EN_IMAGE_TYPOGRAPHY_SUBJECT_RE = /\b(?:figure|image|diagram|illustration)\b/i
const EN_IMAGE_TYPOGRAPHY_SIZE_RE = /\b(?:font\s*size|typography|label\s*size)\b/i

function parsePositiveSizePt(raw) {
  if (typeof raw !== 'number' && typeof raw !== 'string') return null
  if (typeof raw === 'string' && !raw.trim()) return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

export function resolveFigureCaptionSizePt(template) {
  const profileFontSize = template?.source_execution?.object_properties?.figure_caption?.['font.size']
  if (
    profileFontSize
    && typeof profileFontSize === 'object'
    && profileFontSize.unit === 'pt'
    && typeof profileFontSize.value === 'number'
    && Number.isFinite(profileFontSize.value)
    && profileFontSize.value > 0
  ) {
    return profileFontSize.value
  }
  return parsePositiveSizePt(template?.styles?.figure_caption?.size_pt)
}

export function formatCaptionFontSize(sizePt, lang = 'zh') {
  const parsed = Number(sizePt)
  const effective = Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_CAPTION_SIZE_PT
  const formattedPt = Number.isInteger(effective) ? String(effective) : String(effective)
  if (lang === 'en') return `${formattedPt}pt`
  const zhName = CHINESE_FONT_SIZE_NAMES[effective]
  return zhName ? `${zhName}（${formattedPt}pt）` : `${formattedPt}pt`
}

export function isImageTypographyRule(item) {
  if (!item || typeof item !== 'object') return false
  if (item.reason === IMAGE_TYPOGRAPHY_REASON) return true
  if (item.params?.image_typography_constraint === IMAGE_TYPOGRAPHY_CONSTRAINT) return true
  const description = String(item.description || '')
  if (!description) return false
  if (ZH_IMAGE_TYPOGRAPHY_SUBJECT_RE.test(description) && ZH_IMAGE_TYPOGRAPHY_SIZE_RE.test(description)) {
    return true
  }
  return EN_IMAGE_TYPOGRAPHY_SUBJECT_RE.test(description) && EN_IMAGE_TYPOGRAPHY_SIZE_RE.test(description)
}

export function isHardImageTypographyRule(item) {
  return isImageTypographyRule(item) && item?.enforce === 'hard'
}

export function hasImageTypographyConstraint(requirementSnapshot, checkResult = null, manualReview = null) {
  if ((checkResult?.not_checked || []).some(isHardImageTypographyRule)) return true
  if (manualReview?.status === 'required' && (manualReview.checklist || []).some(isImageTypographyRule)) return true
  return (requirementSnapshot?.resolved_items || []).some(isHardImageTypographyRule)
}

export function isManualReviewImageBlocked(manualReview, checkResult = null) {
  if (manualReview?.status !== 'required') return false
  if ((manualReview.checklist || []).some(isImageTypographyRule)) return true
  return (checkResult?.not_checked || []).some((item) => item?.reason === IMAGE_TYPOGRAPHY_REASON && item?.enforce === 'hard')
}

export function isPreservedSmartArt(block) {
  return Boolean(block?.type === 'image' && block.image?.smartart && Object.keys(block.image.smartart).length)
}

export function hasUnverifiedRasterImages(sections) {
  for (const section of sections || []) {
    for (const block of section.paragraphs || []) {
      if (block?.type === 'image' && !isPreservedSmartArt(block) && block.image?.url) {
        return true
      }
    }
  }
  return false
}
