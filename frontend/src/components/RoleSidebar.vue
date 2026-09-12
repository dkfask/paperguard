<template>
  <aside id="role-sidebar" class="role-sidebar" :class="{ open }" :aria-label="t('shell.openNavigation')">
    <div class="sidebar-context">
      <span class="sidebar-eyebrow">{{ t('shell.workspace') }}</span>
      <strong>{{ scopeName }}</strong>
      <span>{{ roleLabel(user?.role) }}</span>
    </div>

    <nav class="sidebar-nav">
      <div v-for="group in groups" :key="group.label" class="sidebar-group">
        <p>{{ group.label }}</p>
        <RouterLink
          v-for="item in group.items"
          :key="item.label"
          :to="item.to"
          class="sidebar-link"
          :class="{ active: isActive(item) }"
          :aria-current="isActive(item) ? 'page' : undefined"
          @click="emit('close-mobile')"
        >
          <span class="material-symbols-outlined" aria-hidden="true">{{ item.icon }}</span>
          <span>{{ item.label }}</span>
        </RouterLink>
      </div>
    </nav>

    <div v-if="managerTools.length" class="sidebar-tools">
      <p>{{ t('sidebar.managementTools') }}</p>
      <RouterLink v-for="tool in managerTools" :key="tool.label" :to="tool.to" class="sidebar-tool" :class="{ active: isActive(tool) }" :aria-current="isActive(tool) ? 'page' : undefined" @click="emit('close-mobile')">
        <span class="material-symbols-outlined" aria-hidden="true">{{ tool.icon }}</span>
        <span>{{ tool.label }}</span>
      </RouterLink>
    </div>

    <div class="sidebar-footer">
      <span class="material-symbols-outlined" aria-hidden="true">verified_user</span>
      <span>{{ t('sidebar.permissionsScope') }}</span>
    </div>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../i18n'

const props = defineProps({
  user: { type: Object, default: null },
  open: { type: Boolean, default: false },
})

const emit = defineEmits(['close-mobile'])
const route = useRoute()
const { t } = useI18n()

function roleLabel(role) {
  const key = `role.${role}`
  return t(key) === key ? t('role.user') : t(key)
}

const scopeName = computed(() => {
  const context = props.user?.organization_context || {}
  return context.college_name || context.school_name || t('shell.schoolWorkspace')
})

const groups = computed(() => {
  const role = props.user?.role
  if (role === 'student') {
    return [{ label: t('sidebar.myLearning'), items: [
      { label: t('sidebar.dashboard'), icon: 'dashboard', to: '/workspace' },
      { label: t('sidebar.theses'), icon: 'edit_document', to: '/' },
      { label: t('sidebar.submissions'), icon: 'history', to: { path: '/workflow', query: { panel: 'submissions' } } },
      { label: t('sidebar.materials'), icon: 'folder_managed', to: { path: '/workflow', query: { panel: 'materials' } } },
      { label: t('sidebar.messages'), icon: 'notifications', to: { path: '/workflow', query: { panel: 'messages' } } },
    ] }]
  }
  if (role === 'teacher') {
    return [{ label: t('sidebar.guidance'), items: [
      { label: t('sidebar.dashboard'), icon: 'dashboard', to: '/workspace' },
      { label: t('sidebar.review'), icon: 'rate_review', to: { path: '/workflow', query: { panel: 'review' } } },
      { label: t('sidebar.progress'), icon: 'groups', to: { path: '/workflow', query: { panel: 'progress' } } },
      { label: t('sidebar.materials'), icon: 'folder_managed', to: { path: '/workflow', query: { panel: 'materials' } } },
      { label: t('sidebar.policies'), icon: 'menu_book', to: '/policies' },
      { label: t('sidebar.messages'), icon: 'notifications', to: { path: '/workflow', query: { panel: 'messages' } } },
    ] }]
  }
  const adminItems = [
    { label: t('sidebar.overview'), icon: 'dashboard', to: '/workspace' },
    { label: t('sidebar.workflow'), icon: 'assignment', to: '/workflow' },
    { label: t('sidebar.members'), icon: 'groups', to: '/organizations' },
    { label: t('sidebar.templates'), icon: 'library_books', to: '/templates' },
    { label: t('sidebar.audit'), icon: 'fact_check', to: '/audit' },
    { label: t('sidebar.messages'), icon: 'notifications', to: { path: '/workflow', query: { panel: 'messages' } } },
  ]
  if (role === 'admin') {
    adminItems.push({ label: t('sidebar.operations'), icon: 'settings', to: '/operations' })
  }
  return [{ label: role === 'admin' ? t('sidebar.schoolOps') : t('sidebar.collegeOps'), items: adminItems }]
})

const managerTools = computed(() => {
  const role = props.user?.role
  if (role === 'admin') return [
    { label: t('sidebar.users'), icon: 'manage_accounts', to: '/admin/users' },
    { label: t('sidebar.ai'), icon: 'tune', to: '/admin/ai' },
  ]
  if (role === 'teacher') return [
    { label: t('sidebar.teacherStudents'), icon: 'school', to: '/teacher/students' },
  ]
  return []
})

function isActive(item) {
  const target = typeof item.to === 'string' ? item.to : item.to.path
  if (target === '/') return route.path === '/' && !route.query.tab
  if (target === '/workflow') {
    const panel = typeof item.to === 'string' ? '' : item.to.query?.panel
    return route.path === '/workflow' && (panel ? route.query.panel === panel : !route.query.panel)
  }
  if (target === '/workspace') return route.path === '/workspace'
  return route.path === target || route.path.startsWith(`${target}/`)
}
</script>
