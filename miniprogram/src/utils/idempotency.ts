const STORAGE_PREFIX = 'pg.idem.'
const memoryKeys = new Map<string, string>()

function readStored(scope: string): string {
  try {
    return String(uni.getStorageSync(`${STORAGE_PREFIX}${scope}`) || '')
  } catch {
    return ''
  }
}

function writeStored(scope: string, value: string) {
  try {
    uni.setStorageSync(`${STORAGE_PREFIX}${scope}`, value)
  } catch {
    // 存储不可用时仍保留进程内键，保证同屏重试不换键。
  }
}

function clearStored(scope: string) {
  try {
    uni.removeStorageSync(`${STORAGE_PREFIX}${scope}`)
  } catch {
    // ignore
  }
}

/** 同一业务动作(如一次正式提交)在进程与本地存储内复用同一幂等键; 成功后用 renew 换新。 */
export function idempotencyKey(scope: string): string {
  const existing = memoryKeys.get(scope) || readStored(scope)
  if (existing) {
    memoryKeys.set(scope, existing)
    return existing
  }
  const key = `${scope}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
  memoryKeys.set(scope, key)
  writeStored(scope, key)
  return key
}

export function renewIdempotencyKey(scope: string): void {
  memoryKeys.delete(scope)
  clearStored(scope)
}
