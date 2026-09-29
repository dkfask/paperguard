<template>
  <div
    v-if="navigationFailure"
    class="nav-failure"
    role="alert"
  >
    <span class="material-symbols-outlined nav-failure-icon" aria-hidden="true">cloud_off</span>
    <div class="nav-failure-body">
      <strong>{{ t('navError.title') }}</strong>
      <span>{{ navigationFailure.chunkLoadFailed ? t('navError.chunkLoad') : t('navError.generic') }}</span>
      <code v-if="navigationFailure.target" class="nav-failure-target">{{ navigationFailure.target }}</code>
    </div>
    <div class="nav-failure-actions">
      <button class="primary" type="button" @click="retryNavigation">{{ t('navError.retry') }}</button>
      <button type="button" @click="clearNavigationFailure">{{ t('navError.dismiss') }}</button>
    </div>
  </div>

  <div v-if="authenticated" class="app-shell-root" @keydown.esc="closeSidebar">
    <header class="app-shell-header">
      <div class="header-inner">
        <div class="brand-section">
          <button
            ref="menuButtonRef"
            class="shell-menu-button"
            type="button"
            :aria-label="t('shell.openNavigation')"
            :title="t('shell.openNavigation')"
            aria-controls="role-sidebar"
            :aria-expanded="sidebarOpen"
            @click="toggleSidebar"
          >
            <IconMaterialSymbolsMenu />
          </button>
          <RouterLink to="/" class="brand-logo">
            <span class="brand-title">PaperGuard</span>
          </RouterLink>
        </div>

        <div class="user-tray">
          <span class="scope-context">{{ roleLabel(user?.role) }}</span>
          <button class="lang-switch-btn" type="button" :title="t('shell.switchLanguage')" @click="toggleLang">
            <IconMaterialSymbolsTranslate />
            <span>{{ lang === 'zh' ? t('shell.switchToEnglish') : t('shell.switchToChinese') }}</span>
          </button>
          <div
            class="user-avatar-btn"
            :title="user?.display_name || user?.username"
            :aria-label="t('shell.userProfile')"
            role="img"
          >
            <span class="user-avatar-initial">{{ userInitial }}</span>
          </div>
          <span class="shell-user-name" :title="user?.display_name || user?.username">
            {{ user?.display_name || user?.username }}
            <span v-if="isPending" class="shell-pending-label">({{ t('pendingAuth') }})</span>
          </span>
          <button class="small shell-logout" type="button" @click="$emit('logout')">{{ t('logout') }}</button>
        </div>
      </div>
    </header>

    <div v-if="isPending" class="pending-bar">
      <span>{{ t('shell.pendingTeacher') }}</span>
    </div>

    <div class="app-shell">
      <div
        v-if="sidebarOpen"
        class="sidebar-backdrop"
        aria-hidden="true"
        @click="closeSidebar"
      />
      <RoleSidebar
        :user="user"
        :open="sidebarOpen"
        @close-mobile="closeSidebar"
      />
      <main class="app-shell-main" @click="closeSidebar">
        <nav v-if="route.path !== '/'" class="app-breadcrumbs" :aria-label="t('common.pageLocation')">
          <RouterLink to="/">{{ t('shell.home') }}</RouterLink>
          <span aria-hidden="true">/</span>
          <span>{{ routeTitle }}</span>
        </nav>
        <slot />
      </main>
    </div>
  </div>
  <main v-else class="public-shell">
    <slot />
  </main>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../i18n'
import RoleSidebar from '../components/RoleSidebar.vue'
import IconMaterialSymbolsMenu from '~icons/material-symbols/menu'
import IconMaterialSymbolsTranslate from '~icons/material-symbols/translate'
import { clearNavigationFailure, navigationFailure } from '../shared/navigationFailure'

const props = defineProps({
  authenticated: { type: Boolean, default: false },
  user: { type: Object, default: null },
})

defineEmits(['logout'])

const sidebarOpen = ref(false)
const menuButtonRef = ref(null)
const route = useRoute()
const { lang, setLang, t } = useI18n()

const routeTitle = computed(() => route.meta.titleKey ? t(route.meta.titleKey) : (route.meta.title || t('shell.currentPage')))

const isPending = computed(() => props.user?.role === 'teacher' && props.user?.status === 'pending')

// avatar.png 是一张 512×512 的整版品牌图，缩到 28px 圆头像里完全不可辨认，
// 所以直接改用姓名首字母，不再显示那张图。
const userInitial = computed(() => {
  const source = props.user?.display_name || props.user?.username || ''
  return source.trim().charAt(0).toUpperCase() || '?'
})

function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value
}

function closeSidebar() {
  if (!sidebarOpen.value) return
  sidebarOpen.value = false
}

// 抽屉打开后把焦点移进侧栏，关闭后归还给汉堡按钮：
// 否则键盘用户 Tab 的仍是抽屉背后的主区域，看不到自己在哪。
function focusFirstSidebarLink() {
  const sidebar = document.getElementById('role-sidebar')
  if (!sidebar) return
  const target = sidebar.querySelector('a, button')
  if (target) target.focus()
}

function trapSidebarFocus(event) {
  if (event.key !== 'Tab' || !sidebarOpen.value) return
  const sidebar = document.getElementById('role-sidebar')
  if (!sidebar) return
  const focusable = Array.from(
    sidebar.querySelectorAll('a[href], button:not([disabled])'),
  ).filter((el) => el.offsetParent !== null)
  if (!focusable.length) return

  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function retryNavigation() {
  const target = navigationFailure.value?.target
  clearNavigationFailure()
  // 必须整页跳转：失败的 chunk 会被浏览器缓存，router.push 只会复用失败记录、
  // 不重新请求，结果是点了重试依然没反应。
  const here = `${window.location.pathname}${window.location.search}`
  if (target && target !== here) window.location.assign(target)
  else window.location.reload()
}

function toggleLang() {
  setLang(lang.value === 'zh' ? 'en' : 'zh')
}

function roleLabel(role) {
  return t(`role.${role}`) === `role.${role}` ? t('role.user') : t(`role.${role}`)
}

watch(sidebarOpen, async (open) => {
  if (open) {
    await nextTick()
    focusFirstSidebarLink()
  } else {
    await nextTick()
    menuButtonRef.value?.focus()
  }
})

// 视口变宽到常驻侧栏布局时，抽屉状态必须复位，否则遮罩会留在屏幕上。
watch(() => route.path, closeSidebar)

onMounted(() => window.addEventListener('keydown', trapSidebarFocus))
onBeforeUnmount(() => window.removeEventListener('keydown', trapSidebarFocus))

watchEffect(() => {
  document.title = `${routeTitle.value} · PaperGuard`
})
</script>

<style scoped>
.app-shell-root { min-height: 100vh; }
.shell-user-name {
  color: var(--on-surface);
  font-size: 13px;
  font-weight: 600;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.shell-pending-label { color: var(--tertiary); font-size: 11px; margin-left: 4px; }
.public-shell { min-height: 100vh; max-width: none; padding: 0; }
.app-breadcrumbs { display: flex; align-items: center; gap: 7px; min-height: 34px; padding-top: 10px; color: var(--outline); font-size: 11px; }
.app-breadcrumbs a { color: var(--primary); text-decoration: none; }
.app-breadcrumbs a:hover { text-decoration: underline; }

/* 顶栏元素不允许被挤成竖排：角色徽章与退出按钮一旦折行，
   在窄屏上会读成「学/生」「退出登/录」这种断开的词。 */
.user-tray :deep(.scope-context),
.shell-logout {
  white-space: nowrap;
  flex: 0 0 auto;
}

.user-avatar-initial {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  font-size: 13px;
  font-weight: 700;
  color: var(--primary);
  background: var(--surface-container-high);
  text-transform: uppercase;
}

/* 移动端抽屉遮罩：此前没有遮罩，抽屉盖住 1/3 屏宽而主内容
   仍完全可见、看起来可点，存在误触风险。 */
.sidebar-backdrop {
  position: fixed;
  inset: 60px 0 0 0;
  z-index: 55;
  background: rgba(15, 23, 42, .32);
}

.nav-failure {
  position: sticky;
  top: 0;
  z-index: 200;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 16px;
  background: #fef3c7;
  border-bottom: 1px solid #fcd34d;
  color: #78350f;
}
.nav-failure-icon { font-size: 18px; color: #b45309; }
.nav-failure-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
  font-size: 13px;
}
.nav-failure-body strong { font-size: 13px; }
.nav-failure-target {
  font-size: 11px;
  color: #92400e;
  word-break: break-all;
}
.nav-failure-actions {
  display: flex;
  gap: 8px;
  flex: 0 0 auto;
}
</style>
