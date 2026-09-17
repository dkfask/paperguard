export function parseSubscribeTemplateIds(raw: unknown): string[] {
  return String(raw ?? '')
    .split(',')
    .map((id) => id.trim())
    .filter(Boolean)
}

export function reportsFromSubscribeResult(
  templateIds: string[],
  result: Record<string, string>,
): Array<{ templateId: string; accepted: boolean }> {
  return templateIds.map((templateId) => ({
    templateId,
    accepted: result[templateId] === 'accept',
  }))
}

export function configuredSubscribeTemplateIds(): string[] {
  return parseSubscribeTemplateIds(import.meta.env.VITE_SUBSCRIBE_TEMPLATE_IDS)
}

/** 必须由用户点击触发。silent 用于提交确认手势, 未配置模板时不打断提交。 */
export function requestSubscribeAuthorization(options?: { silent?: boolean }): Promise<void> {
  const templateIds = configuredSubscribeTemplateIds()
  if (!templateIds.length) {
    if (!options?.silent) {
      uni.showToast({ title: '提醒模板尚未配置，上线后开放', icon: 'none' })
    }
    return Promise.resolve()
  }
  return new Promise((resolve) => {
    uni.requestSubscribeMessage({
      tmplIds: templateIds,
      complete: () => {
        if (!options?.silent) {
          uni.showToast({ title: '已按微信授权结果处理', icon: 'none' })
        }
        resolve()
      },
      fail: () => resolve(),
      success: (result) => {
        void import('../api/request').then(({ reportSubscribe }) => {
          for (const report of reportsFromSubscribeResult(templateIds, result as unknown as Record<string, string>)) {
            void reportSubscribe(report.templateId, report.accepted).catch(() => {})
          }
        }).catch(() => {})
      },
    })
  })
}
