import { isPublicHttpsApiBase } from '../src/utils/wechat-uat-preflight.ts'

const target = process.argv[2] || ''
if (target !== 'mp-weixin') process.exit(0)

const checked = isPublicHttpsApiBase(process.env.VITE_API_BASE_URL)
if (!checked.ok) {
  console.error(checked.reason || 'VITE_API_BASE_URL must be a public HTTPS origin')
  process.exit(1)
}

console.log(`Mini-program API base configured: ${checked.origin}`)
