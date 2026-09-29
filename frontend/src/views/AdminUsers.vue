<template>
  <div class="workspace-page admin-users-page">
    <section class="workspace-hero"><div><p class="workspace-kicker">{{ t('adminUsers.kicker') }}</p><h1>{{ t('adminUsers.title') }}</h1><p>{{ t('adminUsers.description') }}</p></div><div class="workspace-identity"><span class="material-symbols-outlined" aria-hidden="true">manage_accounts</span><div><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><span>{{ t('role.admin') }}</span></div></div></section>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div><div v-if="notice" class="ok-banner" role="status">{{ notice }}</div>
    <AsyncState v-if="loading" state="loading" :title="t('adminUsers.loading')" :detail="t('adminUsers.loadingDetail')" />
    <template v-else>
      <section class="workspace-stats" :aria-label="t('adminUsers.summary')"><div><span>{{ t('adminUsers.totalUsers') }}</span><strong>{{ users.length }}</strong><small>{{ t('adminUsers.schoolScope') }}</small></div><div><span>待认证申请</span><strong>{{ pendingCertifications }}</strong><small>学生/教师身份</small></div><div><span>{{ t('adminUsers.activeTeachers') }}</span><strong>{{ activeTeachers }}</strong><small>{{ t('adminUsers.canGuide') }}</small></div><div><span>{{ t('adminUsers.administrators') }}</span><strong>{{ administrators }}</strong><small>{{ t('adminUsers.managementRoles') }}</small></div></section>
      <section class="panel"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('adminUsers.directoryKicker') }}</p><h2>{{ t('adminUsers.directory') }}</h2><p>{{ t('adminUsers.directoryDetail') }}</p></div><button class="icon-button" type="button" :title="t('adminUsers.refresh')" :aria-label="t('adminUsers.refresh')" :disabled="loading" @click="load"><span class="material-symbols-outlined" aria-hidden="true">refresh</span></button></div>
        <AsyncState v-if="!users.length" state="empty" :title="t('adminUsers.empty')" :detail="t('adminUsers.emptyDetail')" />
        <div v-else class="table-scroll"><table class="directory-table"><thead><tr><th>{{ t('adminUsers.username') }}</th><th>{{ t('adminUsers.name') }}</th><th>{{ t('adminUsers.role') }}</th><th>{{ t('adminUsers.status') }}</th><th>{{ t('adminUsers.actions') }}</th></tr></thead><tbody><tr v-for="user in users" :key="user.id"><td><strong>{{ user.username }}</strong><small v-if="user.username === 'admin'">{{ t('adminUsers.builtinAdmin') }}</small></td><td>{{ user.display_name || '-' }}</td><td><span class="role-label" :class="user.role">{{ roleLabel(user.role) }}</span><small v-if="user.role_application_status === 'pending'">申请：{{ roleLabel(user.requested_role) }}</small></td><td><span class="status-label" :class="user.role_application_status === 'pending' ? 'pending' : user.status">{{ statusLabel(user) }}</span></td><td class="directory-actions"><template v-if="user.username !== 'admin'"><button v-if="user.role_application_status === 'pending'" class="tiny primary" type="button" @click="decideCertification(user, 'approve')">通过身份申请</button><button v-if="user.role_application_status === 'pending'" class="tiny danger" type="button" @click="decideCertification(user, 'reject')">驳回身份申请</button><button v-if="user.role !== 'admin'" class="tiny" type="button" @click="changeRole(user, 'admin', 'active')">{{ t('adminUsers.makeAdmin') }}</button><button v-if="user.role !== 'teacher'" class="tiny" type="button" @click="changeRole(user, 'teacher', 'active')">{{ t('adminUsers.makeTeacher') }}</button><button v-if="user.role !== 'college_admin'" class="tiny" type="button" @click="changeRole(user, 'college_admin', 'active')">{{ t('adminUsers.makeCollegeAdmin') }}</button><button v-if="user.role !== 'student'" class="tiny" type="button" @click="changeRole(user, 'student', 'active')">{{ t('adminUsers.makeStudent') }}</button></template><span v-else class="muted-row">{{ t('adminUsers.builtin') }}</span></td></tr></tbody></table></div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { auth } from '../auth'
import { getAdminUsers, updateAdminUserRoleStatus } from '../api'
import AsyncState from '../components/AsyncState.vue'
import { useI18n } from '../i18n'
import { authApi } from '../features/auth/api'

const { t } = useI18n()
const users = ref([]); const loading = ref(true); const error = ref(''); const notice = ref('')
const pendingTeachers = computed(() => users.value.filter((user) => user.role === 'teacher' && user.status === 'pending').length)
const pendingCertifications = computed(() => users.value.filter((user) => user.role_application_status === 'pending').length)
const activeTeachers = computed(() => users.value.filter((user) => user.role === 'teacher' && user.status === 'active').length)
const administrators = computed(() => users.value.filter((user) => user.role === 'admin').length)
async function load() { loading.value = true; error.value = ''; try { users.value = await getAdminUsers() } catch (exception) { error.value = exception.message } finally { loading.value = false } }
async function changeRole(user, role, status) { error.value = ''; try { await updateAdminUserRoleStatus(user.id, { role, status }); notice.value = t('adminUsers.updated', { name: user.display_name || user.username }); await load() } catch (exception) { error.value = exception.message } }
async function decideCertification(user, decision) { error.value = ''; try { await authApi.decideRoleCertification(user.id, decision); notice.value = decision === 'approve' ? '身份申请已通过' : '身份申请已驳回'; await load() } catch (exception) { error.value = exception.message } }
function roleLabel(role) { return t(`role.${role}`) === `role.${role}` ? role : t(`role.${role}`) }
function statusLabel(user) { if (user.role_application_status === 'pending') return '待审核身份申请'; if (user.role !== 'teacher') return t('adminUsers.normal'); return t(`adminUsers.status.${user.status}`) === `adminUsers.status.${user.status}` ? user.status : t(`adminUsers.status.${user.status}`) }
onMounted(load)
</script>

<style scoped>
.table-scroll { overflow-x: auto; margin-top: 14px; }.directory-table { width: 100%; min-width: 760px; border-collapse: collapse; font-size: 12px; }.directory-table th, .directory-table td { padding: 11px 9px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }.directory-table th { color: var(--on-surface-variant); font-size: 11px; font-weight: 600; }.directory-table td strong, .directory-table td small { display: block; }.directory-table td small { color: var(--on-surface-variant); font-size: 10px; margin-top: 3px; }.directory-actions { display: flex; flex-wrap: wrap; gap: 6px; min-width: 250px; }.role-label, .status-label { display: inline-block; padding: 3px 7px; border-radius: var(--radius-xs); background: var(--surface-container); color: var(--on-surface-variant); white-space: nowrap; font-size: 10px; }.role-label.admin { background: #dbeafe; color: #1d4ed8; }.role-label.teacher { background: #dcfce7; color: #166534; }.status-label.pending { background: #ffedd5; color: #9a3412; }.status-label.active { background: #dcfce7; color: #166534; }.status-label.rejected { background: #fee2e2; color: #b91c1c; }
</style>
