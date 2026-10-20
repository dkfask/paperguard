<template>
  <div class="workspace-page admin-users-page">
    <section class="workspace-hero"><div><p class="workspace-kicker">{{ t('adminUsers.kicker') }}</p><h1>{{ t('adminUsers.title') }}</h1><p>{{ t('adminUsers.description') }}</p></div><div class="workspace-identity"><span class="material-symbols-outlined" aria-hidden="true">manage_accounts</span><div><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><span>{{ t('role.admin') }}</span></div></div></section>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div><div v-if="notice" class="ok-banner" role="status">{{ notice }}</div>
    <AsyncState v-if="loading" state="loading" :title="t('adminUsers.loading')" :detail="t('adminUsers.loadingDetail')" />
    <template v-else>
      <section class="workspace-stats" :aria-label="t('adminUsers.summary')"><div><span>{{ t('adminUsers.totalUsers') }}</span><strong>{{ users.length }}</strong><small>{{ t('adminUsers.schoolScope') }}</small></div><div><span>{{ t('adminUsers.pendingTeachers') }}</span><strong>{{ pendingTeachers }}</strong><small>{{ t('adminUsers.needsReview') }}</small></div><div><span>{{ t('adminUsers.pendingApplications') }}</span><strong>{{ pendingCertifications }}</strong><small>{{ t('adminUsers.identityPending') }}</small></div><div><span>{{ t('adminUsers.activeTeachers') }}</span><strong>{{ activeTeachers }}</strong><small>{{ t('adminUsers.canGuide') }}</small></div><div><span>{{ t('adminUsers.administrators') }}</span><strong>{{ administrators }}</strong><small>{{ t('adminUsers.managementRoles') }}</small></div></section>
      <section class="panel"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('adminUsers.directoryKicker') }}</p><h2>{{ t('adminUsers.directory') }}</h2><p>{{ t('adminUsers.directoryDetail') }}</p></div><button class="icon-button" type="button" :title="t('adminUsers.refresh')" :aria-label="t('adminUsers.refresh')" :disabled="loading" @click="load"><span class="material-symbols-outlined" aria-hidden="true">refresh</span></button></div>
        <AsyncState v-if="!users.length" state="empty" :title="t('adminUsers.empty')" :detail="t('adminUsers.emptyDetail')" />
        <ScreenCollection :items="users" :row-height="64" row-selector="tbody tr" body-class="table-scroll" v-slot="{ items: visibleUsers }"><table class="directory-table"><thead><tr><th>{{ t('adminUsers.username') }}</th><th>{{ t('adminUsers.name') }}</th><th>{{ t('adminUsers.role') }}</th><th>{{ t('adminUsers.status') }}</th><th>{{ t('adminUsers.actions') }}</th></tr></thead><tbody><tr v-for="user in visibleUsers" :key="user.id"><td><strong>{{ user.username }}</strong><small v-if="user.username === 'admin'">{{ t('adminUsers.builtinAdmin') }}</small></td><td>{{ user.display_name || '-' }}</td><td><span class="role-label" :class="user.role">{{ roleLabel(user.role) }}</span><small v-if="user.role_application_status === 'pending'">{{ t('adminUsers.applicationPrefix') }}{{ roleLabel(user.requested_role) }}</small></td><td><span class="status-label" :class="user.role_application_status === 'pending' ? 'pending' : user.status">{{ statusLabel(user) }}</span></td><td class="directory-actions"><button class="tiny" type="button" @click="selectedUser = user">{{ t('screen.details') }}</button></td></tr></tbody></table></ScreenCollection><BaseModal size="small" :open="Boolean(selectedUser)" :title="selectedUser?.display_name || selectedUser?.username" @close="selectedUser = null"><section v-if="selectedUser" class="screen-form-section"><p>{{ roleLabel(selectedUser.role) }} · {{ statusLabel(selectedUser) }}</p><template v-for="user in [selectedUser]" :key="user.id"><div class="directory-actions drawer-actions"><template v-if="user.username !== 'admin'"><button v-if="user.role === 'teacher' && user.status === 'pending'" class="tiny primary" type="button" @click="certifyTeacher(user, 'active')">{{ t('adminUsers.approve') }}</button><button v-if="user.role === 'teacher' && user.status === 'pending'" class="tiny danger" type="button" @click="certifyTeacher(user, 'rejected')">{{ t('adminUsers.reject') }}</button><button v-if="user.role_application_status === 'pending'" class="tiny primary" type="button" @click="decideCertification(user, 'approve')">{{ t('adminUsers.approveApplication') }}</button><button v-if="user.role_application_status === 'pending'" class="tiny danger" type="button" @click="decideCertification(user, 'reject')">{{ t('adminUsers.rejectApplication') }}</button><button v-if="user.role !== 'admin'" class="tiny" type="button" @click="changeRole(user, 'admin', 'active')">{{ t('adminUsers.makeAdmin') }}</button><button v-if="user.role !== 'teacher'" class="tiny" type="button" @click="changeRole(user, 'teacher', 'active')">{{ t('adminUsers.makeTeacher') }}</button><button v-if="user.role !== 'student'" class="tiny" type="button" @click="changeRole(user, 'student', 'active')">{{ t('adminUsers.makeStudent') }}</button></template><span v-else class="muted-row">{{ t('adminUsers.builtin') }}</span></div></template></section></BaseModal>
      </section>
    </template>
  </div>
</template>

<script setup>
import BaseModal from '../components/base/BaseModal.vue'
import ScreenCollection from '../components/ScreenCollection.vue'
import { computed, onMounted, ref } from 'vue'
import { auth } from '../auth'
import { getAdminUsers, updateAdminUserRoleStatus } from '../api'
import AsyncState from '../components/AsyncState.vue'
import { useI18n } from '../i18n'
import { authApi } from '../features/auth/api'

const { t } = useI18n()
const selectedUser = ref(null)
const users = ref([]); const loading = ref(true); const error = ref(''); const notice = ref('')
const pendingTeachers = computed(() => users.value.filter((user) => user.role === 'teacher' && user.status === 'pending').length)
const pendingCertifications = computed(() => users.value.filter((user) => user.role_application_status === 'pending').length)
const activeTeachers = computed(() => users.value.filter((user) => user.role === 'teacher' && user.status === 'active').length)
const administrators = computed(() => users.value.filter((user) => user.role === 'admin').length)
async function load() {
  loading.value = true; error.value = ''
  try {
    users.value = await getAdminUsers()
    if (selectedUser.value) selectedUser.value = users.value.find(user => user.id === selectedUser.value.id) || null
  } catch (exception) { error.value = exception.message } finally { loading.value = false }
}
async function changeRole(user, role, status) { error.value = ''; try { await updateAdminUserRoleStatus(user.id, { role, status }); notice.value = t('adminUsers.updated', { name: user.display_name || user.username }); await load() } catch (exception) { error.value = exception.message } }
// 教师认证（账号状态）与身份申请审批（role-certification 流程）是两个正交状态，
// 必须走各自的端点：前者改 status，后者改 role_application_status。
async function certifyTeacher(user, status) { error.value = ''; try { await updateAdminUserRoleStatus(user.id, { role: 'teacher', status }); notice.value = status === 'active' ? t('adminUsers.certificationApproved', { name: user.display_name || user.username }) : t('adminUsers.certificationRejected', { name: user.display_name || user.username }); await load() } catch (exception) { error.value = exception.message } }
async function decideCertification(user, decision) { error.value = ''; try { await authApi.decideRoleCertification(user.id, decision); notice.value = decision === 'approve' ? t('adminUsers.applicationApproved', { name: user.display_name || user.username }) : t('adminUsers.applicationRejected', { name: user.display_name || user.username }); await load() } catch (exception) { error.value = exception.message } }
function roleLabel(role) { return t(`role.${role}`) === `role.${role}` ? role : t(`role.${role}`) }
function statusLabel(user) { if (user.role_application_status === 'pending') return t('adminUsers.status.pendingApplication'); if (user.role !== 'teacher') return t('adminUsers.normal'); return t(`adminUsers.status.${user.status}`) === `adminUsers.status.${user.status}` ? user.status : t(`adminUsers.status.${user.status}`) }
onMounted(load)
</script>

<style scoped>
.table-scroll { overflow-x: auto; margin-top: 14px; }.directory-table { width: 100%; min-width: 760px; border-collapse: collapse; font-size: 12px; }.directory-table th, .directory-table td { padding: 11px 9px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }.directory-table th { color: var(--on-surface-variant); font-size: 11px; font-weight: 600; }.directory-table td strong, .directory-table td small { display: block; }.directory-table td small { color: var(--on-surface-variant); font-size: 10px; margin-top: 3px; }.directory-actions { display: flex; flex-wrap: wrap; gap: 6px; min-width: 250px; }.role-label, .status-label { display: inline-block; padding: 3px 7px; border-radius: var(--radius-xs); background: var(--surface-container); color: var(--on-surface-variant); white-space: nowrap; font-size: 10px; }.role-label.admin { background: #dbeafe; color: #1d4ed8; }.role-label.teacher { background: #dcfce7; color: #166534; }.status-label.pending { background: #ffedd5; color: #9a3412; }.status-label.active { background: #dcfce7; color: #166534; }.status-label.rejected { background: #fee2e2; color: #b91c1c; }
@media (min-width: 769px) {
  .directory-table { min-width: 0; table-layout: fixed; }
  .directory-table th:nth-child(3) { width: 90px; }
  .directory-table th:nth-child(4) { width: 76px; }
  .directory-table th:last-child { width: 94px; }
  .directory-table td { overflow-wrap: anywhere; }
}
</style>
