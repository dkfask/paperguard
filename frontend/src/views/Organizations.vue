<template>
  <div class="org-page">
    <div class="page-head">
      <div><span class="eyebrow">{{ t('organizations.kicker') }}</span><h1>{{ t('organizations.title') }}</h1><p>{{ t('organizations.description') }}</p></div>
      <button v-if="canManage" class="primary" @click="newUnit">{{ t('organizations.new') }}</button>
    </div>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <div class="org-layout">
      <section class="unit-list card">
        <div class="list-head"><strong>{{ t('organizations.hierarchy') }}</strong><button class="tiny" :disabled="loading" @click="load">{{ t('common.refresh') }}</button></div>
        <div v-if="loading" class="empty">{{ t('common.loading') }}…</div>
        <div v-else-if="units.length === 0" class="empty">{{ t('organizations.empty') }}</div>
        <button v-for="unit in orderedUnits" :key="unit.id" class="unit-row" :class="{ active: form.id === unit.id }" :style="{ paddingLeft: `${10 + depth(unit) * 18}px` }" @click="edit(unit)">
          <span class="unit-name">{{ unit.name }}</span><span class="unit-type">{{ typeLabel(unit.type) }}</span>
        </button>
      </section>
      <section v-if="editing" class="editor card">
        <div class="editor-head"><div><span class="type-tag">{{ form.id ? t('organizations.details') : t('organizations.new') }}</span><h2>{{ form.id ? t('organizations.edit') : t('organizations.create') }}</h2></div></div>
        <div class="form-grid">
          <label>{{ t('organizations.name') }}<input v-model="form.name" :disabled="!!form.id && !canManage" :placeholder="t('organizations.namePlaceholder')" /></label>
          <label>{{ t('organizations.code') }}<input v-model="form.code" :disabled="!!form.id && !canManage" :placeholder="t('organizations.optional')" /></label>
          <label>{{ t('organizations.type') }}<select v-model="form.type" :disabled="!!form.id"><option value="school">{{ t('organizations.type.school') }}</option><option value="college">{{ t('organizations.type.college') }}</option><option value="major">{{ t('organizations.type.major') }}</option><option value="course">{{ t('organizations.type.course') }}</option><option value="batch">{{ t('organizations.type.batch') }}</option></select></label>
          <label>{{ t('organizations.parent') }}<select v-model="form.parent_id" :disabled="!!form.id"><option value="">{{ t('organizations.noParent') }}</option><option v-for="unit in units.filter((item) => item.id !== form.id)" :key="unit.id" :value="unit.id">{{ unit.name }} · {{ typeLabel(unit.type) }}</option></select></label>
          <label class="span-2">{{ t('organizations.descriptionLabel') }}<input v-model="form.metadata.description" :disabled="!canManage" :placeholder="t('organizations.descriptionPlaceholder')" /></label>
        </div>
        <div class="actions"><button v-if="canManage" class="primary" :disabled="saving" @click="save">{{ saving ? t('common.saving') : t('common.save') }}</button><button v-if="canManage && form.id" class="danger" @click="remove">{{ t('common.remove') }}</button></div>
      </section>
      <section v-else class="editor card empty"><span class="material-symbols-outlined" aria-hidden="true">account_tree</span><h2>{{ t('organizations.select') }}</h2><p>{{ t('organizations.selectDetail') }}</p></section>
    </div>

    <section v-if="auth.user?.role === 'admin'" class="members card">
      <div class="list-head"><div><strong>{{ t('organizations.userScope') }}</strong><p>{{ t('organizations.userScopeDetail') }}</p></div><button class="tiny" @click="loadUsers">{{ t('common.refresh') }}</button></div>
      <div class="member-grid"><label>{{ t('organizations.user') }}<select v-model="selectedUserId"><option value="">{{ t('organizations.selectUser') }}</option><option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }} · {{ user.username }}</option></select></label><label>{{ t('organizations.scope') }}<select v-model="selectedScope"><option value="">{{ t('organizations.selectOrganization') }}</option><option v-for="unit in units" :key="unit.id" :value="unit.id">{{ unit.name }} · {{ typeLabel(unit.type) }}</option></select></label><button class="primary" :disabled="!selectedUserId || !selectedScope || saving" @click="assignScope">{{ t('organizations.apply') }}</button></div>
      <div v-if="users.length" class="user-list"><span v-for="user in users" :key="user.id">{{ user.display_name }}: {{ scopeNames(user.organization_context) || t('organizations.notSet') }}</span></div>
    </section>
  </div>
  <ConfirmDialog :open="confirmDialog.open" :title="confirmDialog.title" :description="confirmDialog.description" :confirm-label="confirmDialog.confirmLabel" :danger="confirmDialog.danger" :busy="confirmBusy" @confirm="confirmAction" @cancel="closeConfirmDialog" />
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { auth, isManager } from '../auth'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { deleteOrganization, fetchOrganizations, getAdminUsers, saveOrganization, updateUserOrganizationContext } from '../api'
import { useI18n } from '../i18n'

const { t } = useI18n()
const units = ref([]); const users = ref([]); const loading = ref(false); const saving = ref(false); const error = ref(''); const editing = ref(false)
const form = reactive(emptyUnit()); const selectedUserId = ref(''); const selectedScope = ref('')
const confirmBusy = ref(false)
const confirmDialog = reactive({ open: false, title: '', description: '', confirmLabel: '', danger: true, action: null })
const canManage = computed(() => isManager() && ['admin', 'college_admin'].includes(auth.user?.role))
const orderedUnits = computed(() => {
  const result = []
  const ids = new Set(units.value.map((unit) => unit.id))
  const walk = (parentId) => {
    units.value
      .filter((unit) => (unit.parent_id || '') === parentId)
      .sort((a, b) => a.name.localeCompare(b.name))
      .forEach((unit) => {
        result.push(unit)
        walk(unit.id)
      })
  }
  units.value
    .filter((unit) => !unit.parent_id || !ids.has(unit.parent_id))
    .sort((a, b) => a.name.localeCompare(b.name))
    .forEach((unit) => {
      result.push(unit)
      walk(unit.id)
    })
  return result
})
function emptyUnit() { return { id: '', name: '', type: 'school', parent_id: '', code: '', status: 'active', metadata: { description: '' } } }
function typeLabel(type) { return t(`organizations.type.${type}`) === `organizations.type.${type}` ? type : t(`organizations.type.${type}`) }
function depth(unit) { let value = 0; let parent = units.value.find((item) => item.id === unit.parent_id); while (parent) { value += 1; parent = units.value.find((item) => item.id === parent.parent_id) }; return value }
function edit(unit) { Object.assign(form, { ...unit, metadata: { ...(unit.metadata || {}) } }); editing.value = true; error.value = '' }
function newUnit() { Object.assign(form, emptyUnit()); editing.value = true; error.value = '' }
async function load() { loading.value = true; error.value = ''; try { units.value = await fetchOrganizations() } catch (e) { error.value = e.message } finally { loading.value = false } }
async function save() { saving.value = true; error.value = ''; try { const result = await saveOrganization({ ...form }, !form.id); Object.assign(form, { ...result, metadata: { ...(result.metadata || {}) } }); await load() } catch (e) { error.value = e.message } finally { saving.value = false } }
function remove() {
  if (!form.id) return
  Object.assign(confirmDialog, { open: true, title: t('organizations.deleteTitle', { name: form.name }), description: t('organizations.deleteDescription'), confirmLabel: t('organizations.deleteConfirm'), action: async () => { await deleteOrganization(form.id); editing.value = false; await load() } })
}
function closeConfirmDialog() { if (confirmBusy.value) return; confirmDialog.open = false; confirmDialog.action = null }
async function confirmAction() { const action = confirmDialog.action; if (!action || confirmBusy.value) return; confirmBusy.value = true; try { await action() } catch (e) { error.value = e.message } finally { confirmBusy.value = false; closeConfirmDialog() } }
async function loadUsers() { try { users.value = await getAdminUsers() } catch (e) { error.value = e.message } }
async function assignScope() { saving.value = true; try { const user = users.value.find((item) => item.id === selectedUserId.value); const context = { ...(user?.organization_context || {}) }; const unit = units.value.find((item) => item.id === selectedScope.value); if (unit) context[`${unit.type}_id`] = unit.id; await updateUserOrganizationContext(selectedUserId.value, context); await loadUsers() } catch (e) { error.value = e.message } finally { saving.value = false } }
function scopeNames(context) { return Object.values(context || {}).map((id) => units.value.find((unit) => unit.id === id)?.name || id).join('、') }
onMounted(async () => { await load(); if (auth.user?.role === 'admin') await loadUsers() })
</script>

<style scoped>
.org-page { padding: 28px 0; }.page-head { display:flex; justify-content:space-between; align-items:flex-end; gap:20px; margin-bottom:22px; }.eyebrow { color:var(--primary); font-size:11px; font-weight:700; letter-spacing:.08em; }.page-head h1 { margin:5px 0; font-size:28px; }.page-head p, .members p { color:var(--muted); font-size:13px; }.org-layout { display:grid; grid-template-columns:280px minmax(0,1fr); gap:18px; }.unit-list,.editor,.members { padding:14px; }.list-head,.editor-head,.actions,.member-grid { display:flex; align-items:center; justify-content:space-between; gap:10px; }.list-head { padding-bottom:10px; border-bottom:1px solid var(--border); }.unit-row { width:100%; display:flex; align-items:center; gap:8px; border:0; border-radius:6px; padding-top:9px; padding-bottom:9px; text-align:left; background:transparent; }.unit-row:hover,.unit-row.active { background:var(--surface-container-low); }.unit-name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.unit-type { color:var(--muted); font-size:11px; }.type-tag { color:var(--primary); font-size:11px; }.editor h2 { margin-top:6px; font-size:20px; }.form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-top:20px; }.form-grid label,.member-grid label { display:flex; flex-direction:column; gap:5px; color:var(--muted); font-size:12px; }.form-grid input,.form-grid select,.member-grid select { padding:8px 10px; border:1px solid var(--border); border-radius:6px; background:var(--card); color:var(--on-surface); font:inherit; }.span-2 { grid-column:1 / -1; }.actions { justify-content:flex-end; margin-top:20px; }.empty { min-height:260px; display:grid; place-content:center; text-align:center; color:var(--muted); }.empty .material-symbols-outlined { font-size:42px; color:var(--primary); }.members { margin-top:18px; }.member-grid { justify-content:flex-start; align-items:flex-end; margin-top:14px; }.member-grid label { min-width:220px; }.user-list { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; color:var(--muted); font-size:12px; }.user-list span { padding:6px 8px; background:var(--surface-container-low); border-radius:6px; }@media(max-width:700px){.org-layout{grid-template-columns:1fr}.page-head{align-items:flex-start;flex-direction:column}.form-grid{grid-template-columns:1fr}.span-2{grid-column:auto}.member-grid{align-items:stretch;flex-direction:column}.member-grid label{min-width:0;width:100%}}
</style>
