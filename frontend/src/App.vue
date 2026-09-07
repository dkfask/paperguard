<template>
  <div>
    <header v-if="auth.token">
      <div class="header-inner">
        <!-- Stitch Brand Logo -->
        <div class="brand-section">
          <RouterLink to="/" class="brand-logo">
            <img alt="PaperGuard Logo" src="https://lh3.googleusercontent.com/aida-public/AB6AXuDql0yGL1_UN68HUwkaynIRDx8ioj_UnjbtZPq7DrYzntCwO0hGlpP3r1kPs1FliBzugPbxJeZ5285tEhVkW-z45Zj_nbfqlq17oN_RIAI7A2I_RaG0nkCcPBoV-O7zcEg04VSsWuY0-qlQertUcX-IABRfiFxfwTbwbCGaQDiU5f46xmbag_MRq2ouV7mFAIYiVLmjJmY8Md5HWZj1ru4mtz1lReRMvq1eB0VtgTNRcTeC1xlXBLgwlY6Ya_3v6yTKEkKC6Nl6tleJ" />
            <span class="brand-title">PaperGuard</span>
          </RouterLink>
        </div>

        <!-- Stitch Nav Links -->
        <nav class="nav-links">
          <RouterLink to="/" class="stitch-nav-link" :class="{ active: $route.path === '/' && (!$route.query.tab || $route.query.tab === 'check') }" @click="setTab('check')">
            {{ t('navDashboard') }}
          </RouterLink>
          <RouterLink to="/" class="stitch-nav-link" :class="{ active: $route.path === '/' && $route.query.tab === 'projects' }" @click="setTab('projects')">
            {{ t('navProjects') }}
          </RouterLink>
          <RouterLink to="/templates" class="stitch-nav-link" :class="{ active: $route.path.startsWith('/templates') }">
            {{ t('navStandards') }}
          </RouterLink>
          <button v-if="auth.user?.role === 'admin' || (auth.user?.role === 'teacher' && auth.user?.status === 'active')"
                  class="small" style="background: var(--surface-container-low); border-color: var(--primary-fixed);"
                  @click="openTeacherModal">
            {{ t('navTeacher') }}
          </button>
          <button v-if="auth.user?.role === 'admin'" class="small" @click="openUserAdminModal">
            {{ t('navUserAdmin') }}
          </button>
          <button v-if="auth.user?.role === 'admin'" class="small" @click="openAdminAiModal">
            {{ t('navAdminAi') }}
          </button>
        </nav>

        <!-- Stitch Trailing User Avatar & Lang Switcher -->
        <div class="user-tray">
          <!-- 中英文切换按钮 -->
          <button class="lang-switch-btn" @click="toggleLang" title="切换语言 / Switch Language">
            <span class="material-symbols-outlined" style="font-size: 15px">translate</span>
            <span>{{ lang === 'zh' ? 'EN' : '中文' }}</span>
          </button>

          <div class="user-avatar-btn" :title="auth.user?.display_name || auth.user?.username">
            <img alt="User profile" src="https://lh3.googleusercontent.com/aida-public/AB6AXuC0wMEf8sOMZsI9UqG-vjToR-y29UMQA0stFOsuSCpfF_147cakqSnuDMlmfbWRji89D8HDAT0hg9NaTD7ofJj2PKuyiEwSNfG1cuWozoyE-ZZi8keBNQc5HWp9eAezPEHrBltaXRg1NCSO4xHSjE1loBGYhxaxDYmgye_x-kyW5JbV90xDbt6XRj63EzKZLMW1hjJk87WcSbgtlqvlj0EU9JQpSa2b9Tp_ACkLExFBGpZld8_ZyMC7zmOdzFuxDTWrRdCcMfIfnt8n" />
          </div>
          <span style="font-size: 13px; font-weight: 600; color: var(--on-surface);">
            {{ auth.user?.display_name || auth.user?.username }}
            <span v-if="auth.user?.role === 'teacher' && auth.user?.status === 'pending'" style="color: var(--tertiary); font-size: 11px; margin-left: 4px">
              ({{ t('pendingAuth') }})
            </span>
          </span>
          <button class="small" @click="logout">{{ t('logout') }}</button>
        </div>
      </div>
    </header>
    <div v-if="auth.user?.role === 'teacher' && auth.user.status === 'pending'" class="pending-bar">
      <span>📢 您的教师账号正在等待管理员认证审核中，通过后将开通模板管理等教师权限。</span>
    </div>
    <main>
      <RouterView />
    </main>

    <!-- 管理员设置默认 AI 模型弹窗 -->
    <div v-if="showAdminAiModal" class="modal-mask" @click.self="showAdminAiModal = false">
      <div class="modal card">
        <h3 style="margin-top:0">⚙️ 系统默认 AI 模型设置</h3>
        <p class="hint" style="font-size: 13px; margin-top: 0">
          管理员配置全站默认的 OpenAI 兼容模型，所有未自带 API Key 的用户都将默认使用该配置。
          <br />
          <template v-if="adminAiInfo.configured">
            当前来源: {{ adminAiInfo.source === 'database' ? '数据库 (管理员自定义)' : '环境变量 (PAPERGUARD_AI_*)' }}
          </template>
          <template v-else>
            当前系统未配置默认模型（未填写环境变量且数据库无配置）
          </template>
        </p>
        <label class="field">Base URL
          <input v-model="adminAiForm.base_url" placeholder="如 https://api.openai.com/v1" />
        </label>
        <label class="field">API Key
          <input v-model="adminAiForm.api_key" type="password" autocomplete="new-password"
                 :placeholder="adminAiInfo.configured ? '已配置 ' + adminAiInfo.api_key_masked + ' (输入新Key可覆盖)' : '必填, 如 sk-xxx'" />
        </label>
        <label class="field">模型
          <input v-model="adminAiForm.model" placeholder="如 gpt-4o-mini / glm-4-flash" />
        </label>
        <p v-if="adminAiInfo.message" :class="adminAiInfo.ok ? 'ok-banner' : 'error-banner'" style="font-size: 13px">
          {{ adminAiInfo.ok ? '✅' : '⚠' }} {{ adminAiInfo.message }}
        </p>
        <div class="form-row">
          <button class="primary" :disabled="adminAiInfo.saving" @click="saveAdminAi">
            {{ adminAiInfo.saving ? '保存中…' : '保存系统默认' }}
          </button>
          <button :disabled="adminAiInfo.testing" @click="testAdminAi">
            {{ adminAiInfo.testing ? '测试中…' : '测试连接' }}
          </button>
          <button v-if="adminAiInfo.source === 'database'" @click="clearAdminAi">
            恢复环境变量默认值
          </button>
          <button @click="showAdminAiModal = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- 管理员用户与教师认证管理弹窗 -->
    <div v-if="showUserAdminModal" class="modal-mask" @click.self="showUserAdminModal = false">
      <div class="modal card user-admin-modal">
        <div class="modal-head">
          <h3 style="margin: 0">👥 用户与教师认证管理</h3>
          <button class="small" @click="fetchUsers">刷新列表</button>
        </div>
        <p class="hint" style="font-size: 13px; margin: 8px 0 14px">
          审核教师账号注册认证、升级教师为管理员（实现管理员可以是教师），或调整全站用户角色。
        </p>

        <p v-if="userAdminMsg" :class="userAdminOk ? 'ok-banner' : 'error-banner'" style="font-size: 13px">
          {{ userAdminOk ? '✅' : '⚠' }} {{ userAdminMsg }}
        </p>

        <div v-if="loadingUsers" style="padding: 20px; text-align: center; color: var(--muted)">
          正在读取全站用户列表…
        </div>

        <div v-else class="user-table-wrap">
          <table class="user-table">
            <thead>
              <tr>
                <th>用户名</th>
                <th>姓名/昵称</th>
                <th>当前角色</th>
                <th>认证状态</th>
                <th>快捷管理与操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="u in userList" :key="u.id" :class="{ 'pending-row': u.role === 'teacher' && u.status === 'pending' }">
                <td>
                  <strong>{{ u.username }}</strong>
                  <span v-if="u.username === 'admin'" class="tag-admin">内置主管理员</span>
                </td>
                <td>{{ u.display_name || '-' }}</td>
                <td>
                  <span class="role-tag" :class="u.role">{{ roleLabel(u.role) }}</span>
                </td>
                <td>
                  <span v-if="u.role === 'teacher'" class="status-tag" :class="u.status">
                    {{ u.status === 'active' ? '已认证' : (u.status === 'pending' ? '待审核' : '已驳回') }}
                  </span>
                  <span v-else class="status-tag active">正常</span>
                </td>
                <td class="action-cell">
                  <!-- 待认证教师快捷审核 -->
                  <template v-if="u.role === 'teacher' && u.status === 'pending'">
                    <button class="tiny primary" @click="changeRoleStatus(u.id, 'teacher', 'active')">
                      ✓ 通过认证
                    </button>
                    <button class="tiny danger" @click="changeRoleStatus(u.id, 'teacher', 'rejected')">
                      ✕ 驳回
                    </button>
                  </template>

                  <!-- 角色调整 (管理员/教师/学生互转) -->
                  <template v-if="u.username !== 'admin'">
                    <button v-if="u.role !== 'admin'" class="tiny" title="赋予系统最高管理权限(管理员可以是教师)" @click="changeRoleStatus(u.id, 'admin', 'active')">
                      设为管理员
                    </button>
                    <button v-if="u.role !== 'teacher'" class="tiny" @click="changeRoleStatus(u.id, 'teacher', 'active')">
                      设为教师
                    </button>
                    <button v-if="u.role !== 'student'" class="tiny" @click="changeRoleStatus(u.id, 'student', 'active')">
                      设为学生
                    </button>
                  </template>
                  <span v-else style="color: var(--muted); font-size: 12px">系统基准</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="form-row" style="margin-top: 16px; justify-content: flex-end">
          <button @click="showUserAdminModal = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- 教师指导学生与账号导入管理弹窗 -->
    <div v-if="showTeacherModal" class="modal-mask" @click.self="showTeacherModal = false">
      <div class="modal card teacher-students-modal">
        <div class="modal-head">
          <h3 style="margin: 0">👨‍🎓 指导学生管理与账号批量导入</h3>
          <div style="display: flex; gap: 8px">
            <button class="small" :class="{ primary: teacherTab === 'list' }" @click="teacherTab = 'list'">
              学生名单 ({{ myStudents.length }})
            </button>
            <button class="small" :class="{ primary: teacherTab === 'import' }" @click="teacherTab = 'import'">
              ➕ 批量导入学生
            </button>
          </div>
        </div>

        <p v-if="teacherMsg" :class="teacherOk ? 'ok-banner' : 'error-banner'" style="font-size: 13px; margin: 10px 0">
          {{ teacherOk ? '✅' : '⚠' }} {{ teacherMsg }}
        </p>

        <!-- Tab 1: 学生名单与进度监控 -->
        <div v-if="teacherTab === 'list'">
          <div style="display: flex; justify-content: space-between; align-items: center; margin: 8px 0 12px">
            <p class="hint" style="margin: 0; font-size: 13px">
              查看您名下指导学生的毕业论文撰写动态，支持一键重置初始密码或解除指导。
            </p>
            <button class="tiny" @click="fetchTeacherStudents">刷新名单</button>
          </div>

          <div v-if="loadingStudents" style="padding: 24px; text-align: center; color: var(--muted)">
            正在读取指导学生数据…
          </div>

          <div v-else-if="myStudents.length === 0" style="padding: 32px; text-align: center; color: var(--muted)">
            <p style="font-size: 15px; margin-bottom: 8px">暂无关联的指导学生</p>
            <p style="font-size: 13px">可点击上方「➕ 批量导入学生」快速录入学号生成初始账号</p>
          </div>

          <div v-else class="user-table-wrap">
            <table class="user-table">
              <thead>
                <tr>
                  <th>学号 / 账号</th>
                  <th>姓名</th>
                  <th>论文题目</th>
                  <th>正文字数</th>
                  <th>最后保存</th>
                  <th>管理操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in myStudents" :key="s.id">
                  <td><strong>{{ s.username }}</strong></td>
                  <td>{{ s.display_name }}</td>
                  <td>
                    <span v-if="s.latest_project_title" style="color: var(--primary); font-weight: 500">
                      {{ s.latest_project_title }}
                    </span>
                    <span v-else style="color: var(--muted)">尚未创建论文</span>
                  </td>
                  <td>
                    <span v-if="s.total_words > 0" class="badge-count">{{ s.total_words }} 字</span>
                    <span v-else style="color: var(--muted)">0</span>
                  </td>
                  <td style="font-size: 12px; color: var(--muted)">
                    {{ s.last_updated ? s.last_updated.slice(0, 16).replace('T', ' ') : '-' }}
                  </td>
                  <td class="action-cell">
                    <button class="tiny" title="重置为默认初始密码并强制下次改密" @click="resetStudentPw(s)">
                      重置密码
                    </button>
                    <button class="tiny danger" title="解除指导关系" @click="unbindStudent(s)">
                      解绑
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Tab 2: 批量导入学生模态表单 -->
        <div v-else-if="teacherTab === 'import'">
          <p class="hint" style="font-size: 13px; margin: 8px 0 10px">
            支持直接从 Excel / WPS 复制两列（<strong>学号</strong> 和 <strong>姓名</strong>）直接粘贴，系统将批量生成账号。
          </p>

          <label class="field">学生名单 (每行一个：学号 姓名，可用空格/制表符/逗号分隔)
            <textarea v-model="importRawText" rows="6" style="width:100%; font-family: monospace; font-size: 13px; padding: 8px; border: 1px solid var(--border); border-radius: 8px;"
                      placeholder="例如:&#10;20240101 张三&#10;20240102 李四&#10;20240103 王五"></textarea>
          </label>

          <div style="display: flex; gap: 16px; margin-top: 10px">
            <label class="field" style="flex: 1">统一初始密码
              <input v-model="importDefaultPw" placeholder="默认: Student123456" />
            </label>
            <label class="field" style="flex: 1; display: flex; align-items: center; margin-top: 22px; cursor: pointer">
              <input v-model="importForceChange" type="checkbox" style="width: auto; margin-right: 6px;" />
              首次登录强制修改密码
            </label>
          </div>

          <div style="margin-top: 12px; display: flex; gap: 10px">
            <button class="primary" :disabled="importingStudents" @click="handleBulkImport">
              {{ importingStudents ? '正在生成账号…' : '🚀 开始批量导入并生成账号' }}
            </button>
          </div>

          <!-- 导入结果与凭据一键复制面板 -->
          <div v-if="importResult" style="margin-top: 16px; border-top: 1px solid var(--border); padding-top: 12px">
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span style="font-size: 14px; font-weight: 600">
                🎉 导入结果：成功 {{ importResult.success_count }} 人，失败 {{ importResult.fail_count }} 人
              </span>
              <button v-if="importResult.created && importResult.created.length" class="small primary" @click="copyCredentials">
                📋 一键复制学生账号密码清单
              </button>
            </div>

            <div v-if="importResult.created && importResult.created.length" class="user-table-wrap" style="margin-top: 10px; max-height: 180px">
              <table class="user-table">
                <thead>
                  <tr>
                    <th>学号/账号</th>
                    <th>姓名</th>
                    <th>初始登录密码</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="c in importResult.created" :key="c.username">
                    <td><strong>{{ c.username }}</strong></td>
                    <td>{{ c.display_name }}</td>
                    <td><code>{{ c.initial_password }}</code></td>
                    <td>
                      <span class="status-tag active">
                        {{ c.action === 'created' ? '新建成功' : '已绑定现有账号' }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div v-if="importResult.failed && importResult.failed.length" style="margin-top: 10px">
              <span style="color: var(--error); font-size: 13px; font-weight: 500">以下条目导入失败：</span>
              <ul style="font-size: 12px; color: var(--error); margin: 4px 0 0 18px; padding: 0">
                <li v-for="f in importResult.failed" :key="f.username">
                  {{ f.username }} ({{ f.display_name }}): {{ f.reason }}
                </li>
              </ul>
            </div>
          </div>
        </div>

        <div class="form-row" style="margin-top: 16px; justify-content: flex-end">
          <button @click="showTeacherModal = false">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { auth, clearAuth } from './auth'
import { useI18n } from './i18n'
import {
  logoutApi, getAdminAiConfig, saveAdminAiConfig, clearAdminAiConfig, testAdminAiConfig,
  getAdminUsers, updateAdminUserRoleStatus,
  getTeacherStudents, bulkImportStudents, resetTeacherStudentPassword, unbindTeacherStudent,
} from './api'

const router = useRouter()
const { lang, setLang, t } = useI18n()

function toggleLang() {
  setLang(lang.value === 'zh' ? 'en' : 'zh')
}
function setTab(tab) {
  router.push({ path: '/', query: { tab } })
}

function roleLabel(role) {
  return { admin: '管理员', teacher: '教师', student: '学生' }[role] || role
}

async function logout() {
  try { await logoutApi() } catch { /* 本地会话仍需清理 */ }
  clearAuth()
  router.push('/login')
}

// ---------- 管理员用户与教师认证审核 ----------
const showUserAdminModal = ref(false)
const userList = ref([])
const loadingUsers = ref(false)
const userAdminMsg = ref('')
const userAdminOk = ref(null)

async function fetchUsers() {
  loadingUsers.value = true
  userAdminMsg.value = ''
  try {
    userList.value = await getAdminUsers()
  } catch (e) {
    userAdminOk.value = false
    userAdminMsg.value = e.message || '获取用户列表失败'
  } finally {
    loadingUsers.value = false
  }
}

async function openUserAdminModal() {
  showUserAdminModal.value = true
  userAdminMsg.value = ''
  userAdminOk.value = null
  await fetchUsers()
}

async function changeRoleStatus(userId, role, status) {
  userAdminMsg.value = ''
  try {
    await updateAdminUserRoleStatus(userId, { role, status })
    userAdminOk.value = true
    userAdminMsg.value = '更新成功！'
    await fetchUsers()
  } catch (e) {
    userAdminOk.value = false
    userAdminMsg.value = e.message || '操作失败'
  }
}

// ---------- 管理员设置系统默认模型 ----------
const showAdminAiModal = ref(false)
const adminAiForm = reactive({ base_url: '', api_key: '', model: '' })
const adminAiInfo = reactive({
  configured: false, source: null, api_key_masked: '', has_env_fallback: false,
  testing: false, saving: false, message: '', ok: null,
})

async function openAdminAiModal() {
  showAdminAiModal.value = true
  adminAiInfo.message = ''
  adminAiInfo.ok = null
  try {
    const cfg = await getAdminAiConfig()
    adminAiInfo.configured = cfg.configured
    adminAiInfo.source = cfg.source
    adminAiInfo.api_key_masked = cfg.api_key_masked || ''
    adminAiInfo.has_env_fallback = cfg.has_env_fallback
    adminAiForm.base_url = cfg.base_url || ''
    adminAiForm.model = cfg.model || ''
    adminAiForm.api_key = ''
  } catch (e) {
    adminAiInfo.message = e.message || '读取系统 AI 配置失败'
  }
}

function adminAiFilled() {
  return !!(adminAiForm.base_url && adminAiForm.api_key && adminAiForm.model)
}

async function testAdminAi() {
  if (!adminAiFilled()) {
    adminAiInfo.ok = false
    adminAiInfo.message = '请先完整填写 Base URL / API Key / 模型'
    return
  }
  adminAiInfo.testing = true
  adminAiInfo.message = ''
  try {
    const res = await testAdminAiConfig({ ...adminAiForm })
    adminAiInfo.ok = res.ok
    adminAiInfo.message = res.ok ? `测试成功 (模型: ${res.model})` : (res.error || '测试失败')
  } catch (e) {
    adminAiInfo.ok = false
    adminAiInfo.message = e.message || '测试失败'
  } finally {
    adminAiInfo.testing = false
  }
}

async function saveAdminAi() {
  if (!adminAiFilled()) {
    adminAiInfo.ok = false
    adminAiInfo.message = '请先完整填写 Base URL / API Key / 模型'
    return
  }
  adminAiInfo.saving = true
  adminAiInfo.message = ''
  try {
    await saveAdminAiConfig({ ...adminAiForm })
    adminAiInfo.configured = true
    adminAiInfo.source = 'database'
    adminAiInfo.ok = true
    adminAiInfo.message = '保存成功！已设为全体用户默认模型'
    const updated = await getAdminAiConfig()
    adminAiInfo.api_key_masked = updated.api_key_masked || ''
    adminAiForm.api_key = ''
  } catch (e) {
    adminAiInfo.ok = false
    adminAiInfo.message = e.message || '保存失败'
  } finally {
    adminAiInfo.saving = false
  }
}

async function clearAdminAi() {
  try {
    await clearAdminAiConfig()
    adminAiInfo.ok = true
    adminAiInfo.message = '已清除自定义配置'
    await openAdminAiModal()
  } catch (e) {
    adminAiInfo.ok = false
    adminAiInfo.message = e.message || '清除失败'
  }
}

// ---------- 教师指导学生与批量导入 ----------
const showTeacherModal = ref(false)
const teacherTab = ref('list') // 'list' | 'import'
const myStudents = ref([])
const loadingStudents = ref(false)
const teacherMsg = ref('')
const teacherOk = ref(null)

const importRawText = ref('')
const importDefaultPw = ref('Student123456')
const importForceChange = ref(true)
const importingStudents = ref(false)
const importResult = ref(null)

async function fetchTeacherStudents() {
  loadingStudents.value = true
  teacherMsg.value = ''
  try {
    const res = await getTeacherStudents()
    myStudents.value = res.students || []
  } catch (e) {
    teacherOk.value = false
    teacherMsg.value = e.message || '获取学生名单失败'
  } finally {
    loadingStudents.value = false
  }
}

async function openTeacherModal() {
  showTeacherModal.value = true
  teacherTab.value = 'list'
  teacherMsg.value = ''
  teacherOk.value = null
  importResult.value = null
  await fetchTeacherStudents()
}

async function resetStudentPw(student) {
  if (!confirm(`确定要为学生 ${student.display_name}（${student.username}）重置密码为 Student123456 吗？`)) return
  teacherMsg.value = ''
  try {
    await resetTeacherStudentPassword(student.id, {
      new_password: 'Student123456',
      force_change_password: true,
    })
    teacherOk.value = true
    teacherMsg.value = `已为学生 ${student.display_name} 重置密码为 Student123456（首次登录强制改密）`
  } catch (e) {
    teacherOk.value = false
    teacherMsg.value = e.message || '重置密码失败'
  }
}

async function unbindStudent(student) {
  if (!confirm(`确定解除对学生 ${student.display_name}（${student.username}）的指导关系吗？`)) return
  teacherMsg.value = ''
  try {
    await unbindTeacherStudent(student.id)
    teacherOk.value = true
    teacherMsg.value = `已解除与学生 ${student.display_name} 的指导关系`
    await fetchTeacherStudents()
  } catch (e) {
    teacherOk.value = false
    teacherMsg.value = e.message || '解绑失败'
  }
}

async function handleBulkImport() {
  const text = importRawText.value.trim()
  if (!text) {
    teacherOk.value = false
    teacherMsg.value = '请先粘贴或输入学生名单'
    return
  }

  // 解析多行: 支持 制表符(Excel复制)、逗号、空格 分隔
  const lines = text.split('\n')
  const parsedStudents = []
  for (const rawLine of lines) {
    const line = rawLine.trim()
    if (!line) continue
    const parts = line.split(/[\t,，\s]+/)
    if (parts.length >= 2) {
      parsedStudents.push({ username: parts[0].trim(), display_name: parts[1].trim() })
    } else if (parts.length === 1) {
      parsedStudents.push({ username: parts[0].trim(), display_name: parts[0].trim() })
    }
  }

  if (parsedStudents.length === 0) {
    teacherOk.value = false
    teacherMsg.value = '未能解析出有效学生数据，请检查格式'
    return
  }

  importingStudents.value = true
  teacherMsg.value = ''
  try {
    const res = await bulkImportStudents({
      students: parsedStudents,
      default_password: importDefaultPw.value.trim() || 'Student123456',
      force_change_password: importForceChange.value,
    })
    importResult.value = res
    teacherOk.value = true
    teacherMsg.value = `导入完成！成功生成 ${res.success_count} 个账号`
    await fetchTeacherStudents()
  } catch (e) {
    teacherOk.value = false
    teacherMsg.value = e.message || '导入失败'
  } finally {
    importingStudents.value = false
  }
}

function copyCredentials() {
  if (!importResult.value || !importResult.value.created) return
  const lines = ['【PaperGuard 毕业设计论文学生账号名单】']
  for (const item of importResult.value.created) {
    lines.push(`学号/账号: ${item.username} | 姓名: ${item.display_name} | 初始密码: ${item.initial_password}`)
  }
  lines.push('登录地址: https://paper.thunderuni.com')
  const content = lines.join('\n')
  navigator.clipboard.writeText(content).then(() => {
    alert('✅ 学生账号密码清单已成功复制到剪贴板，可直接发送至班级群！')
  }).catch(() => {
    alert('复制失败，请手动在下方表格中复制')
  })
}
</script>

<style scoped>
.admin-ai-btn {
  background: transparent;
  color: var(--primary);
  border: 1px dashed var(--primary);
  font-size: 13px;
  padding: 4px 10px;
  cursor: pointer;
  border-radius: 6px;
  font-weight: 500;
}
.admin-ai-btn:hover {
  background: #eff6ff;
}
.teacher-btn {
  background: #f0fdf4;
  color: #166534;
  border: 1px solid #bbf7d0;
  font-size: 13px;
  padding: 4px 10px;
  cursor: pointer;
  border-radius: 6px;
  font-weight: 500;
}
.teacher-btn:hover {
  background: #dcfce7;
}
.modal-mask {
  position: fixed; inset: 0; background: rgba(15, 23, 42, .45);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  display: flex; align-items: center; justify-content: center; z-index: 100;
  animation: fadeIn .15s ease-out;
}
@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
.modal {
  width: min(520px, 92vw); text-align: left; max-height: 88vh; overflow-y: auto;
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xl);
  border: 1px solid var(--slate-200);
  animation: modalScale .2s cubic-bezier(.16, 1, .3, 1);
}
@keyframes modalScale {
  from { opacity: 0; transform: scale(0.96) translateY(4px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}
.modal .field { display: block; font-size: 13px; color: var(--muted); margin-bottom: 12px; }
.modal .field input { width: 100%; margin-top: 4px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; }
.ok-banner { background: #f0fdf4; border: 1px solid #bbf7d0; color: #15803d; border-radius: 8px; padding: 8px 12px; margin-bottom: 10px; }
.error-banner { background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c; border-radius: 8px; padding: 8px 12px; margin-bottom: 10px; }

.status-badge.pending {
  background: #fef3c7;
  color: #b45309;
  border: 1px solid #fde68a;
  border-radius: 999px;
  font-size: 11px;
  padding: 1px 6px;
  margin-left: 6px;
}
.pending-bar {
  background: #fffbeb;
  border-bottom: 1px solid #fef3c7;
  color: #92400e;
  font-size: 13px;
  padding: 8px 24px;
  text-align: center;
}

.user-admin-modal {
  width: min(720px, 94vw);
}
.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.user-table-wrap {
  max-height: 420px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.user-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.user-table th, .user-table td {
  padding: 10px 12px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}
.user-table th {
  background: #f8fafc;
  color: var(--muted);
  font-weight: 600;
  position: sticky;
  top: 0;
  z-index: 1;
}
.pending-row {
  background: #fffdf5;
}
.role-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
}
.role-tag.admin { background: #fee2e2; color: #991b1b; }
.role-tag.teacher { background: #e0e7ff; color: #3730a3; }
.role-tag.student { background: #f1f5f9; color: #475569; }

.status-tag {
  display: inline-block;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
}
.status-tag.active { background: #dcfce7; color: #166534; }
.status-tag.pending { background: #fef3c7; color: #b45309; font-weight: 600; }
.status-tag.rejected { background: #fee2e2; color: #991b1b; }

.tag-admin {
  background: #f1f5f9;
  color: var(--muted);
  font-size: 11px;
  padding: 1px 5px;
  border-radius: 4px;
  margin-left: 4px;
}
.action-cell {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
button.tiny {
  font-size: 12px;
  padding: 3px 8px;
  border-radius: 6px;
}
button.tiny.danger {
  color: var(--error);
  border-color: #fecaca;
}
button.tiny.danger:hover {
  background: #fee2e2;
}

.teacher-students-modal {
  width: min(840px, 95vw);
}
.badge-count {
  background: #eff6ff;
  color: #1d4ed8;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
}
</style>
