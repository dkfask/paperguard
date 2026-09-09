import { createRouter, createWebHistory } from 'vue-router'
import { auth } from './auth'
import Home from './views/Home.vue'
import Login from './views/Login.vue'
import Templates from './views/Templates.vue'
import TemplateEditor from './views/TemplateEditor.vue'
import Writer from './views/Writer.vue'
import Policies from './views/Policies.vue'
import Organizations from './views/Organizations.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Home, meta: { title: '首页' } },
    { path: '/login', component: Login, meta: { title: '登录' } },
    { path: '/templates', component: Templates, meta: { title: '模板管理' } },
    { path: '/templates/:id/edit', component: TemplateEditor, meta: { title: '编辑模板' } },
    { path: '/policies', component: Policies, meta: { title: '写作规范' } },
    { path: '/organizations', component: Organizations, meta: { title: '组织与作用域' } },
    { path: '/write/:id', component: Writer, meta: { title: '论文写作' } },
  ],
})

// 路由守卫: 未登录一律跳登录页
router.beforeEach((to) => {
  if (!auth.token && to.path !== '/login') {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (auth.token && to.path === '/login' && to.query.forceChange !== '1') {
    return { path: '/' }
  }
  const canManageTemplates = auth.user?.role === 'admin'
    || (auth.user?.role === 'teacher' && auth.user?.status === 'active')
  if (auth.token && to.path.startsWith('/templates/') && to.path.endsWith('/edit') && !canManageTemplates) {
    return { path: '/templates' }
  }
  if (auth.token && auth.user?.must_change_password && to.path !== '/login') {
    return { path: '/login', query: { forceChange: '1', redirect: to.fullPath } }
  }
})

export default router
