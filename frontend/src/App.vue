<template>
  <div>
    <header>
      <div class="brand">
        <RouterLink to="/" class="logo">📄 PaperGuard</RouterLink>
        <span class="tagline">论文格式规范约束与检查</span>
      </div>
      <nav class="nav">
        <RouterLink to="/" class="nav-link" @click="setTab('check')">上传检查</RouterLink>
        <RouterLink to="/" class="nav-link" @click="setTab('projects')">我的论文</RouterLink>
        <RouterLink to="/templates" class="nav-link">模板管理</RouterLink>
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
function setTab(tab) {
  router.push({ path: '/', query: { tab } })
}
</script>
