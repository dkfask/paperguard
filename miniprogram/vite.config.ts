import { defineConfig, type PluginOption } from 'vite'
import uniModule from '@dcloudio/vite-plugin-uni'

// The current alpha package exposes a CommonJS-shaped default when loaded by
// Vite's native ESM config runner.
const uni = ((uniModule as unknown as { default?: () => PluginOption }).default
  || (uniModule as unknown as () => PluginOption))

export default defineConfig({
  plugins: [uni()],
  server: {
    host: '127.0.0.1',
    port: 5174,
  },
})
