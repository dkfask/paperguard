import { createRouter, createWebHistory } from 'vue-router'
import Home from './views/Home.vue'
import Templates from './views/Templates.vue'
import TemplateEditor from './views/TemplateEditor.vue'
import Writer from './views/Writer.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Home, meta: { title: '首页' } },
    { path: '/templates', component: Templates, meta: { title: '模板管理' } },
    { path: '/templates/:id/edit', component: TemplateEditor, meta: { title: '编辑模板' } },
    { path: '/write/:id', component: Writer, meta: { title: '论文写作' } },
  ],
})

export default router
