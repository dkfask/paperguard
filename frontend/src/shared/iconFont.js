/**
 * Material Symbols 图标字体可用性兜底。
 *
 * 字体随构建产物发布。一旦取不到，ligature 名（school / refresh / fact_check…）会被浏览器
 * 当普通文本直接渲染出来，整页变成英文单词。
 *
 * 纯 CSS 无法检测字体是否加载成功（早期那段 @supports (font-display: swap) 和
 * ::before { content: '' } 都是无效防护，已删除），所以在 JS 里探测。
 *
 * ⚠️ 这里踩过一个很隐蔽的坑，必须记清楚：
 *
 *   原实现是 `fontSet.check('18px "Material Symbols Outlined"')` 检查一次，
 *   失败就打上 .pg-icons-unavailable。**check() 本身没问题，顺序有问题。**
 *   FontFaceSet 只会在**有元素真的要用这个字体**时才发起下载。图标字体的
 *   CSS 规则恰好是 `.pg-icons-unavailable .material-symbols-outlined{display:none}`，
 *   于是形成自我实现的死循环：
 *
 *     ① 页面刚起来，此刻没有任何元素使用该字体 → 字体从未被请求
 *     ② check() 返回 false
 *     ③ 打上降级类 → 所有图标 display:none
 *     ④ 没有任何元素需要该字体 → 浏览器永远不会请求它
 *     ⑤ 类永不摘除 → **图标对所有用户永久不可见**
 *
 *   实测：字体文件一次请求都没发出，25 秒后 check() 仍为 false；
 *   而手动摘掉降级类后，浏览器立刻正常下载字体、字形正常渲染。
 *   换句话说，那套"防护"本身就是让图标消失的原因。
 *
 * 现在的做法：用 FontFaceSet.load() **显式**发起下载——它不依赖任何元素是否
 * 渲染，因此能跳出上述死循环。图标默认先隐藏（避免 ligature 名以英文露脸），
 * 字体就绪后**摘掉**降级类让图标出现；只有真的取不到才保持隐藏。
 * 关键是「失败可保持、成功必摘除」，绝不能只加不减。
 */

const FONT_SPEC = '18px "Material Symbols Outlined"'
// 任意一个真实存在的 ligature 名即可，用它触发对应字形的加载
const PROBE_TEXT = 'refresh'

// 这里刻意**不缓存**「当前是否已降级」到模块变量。
// DOM 上的 class 本身就是权威状态，缓存一份只会带来两个问题：
//   - 本函数被调用两次（路由重挂载、热更新）时，缓存与真实 class 会失步；
//   - 单测里模块状态跨用例泄漏，表现为「上一个用例的 true 让下一个用例
//     的 markUnavailable 提前 return，降级类根本没被加上」。
// 幂等地增删 class 本来就没有性能问题。
function markUnavailable() {
  document.documentElement.classList.add('pg-icons-unavailable')
}

function markAvailable() {
  document.documentElement.classList.remove('pg-icons-unavailable')
}

export function applyIconFontFallback() {
  if (typeof document === 'undefined' || !document.fonts) return
  const fontSet = document.fonts

  // 先按隐藏处理：宁可暂时没有图标，也不要让 "refresh" 这种英文单词露脸。
  // 下面加载成功会把它摘掉。
  markUnavailable()

  // 关键：load() 会真正发起下载，不依赖页面上有没有元素在用这个字体。
  // check() 则相反——它只回答「现在已加载的字体里有没有匹配的」，
  // 字体没被请求过时必然是 false，这正是原来那个死循环的根因。
  const settle = (faces) => {
    if (Array.isArray(faces) && faces.length > 0) markAvailable()
    else markUnavailable()
  }

  // 必须先判断函数是否存在再调用。直接调 fontSet.load 在老浏览器上会抛
  // TypeError，若只用 try/catch 兜住就会在这里 return，
  // **永远走不到下面的 check() 退路** —— 降级类就此永久锁死。
  if (typeof fontSet.load !== 'function') {
    // 老浏览器没有 FontFaceSet.load：退回 check()，且 ready 后再复查一次，
    // 避免只判一次就锁死。
    fontSet.ready
      .then(() => (fontSet.check(FONT_SPEC, PROBE_TEXT) ? markAvailable() : markUnavailable()))
      .catch(() => markUnavailable())
    return
  }

  let pending
  try {
    pending = fontSet.load(FONT_SPEC, PROBE_TEXT)
  } catch {
    markUnavailable()
    return
  }
  if (!pending || typeof pending.then !== 'function') {
    fontSet.ready
      .then(() => (fontSet.check(FONT_SPEC, PROBE_TEXT) ? markAvailable() : markUnavailable()))
      .catch(() => markUnavailable())
    return
  }

  pending.then(settle).catch(() => markUnavailable())

  // 兜底复查：即使 load() 因为某些实现差异没能摘类，
  // 字体就绪后再确认一次，保证「成功必摘除」。
  fontSet.ready
    .then(() => {
      if (document.documentElement.classList.contains('pg-icons-unavailable')
        && fontSet.check(FONT_SPEC, PROBE_TEXT)) {
        markAvailable()
      }
    })
    .catch(() => {})
}
