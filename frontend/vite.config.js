import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // 走代理而不是直连后端：多用户模式下会话 cookie 需要同源，
    // 想换后端端口用 VITE_PROXY_TARGET=http://localhost:8010 npm run dev
    proxy: { '/api': process.env.VITE_PROXY_TARGET || 'http://localhost:8000' }
  }
})
