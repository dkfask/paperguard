<template>
  <div>
    <header v-if="auth.token">
      <div class="brand">
        <RouterLink to="/" class="logo">📄 PaperGuard</RouterLink>
        <span class="tagline">论文格式规范约束与检查</span>
      </div>
      <nav class="nav">
        <RouterLink to="/" class="nav-link" @click="setTab('check')">上传检查</RouterLink>
        <RouterLink to="/" class="nav-link" @click="setTab('projects')">我的论文</RouterLink>
        <RouterLink to="/templates" class="nav-link">模板管理</RouterLink>
        <button v-if="auth.user?.role === 'admin'" class="nav-btn admin-ai-btn" @click="openAdminAiModal">
          ⚙️ 默认模型
        </button>
        <span v-if="auth.user" class="user-chip" :title="'角色: ' + roleLabel(auth.user.role)">
          {{ auth.user.display_name || auth.user.username }}
        </span>
        <button class="logout-btn" @click="logout">退出</button>
      </nav>
    </header>
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
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { auth, clearAuth } from './auth'
import {
  logoutApi, getAdminAiConfig, saveAdminAiConfig, clearAdminAiConfig, testAdminAiConfig,
} from './api'

const router = useRouter()
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
.modal-mask {
  position: fixed; inset: 0; background: rgba(15, 23, 42, .45);
  display: flex; align-items: center; justify-content: center; z-index: 100;
}
.modal {
  width: min(500px, 92vw); text-align: left; max-height: 88vh; overflow-y: auto;
}
.modal .field { display: block; font-size: 13px; color: var(--muted); margin-bottom: 12px; }
.modal .field input { width: 100%; margin-top: 4px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; }
.ok-banner { background: #f0fdf4; border: 1px solid #bbf7d0; color: #15803d; border-radius: 8px; padding: 8px 12px; margin-bottom: 10px; }
.error-banner { background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c; border-radius: 8px; padding: 8px 12px; margin-bottom: 10px; }
</style>
