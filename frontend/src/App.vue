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
        <span v-if="auth.user" class="user-chip" :title="'角色: ' + roleLabel(auth.user.role)">
          {{ auth.user.display_name || auth.user.username }}
        </span>
        <button class="logout-btn" @click="logout">退出</button>
      </nav>
    </header>
    <main>
      <RouterView />
    </main>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { auth, clearAuth } from './auth'
import { logoutApi } from './api'

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
</script>
