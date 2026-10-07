// 校验 router.js 里声明的 titleKey / 各视图 t() 使用的 i18n key 是否都有中文文案。
// 缺 key 不会报错，t() 会把 key 本身返回给用户（例如面包屑显示 route.xxx）。
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(process.cwd())
const i18nText = fs.readFileSync(path.join(root, 'src/i18n.js'), 'utf8')
const routerText = fs.readFileSync(path.join(root, 'src/router.js'), 'utf8')

// 收集 i18n 中定义的所有 key。i18n.js 混用两种写法：
//   带点号的必须加引号： 'route.home': '首页'
//   不带点号的用裸标识符： brandTagline: '毕业论文写作工作台'
const defined = new Set()
for (const m of i18nText.matchAll(/'([a-zA-Z][\w.]*)'\s*:\s*'/g)) defined.add(m[1])
for (const m of i18nText.matchAll(/(?:^|[,{]\s*)([a-zA-Z]\w*)\s*:\s*'/g)) defined.add(m[1])

// 路由声明的 titleKey
const titleKeys = [...routerText.matchAll(/titleKey:\s*'([^']+)'/g)].map((m) => m[1])

console.log('i18n 已定义 key 数量:', defined.size)
console.log('路由 titleKey 数量:', titleKeys.length)
console.log()
const missingTitle = titleKeys.filter((k) => !defined.has(k))
if (missingTitle.length) {
  console.log('❌ 缺失的 titleKey（会直接显示在标签标题和面包屑）:')
  for (const k of missingTitle) console.log('   ', k)
} else {
  console.log('✅ 所有 titleKey 均有文案')
}
console.log()

// 视图里使用的 t('...') key
const views = []
function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name === 'node_modules' || e.name.startsWith('.')) continue
    const full = path.join(dir, e.name)
    if (e.isDirectory()) walk(full)
    else if (/\.(vue|js|ts)$/.test(e.name)) views.push(full)
  }
}
walk(path.join(root, 'src'))

const missingUse = new Map()
for (const file of views) {
  const text = fs.readFileSync(file, 'utf8')
  // 用负向后顾排除 getText('x') / format('x') 这类以 t 结尾的函数名
  for (const m of text.matchAll(/(?<![A-Za-z0-9_$.])t\(\s*'([a-zA-Z][\w.]*)'/g)) {
    if (defined.has(m[1])) continue
    if (!missingUse.has(m[1])) missingUse.set(m[1], new Set())
    missingUse.get(m[1]).add(path.relative(root, file))
  }
}
console.log('❌ 视图中使用但 i18n 未定义的 key:', missingUse.size)
for (const [k, files] of [...missingUse].sort()) {
  console.log('   ', k, '→', [...files].join(', '))
}
if (!missingUse.size) console.log('✅ 视图 t() key 全部有定义')
if (missingTitle.length || missingUse.size) process.exitCode = 1
