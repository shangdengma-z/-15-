<template>
  <div class="me">
    <div class="id">
      <span class="ava num">{{ initial }}</span>
      <div class="id-main">
        <template v-if="!editing">
          <div class="nick">
            <b>{{ account?.nickname || '没起昵称' }}</b>
            <button class="k" @click="startEdit">改名</button>
          </div>
        </template>
        <template v-else>
          <div class="nick-edit">
            <input v-model.trim="draft" maxlength="30" placeholder="显示名"
                   @keyup.enter="saveNick" @keyup.esc="editing = false" />
            <button class="btn ghost" :disabled="busy || !draft" @click="saveNick">保存</button>
            <button class="btn ghost" @click="editing = false">取消</button>
          </div>
        </template>
        <p class="mail">{{ account?.email }}</p>
        <p class="since">注册于 <span class="num">{{ account?.created_at || '—' }}</span> ·
          最近登录 <span class="num">{{ account?.last_login_at || '—' }}</span></p>
      </div>
    </div>

    <p v-if="msg" class="msg" :class="{ bad: !msgOk }">{{ msg }}</p>

    <section>
      <div class="eyebrow">今日额度</div>
      <div class="quota">
        <em class="num">{{ budgetLeft ?? '—' }}</em>
        <div class="bar"><i :style="{ width: quotaPct }"></i></div>
        <span>次可用 / 每日 {{ budgetTotal ?? '—' }} 次 · 一次体检约 180 次路线规划调用，额度按提交次数计</span>
      </div>
      <p v-if="budgetLeft === 0" class="warnline">今日次数已用完：明天自动恢复，
        或在「接入设置」勾上合成路网演示继续走流程（不烧真实配额）。</p>
    </section>

    <section>
      <div class="eyebrow">我的数据</div>
      <div class="grid">
        <div class="cell"><em class="num">{{ stats.communities || 0 }}</em><span>建档社区</span></div>
        <div class="cell"><em class="num">{{ stats.total_runs || 0 }}</em><span>体检次数</span></div>
        <div class="cell"><em class="num">{{ stats.avg_score ?? '—' }}</em><span>平均健康分</span></div>
        <div class="cell"><em class="num">{{ stats.api_calls || 0 }}</em><span>出网调用</span></div>
        <div class="cell"><em class="num">{{ stats.cache_hits || 0 }}</em><span>缓存命中</span></div>
        <div class="cell"><em class="num">{{ stats.agent_turns || 0 }}</em><span>智能体对话</span></div>
      </div>
      <p class="note">这些数字只算你名下的档案：缓存命中 {{ stats.cache_hits || 0 }} 次意味着
        同样多的请求没有再次出网，命中率 <b class="num">{{ hitPct }}</b>。
        别人的档案、密钥与用量你都看不到，反之也一样。</p>
    </section>

    <section>
      <div class="eyebrow">密钥与登录</div>
      <ul class="keys">
        <li><span>百度服务端 AK</span><em class="tag">{{ srcLabel(cfg.bmap?.ak_source) }}</em></li>
        <li><span>大模型 API Key</span><em class="tag">{{ srcLabel(cfg.llm?.key_source) }}</em></li>
        <li><span>会话</span><em class="tag">关闭浏览器需重新登录</em></li>
      </ul>
      <button class="btn ghost wide" @click="emit('goto', 'settings')">去接入设置</button>
    </section>

    <section class="danger">
      <div class="eyebrow">退出</div>
      <button class="btn" :disabled="busy" @click="signOut">
        {{ busy ? '处理中…' : '退出登录' }}</button>
      <p class="note">退出会同时删掉这台浏览器里存的 AK 与大模型 Key（服务端那份按你上次的选择处理：
        没勾"加密保存到本账户"的话就哪儿都不留），下次登录需要重新填一次。</p>
    </section>

    <details class="secbox">
      <summary>这个账号里到底存了什么</summary>
      <ul>
        <li>存：邮箱、<strong>argon2id 加盐哈希后的口令</strong>（不可逆，所以没有"忘记密码"，
          忘了只能重新注册）、昵称、会话令牌的 SHA-256、当日次数与限速计数。</li>
        <li>按需存：只有你勾了「加密保存到本账户」，AK / SK / 大模型 Key 才会以 Fernet 密文进你的账号行，
          取消勾选即刻删除；服务端日志与接口回显里永远只有掩码。</li>
        <li>不存：聊天以外的个人画像、精确选点之外的位置信息。档案表里只有坐标、名称与派生计算结果。</li>
        <li>会话 cookie 是浏览器会话级的：关掉浏览器即失效。浏览器一直开着（或开了"继续浏览上次的页面"）
          时会话会保留到 <code>SESSION_DAYS</code> 上限，想立刻结束请点退出登录。</li>
      </ul>
    </details>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { errorMessage, logout, updateNickname } from '../api'

const props = defineProps({
  account: { type: Object, default: null },
  budgetLeft: { type: Number, default: null },
  budgetTotal: { type: Number, default: null },
  stats: { type: Object, default: () => ({}) }
})
const emit = defineEmits(['signed-out', 'renamed', 'goto'])

const cfg = reactive({})
const editing = ref(false)
const draft = ref('')
const busy = ref(false)
const msg = ref('')
const msgOk = ref(true)

const SRC = { env: '来自 .env', runtime: '运行时设置', empty: '未配置',
              store: '已加密存入账户', browser: '只在本浏览器' }
const srcLabel = (s) => SRC[s] || '未配置'

const initial = computed(() => {
  const a = props.account
  const s = (a?.nickname || a?.email || '?').trim()
  return /[一-龥]/.test(s[0]) ? s[0] : s.slice(0, 1).toUpperCase()
})
const hitPct = computed(() => (props.stats.cache_hit_rate == null
  ? '—' : Math.round(props.stats.cache_hit_rate * 100) + '%'))
// 额度条：分母是服务端告知的当日上限，不让页面自己猜
const quotaPct = computed(() => {
  const { budgetLeft, budgetTotal } = props
  if (budgetLeft == null || !budgetTotal) return '0%'
  return Math.max(0, Math.min(100, Math.round((budgetLeft / budgetTotal) * 100))) + '%'
})

function startEdit() {
  draft.value = props.account?.nickname || ''
  editing.value = true
}

async function saveNick() {
  if (!draft.value || busy.value) return
  busy.value = true
  try {
    const res = await updateNickname(draft.value)
    if (!res.ok) { msg.value = await errorMessage(res); msgOk.value = false; return }
    emit('renamed', res.nickname)
    editing.value = false
    msg.value = '昵称已更新'; msgOk.value = true
  } catch (e) {
    msg.value = '请求失败：' + e.message; msgOk.value = false
  } finally {
    busy.value = false
  }
}

async function signOut() {
  busy.value = true
  try {
    await logout()
    emit('signed-out')
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try {
    Object.assign(cfg, await (await fetch('/api/config')).json())
  } catch (e) { /* 读不到就显示"未配置"，不打断这个面板 */ }
})
</script>

<style scoped>
.me { display: flex; flex-direction: column; gap: 15px; }
section { display: flex; flex-direction: column; gap: 8px; }
section + section { padding-top: 14px; border-top: 1px solid var(--line); }

.id { display: flex; align-items: flex-start; gap: 12px; }
.ava {
  width: 44px; height: 44px; flex: none; display: grid; place-items: center; border-radius: 11px;
  background: var(--accent); color: #fff; font-size: 18px; font-weight: 700;
}
.id-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.nick { display: flex; align-items: center; gap: 8px; }
.nick b { font-size: 14px; color: var(--ink); }
.nick-edit { display: flex; align-items: center; gap: 6px; }
.nick-edit input { flex: 1; min-width: 0; }
.mail { font-size: 11px; color: var(--muted); word-break: break-all; }
.since { font-size: 10.5px; color: var(--dim); }
.since .num { color: var(--muted); }

.quota { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.quota em { font-style: normal; font-size: 26px; font-weight: 700; color: var(--accent); line-height: 1; }
.quota .bar { flex: 1; min-width: 60px; height: 5px; border-radius: 3px; background: var(--surface-3); overflow: hidden; }
.quota .bar i { display: block; height: 100%; background: var(--accent); transition: width .2s; }
.quota span { font-size: 10px; color: var(--dim); width: 100%; }

.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.cell { display: flex; flex-direction: column; gap: 2px; padding: 9px 10px;
  border: 1px solid var(--line); border-radius: 9px; background: var(--surface-2); }
.cell em { font-style: normal; font-size: 16px; font-weight: 700; color: var(--ink); }
.cell span { font-size: 9.5px; color: var(--dim); }

.note { font-size: 10.5px; color: var(--dim); line-height: 1.7; }
.note b { color: var(--muted); font-family: var(--font-num); }
.warnline { font-size: 11px; color: var(--warn); line-height: 1.6; }
.msg { font-size: 11px; color: var(--good); }
.msg.bad { color: var(--bad); }

.keys { list-style: none; display: flex; flex-direction: column; gap: 6px; }
.keys li { display: flex; align-items: center; justify-content: space-between; gap: 10px;
  font-size: 11.5px; color: var(--muted); }
.tag { font-size: 9.5px; padding: 2px 7px; border-radius: 5px; border: 1px solid var(--line);
  font-family: var(--font-num); letter-spacing: .4px; background: var(--surface-2); color: var(--dim); }
.btn.ghost.wide { width: 100%; }
.danger .btn { background: #fff; color: var(--bad); border-color: #e0c2c4; }
.danger .btn:hover:not(:disabled) { background: #f9efef; border-color: var(--bad); }

.secbox { border: 1px dashed var(--line-2); border-radius: 9px; padding: 0 11px; }
.secbox summary { cursor: pointer; font-size: 11px; color: var(--muted); padding: 9px 0; list-style: none; }
.secbox summary::-webkit-details-marker { display: none; }
.secbox summary::before { content: '＋ '; color: var(--accent); }
.secbox[open] summary::before { content: '－ '; }
.secbox[open] summary { border-bottom: 1px dashed var(--line); }
.secbox ul { padding: 9px 0 11px 15px; font-size: 10.5px; color: var(--dim); line-height: 1.8; }
.secbox strong { color: var(--muted); }
.secbox code { font-size: 10px; color: var(--accent-2); font-family: ui-monospace, Consolas, monospace; }
</style>
