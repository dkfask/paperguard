import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(process.cwd())
const fontRoot = resolve(root, 'public/fonts')
const externalFontHost = /fonts\.(?:loli\.net|googleapis\.com|gstatic\.com)|fonts\.bunny\.net|fontshare\.com|use\.typekit\.net|cdn\.jsdelivr\.net/

function read(path) {
  return readFileSync(path, 'utf8')
}

describe('自托管界面字体', () => {
  it('index.html 只引用本地字体样式，不再预连接外部字体服务', () => {
    const html = read(resolve(root, 'index.html'))
    expect(html).toContain('href="/fonts/fonts.css"')
    expect(html).not.toMatch(externalFontHost)
    expect(html).not.toMatch(/rel="preconnect"/)
  })

  it('只发布界面实际用到的字重和正体，并带上许可证', () => {
    const css = read(resolve(fontRoot, 'fonts.css'))
    expect(css).not.toMatch(/https?:\/\//)
    expect(css).not.toMatch(/font-style:\s*italic/)
    const faces = [...css.matchAll(/font-family:\s*"([^"]+)";[\s\S]*?font-weight:\s*(\d+)/g)]
      .map((match) => `${match[1]} ${match[2]}`)
    expect(faces).toEqual([
      'Geist 400',
      'Geist 500',
      'Geist 600',
      'Geist 700',
      'JetBrains Mono 400',
      'JetBrains Mono 600',
      'JetBrains Mono 700',
      'Source Serif 4 700',
      'Material Symbols Outlined 400',
    ])
    for (const license of [
      'geist-OFL.txt',
      'jetbrains-mono-OFL.txt',
      'source-serif-4-OFL.txt',
      'material-symbols-outlined-OFL.txt',
    ]) {
      expect(read(resolve(fontRoot, license))).toContain('SIL OPEN FONT LICENSE')
    }
  })

  it('每个 woff2 都是本地文件，且与来源清单的摘要一致', () => {
    const css = read(resolve(fontRoot, 'fonts.css'))
    const sources = JSON.parse(read(resolve(fontRoot, 'SOURCES.json')))
    const referenced = [...css.matchAll(/url\("\.\/([^"]+\.woff2)"\)/g)].map((match) => match[1])
    expect(referenced).toEqual(sources.files.map((font) => font.file))
    let total = 0
    for (const font of sources.files) {
      const bytes = readFileSync(resolve(fontRoot, font.file))
      expect(bytes.subarray(0, 4).toString()).toBe('wOF2')
      expect(createHash('sha256').update(bytes).digest('hex')).toBe(font.sha256)
      expect(bytes.length).toBe(font.bytes)
      total += bytes.length
    }
    expect(total).toBe(sources.total_bytes)
  })
})
