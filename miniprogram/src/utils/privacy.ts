/** 设计方案 §9：首启隐私协议与分享边界。只存同意版本，不存账号或密钥。 */

export const PRIVACY_ACCEPT_KEY = 'pg_mp_privacy_accepted_v1'
export const PRIVACY_VERSION = '2026-09-16'

export const PRIVACY_COLLECTION_SCOPE = [
  '登录凭证（微信登录换取的会话令牌）',
  '账号绑定关系（微信身份与校内账号）',
  '消息推送标识（订阅消息授权结果）',
] as const

export const PRIVACY_NOT_COLLECTED = ['手机号', '地理位置'] as const

export const HOME_SHARE_PATH = '/pages/tasks/index'

const SENSITIVE_SHARE_PREFIXES = [
  '/pages/submissions/',
  'pages/submissions/',
  '/pages/review/',
  'pages/review/',
  '/pages/submit/',
  'pages/submit/',
  '/pages/requirements/',
  'pages/requirements/',
]

export interface PrivacyStorage {
  getStorageSync?(key: string): unknown
  setStorageSync?(key: string, value: unknown): void
}

export function privacyNoticeTitle(): string {
  return '论文卫士Pro 隐私说明'
}

export function privacyNoticeSummary(): string {
  return [
    '本小程序只处理论文过程管理所需的最少信息。',
    `会处理：${PRIVACY_COLLECTION_SCOPE.join('、')}。`,
    `不会采集：${PRIVACY_NOT_COLLECTED.join('、')}。`,
    '提交详情、论文正文与审阅意见不允许转发分享，仅可分享小程序首页。',
  ].join('')
}

function readAcceptedVersion(storage?: PrivacyStorage): string {
  try {
    const raw = storage?.getStorageSync
      ? storage.getStorageSync(PRIVACY_ACCEPT_KEY)
      : uni.getStorageSync(PRIVACY_ACCEPT_KEY)
    if (!raw) return ''
    if (typeof raw === 'string') return raw
    if (typeof raw === 'object' && raw && 'version' in raw) {
      return String((raw as { version?: unknown }).version || '')
    }
    return ''
  } catch {
    return ''
  }
}

export function isPrivacyAccepted(storage?: PrivacyStorage): boolean {
  return readAcceptedVersion(storage) === PRIVACY_VERSION
}

export function acceptPrivacy(storage?: PrivacyStorage, now = () => new Date().toISOString()): void {
  const payload = { version: PRIVACY_VERSION, accepted_at: now() }
  if (storage?.setStorageSync) {
    storage.setStorageSync(PRIVACY_ACCEPT_KEY, payload)
    return
  }
  uni.setStorageSync(PRIVACY_ACCEPT_KEY, payload)
}

export function normalizeRoute(route: string): string {
  const path = String(route || '').split('?')[0].trim()
  if (!path) return ''
  return path.startsWith('/') ? path : `/${path}`
}

export function isSensitiveShareRoute(route: string): boolean {
  const path = normalizeRoute(route)
  return SENSITIVE_SHARE_PREFIXES.some((prefix) => {
    const normalized = prefix.startsWith('/') ? prefix : `/${prefix}`
    return path.startsWith(normalized)
  })
}

/** 设计方案：仅允许分享小程序首页（任务 Tab）。 */
export function canShareAppMessage(route: string): boolean {
  return normalizeRoute(route) === HOME_SHARE_PATH
}

export function homeSharePayload(): { title: string; path: string } {
  return { title: '论文卫士Pro', path: HOME_SHARE_PATH }
}

type ShareMenuHost = {
  hideShareMenu?: (options?: Record<string, unknown>) => void
  showShareMenu?: (options?: Record<string, unknown>) => void
}

export function applySharePolicy(route: string, host: ShareMenuHost = uni): void {
  if (canShareAppMessage(route)) {
    host.showShareMenu?.({ withShareTicket: false, menus: ['shareAppMessage'] })
    return
  }
  host.hideShareMenu?.({ hideShareItems: ['shareAppMessage', 'shareTimeline'] })
}
