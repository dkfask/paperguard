import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Icons from 'unplugin-icons/vite'
import { FileSystemIconLoader } from 'unplugin-icons/loaders'

export default defineConfig({
  plugins: [
    vue(),
    Icons({
      compiler: 'vue3',
      autoInstall: true,
      // 支持多个图标集
      customCollections: {
        // 可选：自定义 SVG 图标目录
        custom: FileSystemIconLoader('./src/assets/icons'),
      },
    }),
  ],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: 'tiptap-editor',
              test: /node_modules[\\/](@tiptap|prosemirror-|orderedmap|rope-sequence|w3c-keyname|linkifyjs)/,
            },
          ],
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    exclude: ['**/node_modules/**', '**/dist/**', '**/e2e/**'],
  },
  server: {
    port: 5173,
    proxy: {
      // 开发时把 /api 和私有图片转发到 FastAPI 后端
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
      '/media': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
