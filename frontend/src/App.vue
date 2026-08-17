<template>
  <div>
    <header>
      <RouterLink to="/" class="logo">📄 PaperGuard</RouterLink>
      <span class="tagline">论文格式规范约束与检查</span>
      <nav class="nav">
        <RouterLink to="/" class="nav-link">上传检查</RouterLink>
        <RouterLink to="/templates" class="nav-link">模板管理</RouterLink>
        <RouterLink to="/" class="nav-link" @click="goToProjects">我的论文</RouterLink>
      </nav>
    </header>
    <main>
      <RouterView />
    </main>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()
function goToProjects(e) {
  e.preventDefault()
  router.push({ path: '/', query: { tab: 'projects' } })
}
</script>
