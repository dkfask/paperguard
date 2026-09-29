/**
 * 图标字体兜底逻辑的回归测试。
 *
 * 这个模块曾经有一个自我实现的死循环：check() 在字体被请求之前就运行，
 * 返回 false 后打上 .pg-icons-unavailable，而该类又让所有图标 display:none，
 * 于是字体永远不会被请求、类永远不会被摘除 —— 图标对所有用户永久不可见。
 *
 * 这里的每条用例都在盯那个不变量：**成功必摘除降级类**。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { applyIconFontFallback } from '../src/shared/iconFont'

const CLASS = 'pg-icons-unavailable'

function setUpDocumentFontApi({ load, check, status = 'loaded', readyError = null }) {
  const readyPromise = readyError ? Promise.reject(readyError) : Promise.resolve()
  // 避免未处理的 rejection 干扰测试输出
  readyPromise.catch(() => {})
  const fontSet = {
    status,
    ready: readyPromise,
    check: vi.fn(check ?? (() => true)),
    load: load === undefined ? vi.fn(() => Promise.resolve([{ status: 'loaded' }])) : load,
  }
  vi.stubGlobal('document', {
    documentElement: { classList: document.createElement('div').classList },
    fonts: fontSet,
  })
  return fontSet
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

describe('applyIconFontFallback', () => {
  it('字体加载成功后必须摘掉降级类（这条曾被违反）', async () => {
    const fontSet = setUpDocumentFontApi({
      load: vi.fn(() => Promise.resolve([{ status: 'loaded' }])),
      check: vi.fn(() => true),
    })
    applyIconFontFallback()
    // 立即应是隐藏态：避免 ligature 名以英文露脸
    expect(document.documentElement.classList.contains(CLASS)).toBe(true)
    // 关键：load() 成功后必须翻回可用态，而不是只加不减
    await new Promise((r) => setTimeout(r, 0))
    await fontSet.ready.catch(() => {})
    expect(fontSet.load).toHaveBeenCalled()
    expect(document.documentElement.classList.contains(CLASS)).toBe(false)
  })

  it('load() 必须被真正调用——不能只靠 check()', async () => {
    // check() 返回 false（模拟「字体还没被请求」），
    // 但 load() 能拿到 face。此时必须以 load() 的结果为准。
    const fontSet = setUpDocumentFontApi({
      load: vi.fn(() => Promise.resolve([{ status: 'loaded' }])),
      check: vi.fn(() => false),
    })
    applyIconFontFallback()
    await new Promise((r) => setTimeout(r, 0))
    expect(fontSet.load).toHaveBeenCalledWith(
      expect.stringContaining('Material Symbols Outlined'),
      expect.any(String),
    )
    expect(document.documentElement.classList.contains(CLASS)).toBe(false)
  })

  it('字体真的取不到时保持隐藏（不要露出英文单词）', async () => {
    setUpDocumentFontApi({
      load: vi.fn(() => Promise.reject(new Error('network'))),
      check: vi.fn(() => false),
    })
    applyIconFontFallback()
    await new Promise((r) => setTimeout(r, 0))
    expect(document.documentElement.classList.contains(CLASS)).toBe(true)
  })

  it('load() 返回空数组时视为不可用', async () => {
    setUpDocumentFontApi({
      load: vi.fn(() => Promise.resolve([])),
      // load() 明确返回空 → 字体确实不可用；此时 check() 不该翻案
      check: vi.fn(() => false),
    })
    applyIconFontFallback()
    await new Promise((r) => setTimeout(r, 0))
    expect(document.documentElement.classList.contains(CLASS)).toBe(true)
  })

  it('没有 FontFaceSet.load 时退回 check()，且成功仍要摘类', async () => {
    const fontSet = setUpDocumentFontApi({
      load: undefined,
      check: vi.fn(() => true),
    })
    fontSet.load = undefined
    applyIconFontFallback()
    await new Promise((r) => setTimeout(r, 0))
    await fontSet.ready
    expect(document.documentElement.classList.contains(CLASS)).toBe(false)
  })

  it('没有 document.fonts 时静默退出，不抛错', () => {
    vi.stubGlobal('document', { documentElement: { classList: { add: vi.fn(), remove: vi.fn() } } })
    expect(() => applyIconFontFallback()).not.toThrow()
  })
})
