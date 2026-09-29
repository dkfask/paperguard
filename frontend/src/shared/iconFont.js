/**
 * Material Symbols 图标字体可用性兜底。
 *
 * 字体来自 CDN，一旦取不到，ligature 名（school / refresh / fact_check…）会被浏览器
 * 当普通文本直接渲染出来，整页变成英文单词。
 *
 * 纯 CSS 无法检测字体是否加载成功（早期那段 @supports (font-display: swap) 和
 * ::before { content: '' } 都是无效防护，已删除），所以在字体就绪后用 FontFaceSet
 * 探测一次；不可用就给 <html> 打标记，由 CSS 隐藏图标。
 *
 * 图标元素一律带 aria-hidden="true" 或伴随可见文字，因此隐藏图标不损失任何信息。
 */
export function applyIconFontFallback() {
  if (typeof document === 'undefined' || !document.fonts) return
  const fontSet = document.fonts
  const check = () => {
    // 18px 与 style.css 中 .material-symbols-outlined 的 font-size 一致
    if (!fontSet.check('18px "Material Symbols Outlined"')) {
      document.documentElement.classList.add('pg-icons-unavailable')
    }
  }
  if (fontSet.status === 'loaded') {
    check()
    return
  }
  fontSet.ready.then(check).catch(() => {
    document.documentElement.classList.add('pg-icons-unavailable')
  })
}
