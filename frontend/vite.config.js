import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // 走代理而不是直连后端：多用户模式下会话 cookie 需要同源，
    // 想换后端端口用 VITE_PROXY_TARGET=http://localhost:8010 npm run dev
    // /env.js 也要代理：否则 dev 服务器会拿 SPA 的 index.html 兜底，
    // 页面里那个 <script src="/env.js"> 收到的是 HTML，浏览器直接报解析错，
    // 地图密钥只能退回编译期烘进包里的旧值（换 AK 必须重启 vite 才生效，很难查）
    proxy: {
      '/api': process.env.VITE_PROXY_TARGET || 'http://localhost:8000',
      '/env.js': process.env.VITE_PROXY_TARGET || 'http://localhost:8000'
    }
  }
})
