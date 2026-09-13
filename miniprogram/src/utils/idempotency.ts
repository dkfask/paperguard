const keys = new Map<string, string>()

/** 同一业务动作(如一次正式提交)在进程内的重试复用同一幂等键; 成功后用 renew 换新。 */
export function idempotencyKey(scope: string): string {
  const existing = keys.get(scope)
  if (existing) return existing
  const key = `${scope}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
  keys.set(scope, key)
  return key
}

export function renewIdempotencyKey(scope: string): void {
  keys.delete(scope)
}
