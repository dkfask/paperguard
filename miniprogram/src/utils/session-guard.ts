import { useSessionStore } from '../stores/session'
import { isPrivacyAccepted } from './privacy'

/** 页面 onShow 统一守卫: 未登录回绑定页; 初始密码账号强制先改密(设计方案 §4.2)。 */
export function ensureSessionReady(): boolean {
  if (!isPrivacyAccepted()) {
    uni.reLaunch({ url: '/pages/login/index' })
    return false
  }
  const session = useSessionStore()
  if (!session.isAuthenticated) {
    uni.reLaunch({ url: '/pages/login/index' })
    return false
  }
  const needsOnboarding = session.user?.onboarding_required
    || session.user?.email_verified === false
    || session.user?.password_set === false
    || session.user?.must_change_password
  if (needsOnboarding) {
    uni.navigateTo({ url: '/pages/profile/bind-email?first=1' })
    return false
  }
  return true
}
