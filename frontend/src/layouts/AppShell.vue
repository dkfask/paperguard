<template>
  <div v-if="authenticated" class="app-shell-root">
    <header class="app-shell-header">
      <div class="header-inner">
        <div class="brand-section">
          <button
            class="shell-menu-button"
            type="button"
            :aria-label="t('shell.openNavigation')"
            :title="t('shell.openNavigation')"
            aria-controls="role-sidebar"
            :aria-expanded="sidebarOpen"
            @click="sidebarOpen = !sidebarOpen"
          >
            <IconMaterialSymbolsMenu />
          </button>
          <RouterLink to="/" class="brand-logo">
            <img alt="PaperGuard Logo" src="/logo.png" />
            <span class="brand-title">PaperGuard</span>
          </RouterLink>
        </div>

        <div class="user-tray">
          <span class="scope-context">{{ roleLabel(user?.role) }}</span>
          <button class="lang-switch-btn" type="button" :title="t('shell.switchLanguage')" @click="toggleLang">
            <IconMaterialSymbolsTranslate />
            <span>{{ lang === 'zh' ? t('shell.switchToEnglish') : t('shell.switchToChinese') }}</span>
          </button>
          <div class="user-avatar-btn" :title="user?.display_name || user?.username">
            <img :alt="t('shell.userProfile')" src="/avatar.png" />
          </div>
          <span class="shell-user-name">
            {{ user?.display_name || user?.username }}
            <span v-if="isPending" class="shell-pending-label">({{ t('pendingAuth') }})</span>
          </span>
          <button class="small" type="button" @click="$emit('logout')">{{ t('logout') }}</button>
        </div>
      </div>
    </header>

    <div v-if="isPending" class="pending-bar">
      <span>{{ t('shell.pendingTeacher') }}</span>
    </div>

    <div class="app-shell">
      <RoleSidebar :user="user" :open="sidebarOpen" @close-mobile="sidebarOpen = false" />
      <main class="app-shell-main" @click="sidebarOpen = false">
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
import { computed, ref, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../i18n'
import RoleSidebar from '../components/RoleSidebar.vue'
import IconMaterialSymbolsMenu from '~icons/material-symbols/menu'
import IconMaterialSymbolsTranslate from '~icons/material-symbols/translate'

const props = defineProps({
  authenticated: { type: Boolean, default: false },
  user: { type: Object, default: null },
})

defineEmits(['logout'])

const sidebarOpen = ref(false)
const route = useRoute()
const { lang, setLang, t } = useI18n()

const routeTitle = computed(() => route.meta.titleKey ? t(route.meta.titleKey) : (route.meta.title || t('shell.currentPage')))

const isPending = computed(() => props.user?.role === 'teacher' && props.user?.status === 'pending')

function toggleLang() {
  setLang(lang.value === 'zh' ? 'en' : 'zh')
}

function roleLabel(role) {
  return t(`role.${role}`) === `role.${role}` ? t('role.user') : t(`role.${role}`)
}

watchEffect(() => {
  document.title = `${routeTitle.value} · PaperGuard`
})
</script>

<style scoped>
.app-shell-root { min-height: 100vh; }
.shell-user-name { color: var(--on-surface); font-size: 13px; font-weight: 600; }
.shell-pending-label { color: var(--tertiary); font-size: 11px; margin-left: 4px; }
.public-shell { min-height: 100vh; max-width: none; padding: 0; }
.app-breadcrumbs { display: flex; align-items: center; gap: 7px; min-height: 34px; padding-top: 10px; color: var(--outline); font-size: 11px; }
.app-breadcrumbs a { color: var(--primary); text-decoration: none; }
.app-breadcrumbs a:hover { text-decoration: underline; }
</style>
