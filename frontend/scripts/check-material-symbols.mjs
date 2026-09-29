// 扫描全仓 material-symbols ligature 名，与 Google 官方 codepoints 比对。
// 未知 ligature 会被 Material Symbols 字体当普通文本渲染成 "__EXTENDED" 这类可见残字，
// 所以这类错误必须在提交前拦下来。
import fs from 'node:fs'
import path from 'node:path'

const VALID_FILE = process.argv[2] || '/tmp/material-symbols.codepoints'
if (!fs.existsSync(VALID_FILE)) {
  console.error('缺少官方 codepoints 文件:', VALID_FILE)
  process.exit(2)
}
const valid = new Set(
  fs.readFileSync(VALID_FILE, 'utf8').split('\n').map((l) => l.trim().split(/\s+/)[0]).filter(Boolean),
)
console.log('官方图标名数量:', valid.size)

function walk(dir, acc = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) walk(full, acc)
    else if (/\.(vue|js|ts)$/.test(entry.name)) acc.push(full)
  }
  return acc
}

const used = new Map()
for (const file of walk(path.resolve(process.cwd(), 'src'))) {
  fs.readFileSync(file, 'utf8').split('\n').forEach((line, i) => {
    const re = /class="[^"]*\bmaterial-symbols-outlined\b[^"]*"[^>]*>\s*([a-z0-9_]+)\s*</g
    let m
    while ((m = re.exec(line))) {
      if (!used.has(m[1])) used.set(m[1], [])
      used.get(m[1]).push(`${path.relative(process.cwd(), file)}:${i + 1}`)
    }
  })
}

const bad = [...used].filter(([name]) => !valid.has(name))
console.log('全仓使用的不重复图标名:', used.size)
console.log('官方不存在的:', bad.length)
if (bad.length) {
  console.log()
  for (const [name, sites] of bad.sort((a, b) => b[1].length - a[1].length)) {
    console.log(`❌ ${name}  (${sites.length} 处)`)
    for (const s of sites) console.log(`     ${s}`)
  }
  process.exit(1)
}
console.log('✅ 全部图标名均存在于官方 Material Symbols')
