<template>
  <div class="wrap">
    <div class="card">
      <div class="side">
        <span class="mark num">15′</span>
        <h2>15 分钟生活圈 · 智能体检</h2>
        <p class="sub">真实路网等时圈测绘 · 服务盲区核验 · 选址建议</p>
        <ul class="why">
          <li><b>各人用自己的 Key</b>：百度 AK 与大模型 Key 默认只存在你这台设备的浏览器里，
            服务端不留副本，跑出来的配额记在你名下。</li>
          <li><b>档案互相隔离</b>：历史体检、盲区记录、智能体对话按账号分目录，
            别人看不到也删不掉；想让某条报告公开可访问，要你自己点"分享"。</li>
          <li><b>有每日额度</b>：一次体检约 180 次路线规划调用，服务端按账号限当日次数，
            避免一个页面被刷爆整个账号的 1 万次/天配额。</li>
        </ul>
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
          口令用 argon2id 加盐哈希后存储，服务端存不下你的明文口令，也没有"找回"通道——
          忘了只能重新注册。
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
.why { list-style: none; display: flex; flex-direction: column; gap: 11px; margin-top: 6px; }
.why li { font-size: 11.5px; line-height: 1.75; color: var(--muted);
  padding-left: 12px; border-left: 2px solid var(--line-2); }
.why b { color: var(--ink); font-weight: 600; }

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
