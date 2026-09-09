<template>
  <div class="org-page">
    <div class="page-head">
      <div><span class="eyebrow">ORGANIZATION SCOPE</span><h1>组织与作用域</h1><p>维护学校、学院、专业、课程和批次，规范会按作用域自动继承。</p></div>
      <button v-if="canManage" class="primary" @click="newUnit">新建组织</button>
    </div>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <div class="org-layout">
      <section class="unit-list card">
        <div class="list-head"><strong>组织层级</strong><button class="tiny" :disabled="loading" @click="load">刷新</button></div>
        <div v-if="loading" class="empty">正在加载…</div>
        <div v-else-if="units.length === 0" class="empty">暂无组织，请先创建学校</div>
        <button v-for="unit in orderedUnits" :key="unit.id" class="unit-row" :class="{ active: form.id === unit.id }" :style="{ paddingLeft: `${10 + depth(unit) * 18}px` }" @click="edit(unit)">
          <span class="unit-name">{{ unit.name }}</span><span class="unit-type">{{ typeLabel(unit.type) }}</span>
        </button>
      </section>
      <section v-if="editing" class="editor card">
        <div class="editor-head"><div><span class="type-tag">{{ form.id ? '组织详情' : '新建组织' }}</span><h2>{{ form.id ? '编辑组织' : '创建组织' }}</h2></div></div>
        <div class="form-grid">
          <label>名称<input v-model="form.name" :disabled="!!form.id && !canManage" placeholder="如：信息工程学院" /></label>
          <label>编码<input v-model="form.code" :disabled="!!form.id && !canManage" placeholder="可选" /></label>
          <label>类型<select v-model="form.type" :disabled="!!form.id"><option value="school">学校</option><option value="college">学院</option><option value="major">专业</option><option value="course">课程</option><option value="batch">批次</option></select></label>
          <label>父级<select v-model="form.parent_id" :disabled="!!form.id"><option value="">无（学校）</option><option v-for="unit in units.filter((item) => item.id !== form.id)" :key="unit.id" :value="unit.id">{{ unit.name }} · {{ typeLabel(unit.type) }}</option></select></label>
          <label class="span-2">说明<input v-model="form.metadata.description" :disabled="!canManage" placeholder="组织说明" /></label>
        </div>
        <div class="actions"><button v-if="canManage" class="primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存' }}</button><button v-if="canManage && form.id" class="danger" @click="remove">删除</button></div>
      </section>
      <section v-else class="editor card empty"><span class="material-symbols-outlined">account_tree</span><h2>选择一个组织</h2><p>组织是规范自动继承和用户权限范围的基础。</p></section>
    </div>

    <section v-if="auth.user?.role === 'admin'" class="members card">
      <div class="list-head"><div><strong>用户作用域</strong><p>把用户放入学校、学院、专业或批次后，创建项目会自动带入这些范围。</p></div><button class="tiny" @click="loadUsers">刷新</button></div>
      <div class="member-grid"><label>用户<select v-model="selectedUserId"><option value="">选择用户</option><option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }} · {{ user.username }}</option></select></label><label>作用域<select v-model="selectedScope"><option value="">选择组织</option><option v-for="unit in units" :key="unit.id" :value="unit.id">{{ unit.name }} · {{ typeLabel(unit.type) }}</option></select></label><button class="primary" :disabled="!selectedUserId || !selectedScope || saving" @click="assignScope">应用</button></div>
      <div v-if="users.length" class="user-list"><span v-for="user in users" :key="user.id">{{ user.display_name }}：{{ scopeNames(user.organization_context) || '未设置' }}</span></div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { auth, isManager } from '../auth'
import { deleteOrganization, fetchOrganizations, getAdminUsers, saveOrganization, updateUserOrganizationContext } from '../api'

const units = ref([]); const users = ref([]); const loading = ref(false); const saving = ref(false); const error = ref(''); const editing = ref(false)
const form = reactive(emptyUnit()); const selectedUserId = ref(''); const selectedScope = ref('')
const canManage = computed(() => isManager() && ['admin', 'college_admin'].includes(auth.user?.role))
const orderedUnits = computed(() => { const result = []; const walk = (parentId, depth) => { units.value.filter((unit) => unit.parent_id === parentId).sort((a, b) => a.name.localeCompare(b.name)).forEach((unit) => { result.push(unit); walk(unit.id, depth + 1) }) }; walk('', 0); return result })
function emptyUnit() { return { id: '', name: '', type: 'school', parent_id: '', code: '', status: 'active', metadata: { description: '' } } }
function typeLabel(type) { return { school: '学校', college: '学院', major: '专业', course: '课程', batch: '批次' }[type] || type }
function depth(unit) { let value = 0; let parent = units.value.find((item) => item.id === unit.parent_id); while (parent) { value += 1; parent = units.value.find((item) => item.id === parent.parent_id) }; return value }
function edit(unit) { Object.assign(form, { ...unit, metadata: { ...(unit.metadata || {}) } }); editing.value = true; error.value = '' }
function newUnit() { Object.assign(form, emptyUnit()); editing.value = true; error.value = '' }
async function load() { loading.value = true; error.value = ''; try { units.value = await fetchOrganizations() } catch (e) { error.value = e.message } finally { loading.value = false } }
async function save() { saving.value = true; error.value = ''; try { const result = await saveOrganization({ ...form }, !form.id); Object.assign(form, { ...result, metadata: { ...(result.metadata || {}) } }); await load() } catch (e) { error.value = e.message } finally { saving.value = false } }
async function remove() { if (!window.confirm(`删除组织「${form.name}」？`)) return; try { await deleteOrganization(form.id); editing.value = false; await load() } catch (e) { error.value = e.message } }
async function loadUsers() { try { users.value = await getAdminUsers() } catch (e) { error.value = e.message } }
async function assignScope() { saving.value = true; try { const user = users.value.find((item) => item.id === selectedUserId.value); const context = { ...(user?.organization_context || {}) }; const unit = units.value.find((item) => item.id === selectedScope.value); if (unit) context[`${unit.type}_id`] = unit.id; await updateUserOrganizationContext(selectedUserId.value, context); await loadUsers() } catch (e) { error.value = e.message } finally { saving.value = false } }
function scopeNames(context) { return Object.values(context || {}).map((id) => units.value.find((unit) => unit.id === id)?.name || id).join('、') }
onMounted(async () => { await load(); if (auth.user?.role === 'admin') await loadUsers() })
</script>

<style scoped>
.org-page { padding: 28px 0; }.page-head { display:flex; justify-content:space-between; align-items:flex-end; gap:20px; margin-bottom:22px; }.eyebrow { color:var(--primary); font-size:11px; font-weight:700; letter-spacing:.08em; }.page-head h1 { margin:5px 0; font-size:28px; }.page-head p, .members p { color:var(--muted); font-size:13px; }.org-layout { display:grid; grid-template-columns:280px minmax(0,1fr); gap:18px; }.unit-list,.editor,.members { padding:14px; }.list-head,.editor-head,.actions,.member-grid { display:flex; align-items:center; justify-content:space-between; gap:10px; }.list-head { padding-bottom:10px; border-bottom:1px solid var(--border); }.unit-row { width:100%; display:flex; align-items:center; gap:8px; border:0; border-radius:6px; padding-top:9px; padding-bottom:9px; text-align:left; background:transparent; }.unit-row:hover,.unit-row.active { background:var(--surface-container-low); }.unit-name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.unit-type { color:var(--muted); font-size:11px; }.type-tag { color:var(--primary); font-size:11px; }.editor h2 { margin-top:6px; font-size:20px; }.form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-top:20px; }.form-grid label,.member-grid label { display:flex; flex-direction:column; gap:5px; color:var(--muted); font-size:12px; }.form-grid input,.form-grid select,.member-grid select { padding:8px 10px; border:1px solid var(--border); border-radius:6px; background:var(--card); color:var(--on-surface); font:inherit; }.span-2 { grid-column:1 / -1; }.actions { justify-content:flex-end; margin-top:20px; }.empty { min-height:260px; display:grid; place-content:center; text-align:center; color:var(--muted); }.empty .material-symbols-outlined { font-size:42px; color:var(--primary); }.members { margin-top:18px; }.member-grid { justify-content:flex-start; align-items:flex-end; margin-top:14px; }.member-grid label { min-width:220px; }.user-list { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; color:var(--muted); font-size:12px; }.user-list span { padding:6px 8px; background:var(--surface-container-low); border-radius:6px; }@media(max-width:700px){.org-layout{grid-template-columns:1fr}.page-head{align-items:flex-start;flex-direction:column}.form-grid{grid-template-columns:1fr}.span-2{grid-column:auto}.member-grid{align-items:stretch;flex-direction:column}.member-grid label{min-width:0;width:100%}}
</style>
