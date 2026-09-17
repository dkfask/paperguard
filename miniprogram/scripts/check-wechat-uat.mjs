import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { wechatUatBlockers, wechatUatReady } from '../src/utils/wechat-uat-preflight.ts'

const root = resolve(new URL('..', import.meta.url).pathname)
const manifest = JSON.parse(readFileSync(resolve(root, 'src/manifest.json'), 'utf8'))
const envPath = resolve(root, '.env.production')
const envText = existsSync(envPath) ? readFileSync(envPath, 'utf8') : ''
const envApi = (envText.match(/^VITE_API_BASE_URL=(.*)$/m) || [])[1]
const envTemplates = (envText.match(/^VITE_SUBSCRIBE_TEMPLATE_IDS=(.*)$/m) || [])[1]

const blockers = wechatUatBlockers({
  apiBaseUrl: process.env.VITE_API_BASE_URL || envApi,
  mpAppId: manifest?.['mp-weixin']?.appid,
  wechatAppIdConfigured: Boolean(process.env.PAPERGUARD_WECHAT_APP_ID),
  wechatAppSecretConfigured: Boolean(process.env.PAPERGUARD_WECHAT_APP_SECRET),
  mpBindingSecretConfigured: Boolean(process.env.PAPERGUARD_MP_BINDING_SECRET),
  subscribeTemplatesConfigured: Boolean(
    process.env.PAPERGUARD_WECHAT_SUBSCRIBE_TEMPLATES
    || process.env.VITE_SUBSCRIBE_TEMPLATE_IDS
    || envTemplates,
  ),
})

for (const item of blockers) {
  const mark = item.status === 'ready' ? 'READY' : 'BLOCKED'
  console.log(`${mark}\t${item.id}\t${item.item}\t${item.reason}`)
}

if (wechatUatReady(blockers)) {
  console.log('WeChat device UAT preflight READY')
  process.exit(0)
}

console.log('WeChat device UAT preflight BLOCKED — 本地代码门禁不能替代真机点击')
process.exit(2)
