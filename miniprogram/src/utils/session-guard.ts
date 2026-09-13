import { useSessionStore } from '../stores/session'

/** 页面 onShow 统一守卫: 未登录回绑定页; 初始密码账号强制先改密(设计方案 §4.2)。 */
export function ensureSessionReady(): boolean {
  const session = useSessionStore()
  if (!session.isAuthenticated) {
    uni.reLaunch({ url: '/pages/login/index' })
    return false
  }
  if (session.user?.password_set === false && !session.passwordHintShown) {
    session.markPasswordHintShown()
    uni.showModal({
      title: '绑定邮箱并设置密码',
      content: '绑定后可在电脑端用「邮箱 + 密码」登录，并用于账号找回。',
      confirmText: '去绑定',
      cancelText: '暂不',
      success: (result) => {
        if (result.confirm) uni.navigateTo({ url: '/pages/profile/bind-email' })
      },
    })
  }
  if (session.user?.must_change_password) {
    if (!session.passwordNoticeShown) {
      session.markPasswordNoticeShown()
      uni.showModal({
        title: '请先修改初始密码',
        content: '检测到账号仍在使用初始密码，修改后将重新登录。',
        confirmText: '去修改',
        showCancel: false,
        success: () => uni.switchTab({ url: '/pages/profile/index' }),
      })
    }
    return false
  }
  return true
}
