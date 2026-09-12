import { createRouter, createWebHistory } from 'vue-router'
import { auth } from './auth'
import { canAccessRoute } from './shared/auth/permissions'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('./views/Home.vue'), meta: { title: '首页', titleKey: 'route.home', requiresAuth: true } },
    { path: '/login', component: () => import('./views/Login.vue'), meta: { title: '登录', titleKey: 'route.login', public: true } },
    { path: '/templates', component: () => import('./views/Templates.vue'), meta: { title: '模板管理', titleKey: 'route.templates' } },
    { path: '/templates/:id/edit', component: () => import('./views/TemplateEditor.vue'), meta: { title: '编辑模板', titleKey: 'route.templateEditor' } },
    { path: '/document-packages/publish', component: () => import('./views/DocumentPackagePublisher.vue'), meta: { title: '发布通用文档', titleKey: 'route.documentPackagePublisher', roles: ['admin', 'college_admin', 'teacher'] } },
    { path: '/document-projects', component: () => import('./views/DocumentProjects.vue'), meta: { title: '通用文档', titleKey: 'route.documentProjects' } },
    { path: '/document-projects/:id', component: () => import('./views/DocumentProjectEditor.vue'), meta: { title: '文档编辑', titleKey: 'route.documentProjectEditor' } },
    { path: '/policies', component: () => import('./views/Policies.vue'), meta: { title: '写作规范', titleKey: 'route.policies' } },
    { path: '/organizations', component: () => import('./views/Organizations.vue'), meta: { title: '组织与作用域', titleKey: 'route.organizations' } },
    { path: '/workflow', component: () => import('./views/Workflow.vue'), meta: { title: '论文业务闭环', titleKey: 'route.workflow' }, beforeEnter: (to) => to.query.panel === 'audit' ? { path: '/audit' } : true },
    { path: '/audit', component: () => import('./views/Audit.vue'), meta: { title: '审计记录', titleKey: 'route.audit' } },
    { path: '/cohorts', component: () => import('./views/Cohorts.vue'), meta: { title: '批次与阶段配置', titleKey: 'route.cohorts', roles: ['admin', 'college_admin'] } },
    { path: '/admin/users', component: () => import('./views/AdminUsers.vue'), meta: { title: '用户与认证', titleKey: 'route.adminUsers', roles: ['admin'] } },
    { path: '/admin/ai', component: () => import('./views/AiSettings.vue'), meta: { title: 'AI 与系统设置', titleKey: 'route.adminAi', roles: ['admin'] } },
    { path: '/teacher/students', component: () => import('./views/TeacherStudents.vue'), meta: { title: '指导学生', titleKey: 'route.teacherStudents', roles: ['teacher'] } },
    { path: '/workspace', component: () => import('./views/Workspace.vue'), meta: { title: '角色工作台', titleKey: 'route.workspace' } },
    { path: '/operations', component: () => import('./views/Operations.vue'), meta: { title: '运行状态与数据治理', titleKey: 'route.operations' } },
    { path: '/write/:id', component: () => import('./views/Writer.vue'), meta: { title: '论文写作', titleKey: 'route.writer' } },
    { path: '/:pathMatch(.*)*', component: () => import('./views/NotFound.vue'), meta: { title: '页面不存在', titleKey: 'route.notFound', public: true } },
  ],
})

// 路由守卫: 未登录一律跳登录页
router.beforeEach(async (to) => {
  await auth.restore()
  document.title = to.meta.title ? `${to.meta.title} · PaperGuard` : 'PaperGuard'

  if (!auth.token && !to.meta.public) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (auth.token && to.path === '/login' && to.query.forceChange !== '1' && to.query.logout !== '1') {
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
  if (auth.token && !canAccessRoute(to, auth.user)) return { path: '/workspace' }
})

export default router
