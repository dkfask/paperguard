export const ERROR_MESSAGES: Record<string, string> = {
  MP_NOT_CONFIGURED: '小程序登录尚未配置，请联系管理员',
  MP_LOGIN_FAILED: '微信登录凭证无效，请重试',
  MP_CODE_REQUIRED: '微信登录凭证缺失，请重试',
  MP_BIND_TICKET_REQUIRED: '绑定会话已过期，请重新获取微信凭证',
  ACCOUNT_UNAVAILABLE: '账号当前不可使用，请联系管理员',
  CHECK_PENDING: '格式检查仍在进行，请稍后重试',
  CHECK_FAILED: '格式检查失败，请稍后重试或联系管理员',
  CHECK_STALE: '正文已在电脑端更新，请重新预检',
  BLOCKING_FINDINGS: '存在未关闭的阻断项，暂不能提交',
  DEADLINE_PASSED: '已超过有效截止时间，如需补交请联系教师或管理员',
  SUBMISSION_ALREADY_PENDING: '已存在待审核的提交，请勿重复提交',
  WITHDRAW_NOT_PENDING: '当前提交已处理，不能撤回',
  REVISION_CONFLICT: '内容已在他端修改，请在电脑端处理冲突',
  CONTENT_SECURITY_REJECTED: '文本未通过内容安全检查，请修改后再提交',
  CONTENT_SECURITY_UNAVAILABLE: '内容安全检查暂时不可用，请稍后重试',
}

export function describeApiError(error: unknown): string {
  if (error && typeof error === 'object' && 'status' in error) {
    const coded = error as { status: number; code?: string; message?: string }
    if (coded.code && ERROR_MESSAGES[coded.code]) return ERROR_MESSAGES[coded.code]
    if (coded.status === 401) return '登录状态已过期，请重新登录'
    if (coded.status === 403) return '没有执行该操作的权限'
    if (coded.status === 404) return '资源不存在或无权访问'
    if (coded.status === 409) return '状态已变化，请刷新后重试'
    if (coded.status === 429 || coded.status === 503) return '服务繁忙，请稍后重试'
    return coded.message || '请求失败，请稍后重试'
  }
  return error instanceof Error ? error.message : '请求失败，请稍后重试'
}
