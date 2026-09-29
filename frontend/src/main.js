import { createApp } from 'vue'
import { VueQueryPlugin } from '@tanstack/vue-query'
import './style.css'
import App from './App.vue'
import router from './router'
import { pinia } from './app/pinia'
import { queryClient } from './app/query'
import { applyIconFontFallback } from './shared/iconFont'

applyIconFontFallback()

const app = createApp(App)

app.use(pinia)
app.use(router)
app.use(VueQueryPlugin, { queryClient })

app.config.errorHandler = (error, instance, info) => {
  console.error('[PaperGuard] application error', { error, instance, info })
}

app.mount('#app')
