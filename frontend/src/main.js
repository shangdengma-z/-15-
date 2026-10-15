import { createApp } from 'vue'
import './theme.css'
import { installFetchAuth } from './api'
import App from './App.vue'

// 先包 fetch 再挂载：组件一上来就会发请求，CSRF/密钥头必须已经在位
installFetchAuth()
createApp(App).mount('#app')
