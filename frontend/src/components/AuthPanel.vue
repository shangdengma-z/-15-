<template>
  <div class="wrap">
    <div class="card">
      <div class="side">
        <span class="mark num">15′</span>
        <h2>15 分钟生活圈 · 智能体检</h2>
        <p class="sub">真实路网等时圈测绘 · 服务盲区核验 · 选址建议</p>
        <p class="intro">用百度地图真实路网把「15 分钟生活圈」画出来：一次体检给出 5/10/15 分钟
          步行可达圈、四类民生设施覆盖、连片服务盲区与导则达标对照，AI 助手可在此基础上给选址建议。
          你自己的 AK 与 Key 只存在这台设备上，配额记在你名下，档案按账号彼此隔离。</p>
      </div>

      <form class="form" @submit.prevent="submit">
        <div class="tabs">
          <button type="button" :class="{ on: mode === 'login' }" @click="mode = 'login'">登录</button>
          <button type="button" :class="{ on: mode === 'register' }" @click="mode = 'register'">注册</button>
        </div>

        <label>邮箱
          <input v-model.trim="email" type="email" autocomplete="username" required
                 placeholder="you@example.com" maxlength="190" />
        </label>
        <label v-if="mode === 'register'">昵称（可选，只用于顶栏显示）
          <input v-model.trim="nickname" type="text" maxlength="30" placeholder="规划科 · 小王" />
        </label>
        <label>口令
          <input v-model="password" :type="peek ? 'text' : 'password'"
                 :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
                 required minlength="8" maxlength="128" placeholder="至少 8 位" />
          <button v-if="mode === 'login'" type="button" class="peek" @click="peek = !peek">
            {{ peek ? '隐藏' : '显示' }}</button>
        </label>
        <p v-if="mode === 'register'" class="tip">
          口令用 argon2id 加盐哈希后存储，没有"找回"通道—— 忘了只能重新注册。
        </p>

        <p v-if="err" class="err">{{ err }}</p>
        <button class="btn" type="submit" :disabled="busy">
          {{ busy ? '处理中…' : (mode === 'login' ? '进入工作台' : '注册并进入') }}
        </button>
      </form>
    </div>
    <p class="foot">数据源：百度地图开放平台路网与 POI 检索 · 导则依据 GB 50180-2018 与上海市 15 分钟社区生活圈导则</p>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { login, register } from '../api'

const emit = defineEmits(['done'])
const mode = ref('login')
const email = ref('')
const password = ref('')
const nickname = ref('')
const peek = ref(false)
const busy = ref(false)
const err = ref('')

async function submit() {
  if (busy.value) return
  err.value = ''
  if (password.value.length < 8) { err.value = '口令至少 8 位'; return }
  busy.value = true
  try {
    const res = mode.value === 'login'
      ? await login(email.value, password.value)
      : await register(email.value, password.value, nickname.value)
    if (!res.ok) {
      // 401 是"邮箱或口令不正确"，后端刻意不区分哪一种，这里照原话显示
      err.value = res.detail || (res.status === 429 ? '尝试次数过多，请 10 分钟后再试' : '请求失败，请稍后再试')
      return
    }
    emit('done', mode.value === 'login' ? res : { id: res.id, email: res.email, nickname: nickname.value })
  } catch (e) {
    err.value = '连不上服务：' + e.message
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.wrap {
  min-height: 100vh; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 16px; padding: 26px 18px;
}
.card {
  width: min(880px, 100%); display: grid; grid-template-columns: 1.05fr 1fr;
  background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
  box-shadow: var(--shadow); overflow: hidden; animation: rise .3s ease;
}
.side {
  padding: 26px 24px; background: var(--surface-2);
  border-right: 1px solid var(--line); display: flex; flex-direction: column; gap: 12px;
}
.mark {
  width: 38px; height: 38px; display: grid; place-items: center; border-radius: 9px;
  background: var(--accent); color: #fff; font-size: 15px; font-weight: 700;
}
.side h2 { font-size: 16px; }
.sub { font-size: 11px; color: var(--dim); margin-top: 3px; }
.intro { font-size: 11.5px; color: var(--muted); line-height: 1.85; margin-top: 4px; }

.form { padding: 26px 24px; display: flex; flex-direction: column; gap: 12px; }
.tabs { display: flex; gap: 6px; padding: 4px; border-radius: 10px; background: var(--surface-2); }
.tabs button {
  flex: 1; padding: 8px 0; border: none; border-radius: 8px; cursor: pointer;
  background: none; color: var(--muted); font-size: 12.5px; font-weight: 600;
  font-family: var(--font-body); transition: background .15s, color .15s;
}
.tabs button.on { background: var(--surface); color: var(--accent); box-shadow: var(--shadow); }
.form label {
  position: relative; font-size: 10.5px; color: var(--muted);
  display: flex; flex-direction: column; gap: 5px;
}
.form input { padding-right: 52px; }
.peek {
  position: absolute; right: 6px; bottom: 6px; width: auto; padding: 4px 8px;
  font-size: 10.5px; border: 1px solid var(--line); border-radius: 6px; cursor: pointer;
  background: var(--surface-2); color: var(--muted); box-shadow: none;
}
.peek:hover { color: var(--accent); border-color: var(--accent); }
.tip { font-size: 10.5px; color: var(--dim); line-height: 1.7; }
.err { font-size: 11.5px; color: var(--bad); line-height: 1.6; }
.foot { font-size: 10.5px; color: var(--dim); text-align: center; line-height: 1.7; }

@media (max-width: 760px) {
  .card { grid-template-columns: 1fr; }
  .side { border-right: none; border-bottom: 1px solid var(--line); }
}
</style>
