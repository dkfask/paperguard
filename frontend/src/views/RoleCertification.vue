<template>
  <div class="workspace-page role-certification-page">
    <section class="workspace-hero">
      <div>
        <p class="workspace-kicker">身份认证</p>
        <h1>选择你的使用身份</h1>
        <p>注册已经完成。你可以先以社会用户使用系统，也可以提交学校身份申请，审核通过后获得对应组织权限。</p>
      </div>
      <div class="workspace-identity"><span class="material-symbols-outlined">verified_user</span><div><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><span>{{ statusLabel }}</span></div></div>
    </section>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <div v-if="notice" class="ok-banner" role="status">{{ notice }}</div>

    <section class="panel certification-panel">
      <div class="role-options" role="radiogroup" aria-label="选择认证身份">
        <button v-for="option in roleOptions" :key="option.value" type="button" class="role-option" :class="{ selected: selectedRole === option.value }" @click="selectedRole = option.value">
          <span class="material-symbols-outlined">{{ option.icon }}</span><span><strong>{{ option.title }}</strong><small>{{ option.detail }}</small></span>
        </button>
      </div>

      <div v-if="selectedRole !== 'user'" class="organization-fields">
        <label>所属学校<select v-model="schoolId"><option value="">请选择学校</option><option v-for="school in schools" :key="school.id" :value="school.id">{{ school.name }}</option></select></label>
        <label v-if="colleges.length">所属学院（可选）<select v-model="collegeId"><option value="">不选择学院</option><option v-for="college in colleges" :key="college.id" :value="college.id">{{ college.name }}</option></select></label>
        <p class="hint">学生和教师需要绑定学校；学院信息用于缩小组织范围，提交后由管理员审核。</p>
      </div>

      <div class="certification-actions"><button class="primary" type="button" :disabled="submitting || loading" @click="submit">{{ submitting ? '提交中…' : '确认并继续' }}</button><button v-if="applicationStatus === 'pending'" type="button" @click="load">刷新审核状态</button></div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { auth } from '../auth'
import { authApi, type OrganizationOption } from '../features/auth/api'

const router = useRouter()

const loading = ref(true)
const submitting = ref(false)
const error = ref('')
const notice = ref('')
const selectedRole = ref('user')
const schoolId = ref('')
const collegeId = ref('')
const applicationStatus = ref('unselected')
const organizations = ref<OrganizationOption[]>([])

const roleOptions = [
  { value: 'user', icon: 'person', title: '社会用户', detail: '无需组织认证，立即进入文档工作台' },
  { value: 'student', icon: 'school', title: '学生', detail: '提交学校/学院后等待管理员审核' },
  { value: 'teacher', icon: 'psychology', title: '教师', detail: '提交学校/学院后等待管理员审核' },
]
const schools = computed(() => organizations.value.filter((item) => item.type === 'school'))
const colleges = computed(() => organizations.value.filter((item) => item.type === 'college' && (!schoolId.value || item.parent_id === schoolId.value)))
const statusLabel = computed(() => ({ unselected: '待选择身份', pending: '认证审核中', approved: '已完成认证', rejected: '申请已驳回' }[applicationStatus.value] || applicationStatus.value))

async function load() {
  loading.value = true; error.value = ''
  try {
    const data = await authApi.getRoleCertification()
    organizations.value = data.organizations || []
    applicationStatus.value = data.application_status || 'unselected'
    if (data.requested_role) selectedRole.value = data.requested_role
    schoolId.value = data.requested_organization_context?.school_id || ''
    collegeId.value = data.requested_organization_context?.college_id || ''
  } catch (exception: any) { error.value = exception?.message || '加载认证信息失败' } finally { loading.value = false }
}
async function submit() {
  error.value = ''; notice.value = ''
  if (selectedRole.value !== 'user' && !schoolId.value) { error.value = '请选择所属学校'; return }
  submitting.value = true
  try {
    const context: Record<string, string> = selectedRole.value === 'user' ? {} : { school_id: schoolId.value }
    if (collegeId.value) context.college_id = collegeId.value
    const updated = await authApi.submitRoleCertification(selectedRole.value, context)
    auth.updateUser(updated)
    applicationStatus.value = updated.role_application_status || (selectedRole.value === 'user' ? 'approved' : 'pending')
    notice.value = selectedRole.value === 'user' ? '已按社会用户身份进入系统' : '申请已提交，请等待管理员审核'
    if (selectedRole.value === 'user') router.replace('/')
  } catch (exception: any) { error.value = exception?.message || '提交认证申请失败' } finally { submitting.value = false }
}
onMounted(load)
</script>

<style scoped>
.certification-panel { max-width: 820px; }.role-options { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }.role-option { display: flex; gap: 12px; align-items: flex-start; text-align: left; padding: 16px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface); cursor: pointer; }.role-option.selected { border-color: var(--primary); background: var(--surface-container-low); }.role-option .material-symbols-outlined { color: var(--primary); }.role-option strong, .role-option small { display: block; }.role-option small { margin-top: 5px; color: var(--on-surface-variant); line-height: 1.4; }.organization-fields { display: grid; gap: 12px; margin-top: 20px; max-width: 440px; }.organization-fields label { display: grid; gap: 6px; font-weight: 600; }.organization-fields select { padding: 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface); }.hint { color: var(--on-surface-variant); font-size: 13px; }.certification-actions { display: flex; gap: 10px; margin-top: 22px; }@media (max-width: 720px) { .role-options { grid-template-columns: 1fr; } }
</style>
