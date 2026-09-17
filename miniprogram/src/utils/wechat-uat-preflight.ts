export type WechatUatBlocker = {
  id: string
  item: string
  status: 'ready' | 'blocked'
  reason: string
}

const PLACEHOLDER_HOSTS = new Set([
  'localhost',
  '127.0.0.1',
  '0.0.0.0',
  '::1',
  'example.com',
  'www.example.com',
  'paperguard.example.com',
  'example.edu',
  'paperguard.example.edu',
])

export function isPublicHttpsApiBase(raw: unknown): { ok: boolean; origin?: string; reason?: string } {
  const value = String(raw || '').trim()
  if (!value) return { ok: false, reason: 'VITE_API_BASE_URL 未配置' }
  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    return { ok: false, reason: 'VITE_API_BASE_URL 不是绝对 URL' }
  }
  if (parsed.protocol !== 'https:') return { ok: false, reason: 'VITE_API_BASE_URL 必须是 HTTPS' }
  const host = parsed.hostname.toLowerCase()
  if (
    PLACEHOLDER_HOSTS.has(host)
    || host.endsWith('.example.com')
    || host.endsWith('.example.net')
    || host.endsWith('.example.org')
    || host.endsWith('.example.edu')
    || host.endsWith('.local')
  ) {
    return { ok: false, reason: 'VITE_API_BASE_URL 不能是 localhost 或占位域名' }
  }
  if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) {
    return { ok: false, reason: 'VITE_API_BASE_URL 不能使用裸 IP，需备案域名' }
  }
  return { ok: true, origin: parsed.origin }
}

export function wechatAppIdReady(raw: unknown): boolean {
  const value = String(raw || '').trim()
  if (!value) return false
  if (value.includes('UNI__') || value.includes('PLACEHOLDER')) return false
  return /^wx[0-9a-z]{16}$/i.test(value)
}

export function wechatUatBlockers(input: {
  apiBaseUrl?: unknown
  mpAppId?: unknown
  wechatAppIdConfigured?: boolean
  wechatAppSecretConfigured?: boolean
  mpBindingSecretConfigured?: boolean
  subscribeTemplatesConfigured?: boolean
}): WechatUatBlocker[] {
  const api = isPublicHttpsApiBase(input.apiBaseUrl)
  return [
    {
      id: 'UAT-MP-DOMAIN',
      item: 'request 合法域名（HTTPS 备案域名）',
      status: api.ok ? 'ready' : 'blocked',
      reason: api.ok ? `已配置 ${api.origin}` : api.reason || '未配置合法域名',
    },
    {
      id: 'UAT-MP-APPID',
      item: '微信小程序 AppID',
      status: wechatAppIdReady(input.mpAppId) ? 'ready' : 'blocked',
      reason: wechatAppIdReady(input.mpAppId) ? 'manifest AppID 形态合法' : 'manifest 未写入真实 wx AppID',
    },
    {
      id: 'UAT-MP-SERVER',
      item: '服务端 code2session / msgSecCheck 凭据',
      status: input.wechatAppIdConfigured && input.wechatAppSecretConfigured && input.mpBindingSecretConfigured
        ? 'ready'
        : 'blocked',
      reason: 'PAPERGUARD_WECHAT_APP_ID / SECRET 与独立 MP_BINDING_SECRET 必须来自受控环境，源码不保存',
    },
    {
      id: 'UAT-MP-SUBSCRIBE',
      item: '订阅消息模板',
      status: input.subscribeTemplatesConfigured ? 'ready' : 'blocked',
      reason: input.subscribeTemplatesConfigured
        ? '模板映射已从环境注入'
        : '未配置 PAPERGUARD_WECHAT_SUBSCRIBE_TEMPLATES 或 VITE_SUBSCRIBE_TEMPLATE_IDS',
    },
    {
      id: 'UAT-MP-DEVICE',
      item: '微信开发者工具 / 真机预览',
      status: 'blocked',
      reason: '本仓库门禁不能代替真机点击；需在开发者工具勾选合法域名并走 AC-MP-01~03',
    },
  ]
}

export function wechatUatReady(blockers: WechatUatBlocker[]): boolean {
  return blockers.every((item) => item.status === 'ready')
}
