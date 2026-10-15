import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('bundled interface fonts', () => {
  it('loads its stylesheet locally and ships every referenced font with its recorded digest', () => {
    const root = resolve(process.cwd(), 'public/fonts')
    const index = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8')
    expect(index).toContain('href="/fonts/fonts.css"')
    expect(index).not.toMatch(/https:\/\/(?:fonts\.(?:loli\.net|googleapis\.com)|fonts\.gstatic\.com)/)
    const css = readFileSync(resolve(root, 'fonts.css'), 'utf8')
    expect(css).not.toContain('https://')
    const sources = JSON.parse(readFileSync(resolve(root, 'SOURCES.json'), 'utf8'))
    const referenced = new Set([...css.matchAll(/url\(\/fonts\/([^\)]+)\)/g)].map(match => match[1]))
    expect(referenced.size).toBe(sources.files.length)
    for (const font of sources.files) {
      expect(referenced.has(font.file)).toBe(true)
      const bytes = readFileSync(resolve(root, font.file))
      expect(bytes.subarray(0, 4).toString()).toBe('wOF2')
      expect(createHash('sha256').update(bytes).digest('hex')).toBe(font.sha256)
    }
  })
})
