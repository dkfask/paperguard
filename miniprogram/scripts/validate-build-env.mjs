const target = process.argv[2] || ''
const baseUrl = (process.env.VITE_API_BASE_URL || '').trim()

if (target !== 'mp-weixin') process.exit(0)

if (!baseUrl) {
  console.error('VITE_API_BASE_URL is required for a WeChat mini-program build')
  process.exit(1)
}

let parsed
try {
  parsed = new URL(baseUrl)
} catch {
  console.error('VITE_API_BASE_URL must be an absolute HTTPS URL')
  process.exit(1)
}

if (parsed.protocol !== 'https:') {
  console.error('VITE_API_BASE_URL must use HTTPS for a WeChat mini-program build')
  process.exit(1)
}

console.log(`Mini-program API base configured: ${parsed.origin}`)
