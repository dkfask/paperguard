import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(new URL('..', import.meta.url).pathname)
const required = [
  'src/manifest.json',
  'src/pages.json',
  'src/main.ts',
  'src/App.vue',
  'src/api/request.ts',
  'src/stores/session.ts',
  'src/pages/login/index.vue',
  'src/pages/tasks/index.vue',
  'src/pages/messages/index.vue',
  'src/pages/profile/index.vue',
]

const missing = required.filter((file) => !existsSync(resolve(root, file)))
if (missing.length) {
  console.error(`Missing mini-program files: ${missing.join(', ')}`)
  process.exit(1)
}

const pages = readFileSync(resolve(root, 'src/pages.json'), 'utf8')
for (const path of ['pages/login/index', 'pages/tasks/index', 'pages/messages/index', 'pages/profile/index']) {
  if (!pages.includes(path)) {
    console.error(`Missing page registration: ${path}`)
    process.exit(1)
  }
}

console.log(`Mini-program MP0 structure OK (${required.length} files, 4 pages)`)
