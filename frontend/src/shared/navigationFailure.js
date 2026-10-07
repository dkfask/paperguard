import { ref } from 'vue'

// Vue Router 在懒加载组件（`() => import(...)`）的 chunk 拉取失败时会 reject 导航
// promise 并转交 `router.onError`，此时路由不会提交：URL、页面内容都停在原处。
// 如果不接住这个错误，用户点完导航入口后界面毫无反应，唯一痕迹是
// `document.title` 已经被守卫提前改掉，与实际页面不符。
//
// 这里用一个模块级 ref 把失败暴露给 UI（AppShell 渲染可见横幅），
// 让导航失败至少是可感知、可恢复的。
export const navigationFailure = ref(null)

// 失败的动态 import 会被浏览器缓存，因此重试不能只走 `router.push`
// （那样会直接复用失败记录、不重新请求）。必须整页跳转到目标地址。
export function reportNavigationFailure(error, to) {
  navigationFailure.value = {
    message: error?.message || String(error),
    // 动态 import 的 chunk 拉取失败
    chunkLoadFailed: /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed/i
      .test(error?.message || ''),
    target: to?.fullPath || null,
  }
}

export function clearNavigationFailure() {
  navigationFailure.value = null
}
