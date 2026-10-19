import { describe, expect, it, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import {
  clearNavigationFailure,
  navigationFailure,
  reportNavigationFailure,
} from '../src/shared/navigationFailure.js'

const read = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8')

describe('navigationFailure（F-10 导航静默失效）', () => {
  beforeEach(() => clearNavigationFailure())

  it('识别动态 import 失败并记住目标地址，供整页重试使用', () => {
    const error = new TypeError('Failed to fetch dynamically imported module: http://x/assets/Workspace-abc.js')
    reportNavigationFailure(error, { fullPath: '/workspace' })

    expect(navigationFailure.value.chunkLoadFailed).toBe(true)
    expect(navigationFailure.value.target).toBe('/workspace')
  })

  it('非 chunk 类导航错误标记为通用错误，仍要暴露给用户', () => {
    reportNavigationFailure(new Error('守卫里抛错了'), { fullPath: '/workflow' })

    expect(navigationFailure.value.chunkLoadFailed).toBe(false)
    expect(navigationFailure.value.target).toBe('/workflow')
  })

  it('支持被导航条清除，避免横幅残留', () => {
    reportNavigationFailure(new Error('boom'), { fullPath: '/x' })
    expect(navigationFailure.value).not.toBeNull()
    clearNavigationFailure()
    expect(navigationFailure.value).toBeNull()
  })

  it('路由在导航失败时不提前改标题（标题只应在 afterEach 写入）', () => {
    const router = read('../src/router.js')
    const beforeEach = router.slice(router.indexOf('router.beforeEach'), router.indexOf('router.afterEach'))
    const afterEach = router.slice(router.indexOf('router.afterEach'), router.indexOf('router.onError'))

    expect(beforeEach).not.toContain('document.title')
    expect(afterEach).toContain('document.title')
    expect(router).toContain('router.onError')
  })
})

describe('F-06 写作页可访问名称', () => {
  const writer = read('../src/views/Writer.vue')

  it('material-symbols 图标一律对读屏隐藏（ligature 文本不得进可访问名称）', () => {
    const iconSpans = writer.match(/<span[^>]*class="material-symbols-outlined[^"]*"[^>]*>/g) || []
    expect(iconSpans.length).toBeGreaterThan(0)
    const missing = iconSpans.filter((tag) => !tag.includes('aria-hidden="true"'))
    expect(missing).toEqual([])
  })

  it('窄屏会折叠文字标签的三个按钮必须自带 aria-label', () => {
    // 这三个按钮在 Writer.vue 的 @media (max-width: 768px) 里被 display:none 收成图标，
    // 文字一并从无障碍树移除，没有 aria-label 就会完全没有可访问名称。
    //
    // 注意：按钮属性里含有 `:disabled="... dirtyCount > 0"`，值中有 `>`，
    // 所以不能用朴素的 `<button[^>]*>` 截断，否则会在属性中间断开。
    const tagOf = (cls) => (writer.match(
      new RegExp(`<button(?:[^>"]|"[^"]*")*class="[^"]*\\b${cls}\\b[^"]*"(?:[^>"]|"[^"]*")*>`),
    ) || [])[0]

    for (const cls of ['writer-review-action', 'writer-fix-action', 'writer-import-action', 'writer-preview-action', 'writer-export-action']) {
      const tag = tagOf(cls)
      expect(tag, `${cls} 按钮未找到`).toBeTruthy()
      expect(tag, `${cls} 缺少 aria-label`).toContain('aria-label')
    }
  })

  it('章节大纲常驻写作区，工具栏按钮只负责定位到大纲', () => {
    expect(writer).toContain('data-test="writer-outline"')
    expect(writer).toContain('aria-controls="writer-outline-panel"')
    expect(writer).toContain('@click="focusOutline"')
    expect(writer).not.toContain('presentation="drawer" :open="outlineOpen"')
  })
})

describe('F-11 状态徽章样式作用域', () => {
  it('.state-tag 配色必须放在全局样式，而不是某个视图的 scoped 样式里', () => {
    const styleCss = read('../src/style.css')
    expect(styleCss).toMatch(/^\.state-tag \{/m)
    expect(styleCss).toContain('.state-tag.approved')
    expect(styleCss).toContain('.state-tag.returned')

    // scoped 样式只对带该组件 scope id 的元素生效，
    // StudentWorkspace 等处渲染的 .state-tag 拿不到这里的配色。
    const workflow = read('../src/views/Workflow.vue')
    const styleBlock = workflow.slice(workflow.lastIndexOf('<style'))
    expect(styleBlock).not.toMatch(/^\.state-tag \{/m)
  })
})
