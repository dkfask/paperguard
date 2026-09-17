/** 消息 Tab 未读角标。index 与 pages.json tabBar.list 对齐。 */

export const MESSAGE_TAB_INDEX = 1

export interface TabBarHost {
  setTabBarBadge?: (options: { index: number; text: string }) => void
  removeTabBarBadge?: (options: { index: number }) => void
}

export function tabBarBadgeText(unread: number): string | null {
  if (!Number.isFinite(unread) || unread <= 0) return null
  const count = Math.floor(unread)
  if (count > 99) return '99+'
  return String(count)
}

export function applyMessageTabBadge(unread: number, host: TabBarHost = uni): void {
  const text = tabBarBadgeText(unread)
  if (!text) {
    host.removeTabBarBadge?.({ index: MESSAGE_TAB_INDEX })
    return
  }
  host.setTabBarBadge?.({ index: MESSAGE_TAB_INDEX, text })
}
