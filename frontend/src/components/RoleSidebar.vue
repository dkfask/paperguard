<template>
  <aside id="role-sidebar" class="role-sidebar" :class="{ open }" :aria-label="t('shell.openNavigation')">
    <div class="sidebar-context">
      <span class="sidebar-eyebrow">{{ t('shell.workspace') }}</span>
      <strong>{{ scopeName }}</strong>
      <span>{{ roleLabel(user?.role) }}</span>
    </div>

    <ScreenDeck v-model="activeGroup" class="sidebar-nav" :labels="groupLabels">
      <section v-for="group in groups" :key="group.label" class="sidebar-group">
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
      </section>
      <section v-if="managerTools.length" class="sidebar-tools">
        <p>{{ t('sidebar.managementTools') }}</p>
        <RouterLink v-for="tool in managerTools" :key="tool.label" :to="tool.to" class="sidebar-tool" :class="{ active: isActive(tool) }" :aria-current="isActive(tool) ? 'page' : undefined" @click="emit('close-mobile')">
          <span class="material-symbols-outlined" aria-hidden="true">{{ tool.icon }}</span>
          <span>{{ tool.label }}</span>
        </RouterLink>
      </section>
    </ScreenDeck>

    <div class="sidebar-footer">
      <span class="sidebar-avatar" aria-hidden="true">{{ userInitial }}</span>
      <div class="sidebar-user">
        <strong :title="user?.display_name || user?.username">{{ user?.display_name || user?.username }}</strong>
        <span>{{ roleLabel(user?.role) }}</span>
        <small>{{ t('sidebar.permissionsScope') }}</small>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import ScreenDeck from './ScreenDeck.vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../i18n'

const props = defineProps({
  user: { type: Object, default: null },
  open: { type: Boolean, default: false },
})

const emit = defineEmits(['close-mobile'])
const route = useRoute()
const { t } = useI18n()
const activeGroup = ref(0)

function roleLabel(role) {
  const key = `role.${role}`
  return t(key) === key ? t('role.user') : t(key)
}

const scopeName = computed(() => {
  const context = props.user?.organization_context || {}
  return context.college_name || context.school_name || t('shell.schoolWorkspace')
})
const userInitial = computed(() => (props.user?.display_name || props.user?.username || '?').trim().charAt(0).toUpperCase())

const groups = computed(() => {
  const role = props.user?.role
  if (role === 'student') {
    return [{ label: t('sidebar.myLearning'), items: [
      { label: t('sidebar.taskCenter'), icon: 'dashboard', to: '/workspace' },
      { label: t('sidebar.documentProjects'), icon: 'description', to: '/document-projects' },
      { label: t('sidebar.theses'), icon: 'edit_document', to: '/' },
      { label: t('sidebar.submissions'), icon: 'history', to: { path: '/workflow', query: { panel: 'submissions' } } },
      { label: t('sidebar.materials'), icon: 'folder_managed', to: { path: '/workflow', query: { panel: 'materials' } } },
      { label: t('sidebar.messages'), icon: 'notifications', to: { path: '/workflow', query: { panel: 'messages' } } },
    ] }]
  }
  if (role === 'teacher') {
    return [{ label: t('sidebar.studentGuidance'), items: [
      { label: t('sidebar.todoCenter'), icon: 'dashboard', to: '/workspace' },
      { label: t('sidebar.publishThesisTemplate'), icon: 'publish', to: '/templates' },
      { label: t('sidebar.review'), icon: 'rate_review', to: { path: '/workflow', query: { panel: 'review' } } },
      { label: t('sidebar.progress'), icon: 'groups', to: { path: '/workflow', query: { panel: 'progress' } } },
      { label: t('sidebar.teacherStudents'), icon: 'school', to: '/teacher/students' },
      { label: t('sidebar.materials'), icon: 'folder_managed', to: { path: '/workflow', query: { panel: 'materials' } } },
      { label: t('sidebar.policies'), icon: 'menu_book', to: '/policies' },
      { label: t('sidebar.messages'), icon: 'notifications', to: { path: '/workflow', query: { panel: 'messages' } } },
    ] }, { label: t('sidebar.personalWriting'), items: [
      { label: t('personalDocuments.title'), icon: 'description', to: '/document-projects' },
      { label: t('personalTemplates.title'), icon: 'settings', to: '/document-packages/publish' },
    ] }]
  }
  if (!['admin', 'college_admin'].includes(role)) return []
  const adminItems = [
    { label: t('sidebar.overview'), icon: 'dashboard', to: '/workspace' },
    { label: t('sidebar.workflow'), icon: 'assignment', to: '/workflow' },
    { label: t('route.cohorts'), icon: 'calendar_month', to: '/cohorts' },
    { label: t('workflow.reports'), icon: 'monitoring', to: { path: '/workflow', query: { panel: 'reports' } } },
    { label: t('sidebar.messages'), icon: 'notifications', to: { path: '/workflow', query: { panel: 'messages' } } },
  ]
  const resources = [
    { label: t('sidebar.members'), icon: 'groups', to: '/organizations' },
    { label: t('sidebar.templates'), icon: 'library_books', to: '/templates' },
    { label: t('sidebar.policies'), icon: 'menu_book', to: '/policies' },
    { label: t('sidebar.documentProjects'), icon: 'description', to: '/document-projects' },
    { label: t('sidebar.publishDocumentPackage'), icon: 'publish', to: '/document-packages/publish' },
    { label: t('sidebar.audit'), icon: 'fact_check', to: '/audit' },
    { label: t('workspace.archiveJobs'), icon: 'inventory_2', to: { path: '/workflow', query: { panel: 'archives' } } },
  ]
  if (role === 'admin') {
    resources.push({ label: t('sidebar.operations'), icon: 'settings', to: '/operations' })
  }
  return [
    { label: role === 'admin' ? t('sidebar.schoolOps') : t('sidebar.collegeOps'), items: adminItems },
    { label: t('sidebar.resourcesGovernance'), items: resources },
  ]
})

const managerTools = computed(() => {
  const role = props.user?.role
  if (role === 'admin') return [
    { label: t('sidebar.users'), icon: 'manage_accounts', to: '/admin/users' },
    { label: t('sidebar.ai'), icon: 'tune', to: '/admin/ai' },
  ]
  return []
})

const groupLabels = computed(() => [...groups.value.map(group => group.label), ...(managerTools.value.length ? [t('sidebar.managementTools')] : [])])
watch([() => route.fullPath, () => props.user?.role], () => {
  const group = groups.value.findIndex(group => group.items.some(isActive))
  if (group >= 0) activeGroup.value = group
  else if (managerTools.value.some(isActive)) activeGroup.value = groups.value.length
}, { immediate: true })

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

<style scoped>
.sidebar-avatar { width: 34px; height: 34px; flex: 0 0 auto; display: grid; place-items: center; border-radius: 50%; background: #dce6ff; color: #1d43a9; font-size: 13px; font-weight: 700; }
.sidebar-user { min-width: 0; display: grid; gap: 3px; }
.sidebar-user strong { color: #fff; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sidebar-user small { font-size: 10px; line-height: 1.5; }
</style>
