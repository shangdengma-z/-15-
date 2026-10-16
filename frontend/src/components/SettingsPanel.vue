<template>
  <div class="set">
    <div v-if="isUser" class="block">
      <div class="eyebrow">当前账户</div>
      <div class="acct">
        <div class="a-main">
          <b>{{ cfg.account?.nickname || cfg.account?.email }}</b>
          <span>{{ cfg.account?.email }}</span>
        </div>
        <div class="a-num">
          <em class="num">{{ cfg.account?.budget_left ?? '-' }}</em>
          <span>今日剩余体检次数</span>
        </div>
      </div>
      <div class="acts">
        <button class="btn ghost" @click="signOut">退出登录</button>
        <button class="btn ghost bad" :disabled="busy" @click="clearAllKeys">
          清除本机与账户里的 Key</button>
      </div>
      <p class="note">档案、盲区记录与智能体对话都挂在这个账号名下，别人看不到；
        只有你点「复制链接」把某条报告设为公开时，它才可以被匿名打开。</p>
    </div>

    <div class="block">
      <div class="eyebrow">数据模式</div>
      <div class="mode">
        <div class="cur" :class="cfg.mode">
          <i></i><b>{{ cfg.mode === 'real' ? '应用模式 · 真实路网' : '演示模式 · 合成路网' }}</b>
          <span>{{ cfg.mode === 'real'
              ? `出网按 ${cfg.qps} QPS 令牌桶`
              : '零出网，可反复演示；结果不写入档案库' }}</span>
        </div>
        <button v-if="!isUser" class="btn" :disabled="busy" @click="toggleMode">
          {{ busy ? '切换中…' : (cfg.mode === 'real' ? '一键切到演示模式' : '一键切到应用模式') }}
        </button>
      </div>
      <label v-if="isUser" class="check">
        <input v-model="demo" type="checkbox" @change="applyDemo" />
        <span>用合成路网演示（不消耗你的配额、不写入档案）；打开地图与流程照常，跑完约 1 秒</span>
      </label>
      <p v-if="cfg.quota_exhausted" class="warnline">服务端今日路线规划配额已用完：
        勾上合成路网演示可继续走完整流程，或明天再跑真实数据。</p>

      <template v-if="!isUser">
        <div class="eyebrow">限流档位 <span class="tag" :class="tagCls(cfg.qps_source)">配额 {{
            SRC_LABEL[cfg.qps_source] || '来自 .env' }}</span></div>
        <div class="row2">
          <label>路线规划 QPS<input v-model="form.qps" type="number" min="1" max="100" step="1"
                                    :placeholder="String(cfg.qps)" /></label>
          <label>并发闸<input v-model="form.concurrency" type="number" min="1" max="32" step="1"
                              :placeholder="String(cfg.concurrency)" /></label>
        </div>
        <p class="note">当前进程 <b class="num">{{ cfg.qps }}</b> QPS / 并发 <b class="num">{{
            cfg.concurrency }}</b>。配额升档后不必重启后端，填新值点「校验并应用」即可重建令牌桶；
          若频繁出现 401，把并发降到 3。</p>
      </template>
    </div>

    <div v-if="isUser" class="block">
      <div class="eyebrow">Key 存在哪里</div>
      <div class="radios">
        <label class="radio">
          <input v-model="store" type="radio" :value="false" />
          <span><b>只存这台设备的浏览器</b><em>推荐</em><br />每次请求由你的浏览器带上，服务端不落库、不写日志</span>
        </label>
        <label class="radio">
          <input v-model="store" type="radio" :value="true" />
          <span><b>加密保存到本账户</b><em>换设备可用</em><br />用服务端主密钥（Fernet/AES-128-CBC+HMAC）加密后存进你的账号行，
            库里拿不到明文</span>
        </label>
      </div>
      <p class="note">当前状态：<span class="tag" :class="cfg.bmap.ak_source === 'store' ? 'rt' : 'env'">{{
          SRC_LABEL[cfg.bmap.ak_source] || '未配置' }}</span>
        选「加密保存到本账户」后，本浏览器里的那份会立即删掉，后续请求改用库里那份。</p>
      <p v-if="warnTakeBack" class="warnline">账户里已有密文而你现在选回到「只存本机」：
        保存后服务端会删掉那份密文（不会把明文回传给浏览器），需要你重新填一次 AK。</p>
    </div>

    <div class="block">
      <div class="eyebrow">百度地图 · 服务端 AK / SK</div>
      <p class="note"><strong>必须是「服务端类型」的应用 AK</strong>（控制台里类型显示"服务端"）。
        浏览器类型的 AK 打 REST 接口会返回 status=10/210 一类错误，本项目不会替你转换类型。
        若应用开了 SN 校验，请把 SK 一并填上；没开就留空。地点检索配额需 ≥ 本项目用量。</p>
      <label>AK <span class="tag" :class="tagCls(cfg.bmap.ak_source)">{{ SRC_LABEL[cfg.bmap.ak_source] }}</span></label>
      <input v-model="form.server_ak" type="password" autocomplete="off"
             :placeholder="cfg.bmap.ak_masked || '粘贴你的服务端 AK'" />
      <label>SK（可选，用于 SN 签名） <span class="tag" :class="tagCls(cfg.bmap.sk_source)">{{
          cfg.bmap.sk_set ? '已设置' : '未设置' }}</span></label>
      <input v-model="form.server_sk" type="password" autocomplete="off" placeholder="留空表示不修改" />
    </div>

    <div class="block">
      <div class="eyebrow">大模型 · OpenAI 兼容接口</div>
      <p class="note">留空则智能体自动回落为规则模式，其余功能不受影响。</p>
      <label>API Key <span class="tag" :class="tagCls(cfg.llm.key_source)">{{ SRC_LABEL[cfg.llm.key_source] }}</span></label>
      <input v-model="form.llm_api_key" type="password" autocomplete="off"
             :placeholder="cfg.llm.key_masked || '粘贴你的大模型 Key'" />
      <div class="row2">
        <label>Base URL<input v-model="form.llm_base_url" :placeholder="cfg.llm.base_url" /></label>
        <label>Model<input v-model="form.llm_model" :placeholder="cfg.llm.model" /></label>
      </div>
      <div class="acts">
        <button class="btn ghost" :disabled="busy" @click="testOnly">先测一下（不保存）</button>
        <button class="btn" :disabled="busy" @click="save">{{ isUser ? '校验并保存' : '校验并应用' }}</button>
        <button class="btn ghost" @click="clearForm">清空输入</button>
        <button class="btn ghost" @click="load">重新读取</button>
      </div>
      <p v-if="msg" class="msg" :class="{ bad: !msgOk }">{{ msg }}</p>
      <ul v-if="results.length" class="probes">
        <li v-for="r in results" :key="r.k" :class="{ bad: !r.ok }">
          <span class="ic">{{ r.ok ? '✓' : '✗' }}</span>{{ r.label }}：{{ r.detail }}
        </li>
      </ul>
      <p class="note">校验是<strong>真发一次请求</strong>：百度用 500m 圆形检索探测（status=0 才算过），
        大模型发一条 1 token 的 ping。测不过就不会写入，也不会改变模式。</p>
    </div>

    <details class="secbox">
      <summary>密钥会去哪里</summary>
      <ul>
        <li><strong>单机模式</strong>（后端 <code>AUTH_ENABLED=0</code>，也是本项目交付用的那条路径）：
          只写进当前后端进程的内存，不落盘、不写日志、接口只回掩码，进程重启回到 <code>.env</code> 的值。</li>
        <li><strong>多用户模式</strong>：默认只存在你这台设备的 <code>localStorage</code> 里，
          随请求头带给后端用完即弃；勾「加密保存到本账户」才会用 <code>MASTER_KEY</code> 加密入库，
          随时可以取消（取消即删除库里的密文）。</li>
        <li>两种模式下服务端对外都只回掩码（前 4 后 4），日志与档案里都不会出现 Key 明文。</li>
        <li>你在「接入设置」里填的 Key 只用于你自己的请求：AK 的配额与账单记在你的百度账号上；
          若本页显示「来自 .env」，说明部署者在服务端配了一份兜底 AK，那部分用量记在部署者账上。</li>
      </ul>
    </details>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { errorMessage, localCreds, logout, saveCreds } from '../api'

const API = import.meta.env.VITE_API_BASE || ''
const SRC_LABEL = {
  env: '来自 .env', runtime: '运行时设置', empty: '未配置',
  store: '已加密存入账户', browser: '只在本浏览器'
}

const cfg = reactive({ mode: 'real', mock: false, qps: 0, concurrency: 1, qps_source: 'env',
                       quota_exhausted: false, scope: 'process', account: null,
                       bmap: { ak_masked: '', ak_source: 'empty', sk_set: false, sk_source: 'empty' },
                       llm: { configured: false, model: '', base_url: '', key_masked: '', key_source: 'empty' } })
const form = reactive({ server_ak: '', server_sk: '', llm_api_key: '', llm_base_url: '', llm_model: '',
                        qps: '', concurrency: '' })
const busy = ref(false)
const msg = ref('')
const msgOk = ref(true)
const results = ref([])
// 多用户下密钥的去向与演示模式开关，都是"本机的选择"，所以直接反映到 api.js 的存储里
const store = ref(false)
const demo = ref(false)

const isUser = computed(() => cfg.scope === 'user')
// 从"账户里有密文"改回"只存本机"时必须提醒：删除是不可逆的，服务端不会反向导出明文
const warnTakeBack = computed(() => !store.value && cfg.bmap.ak_source === 'store')
const tagCls = (s) => ({ runtime: 'rt', env: 'env', store: 'rt', browser: 'env', empty: 'off' }[s] || 'off')

const emit = defineEmits(['changed', 'signed-out'])

async function load() {
  try {
    const res = await fetch(`${API}/api/config`)
    const data = await res.json()
    Object.assign(cfg, data)
    if (isUser.value) {
      const c = localCreds()
      store.value = c.store
      demo.value = c.demo
      // 密钥本来就在用户自己的浏览器里，回填成密码框的初值，改哪条都看得见
      form.server_ak = c.bmapAk
      form.server_sk = c.bmapSk
      form.llm_api_key = c.llmKey
      form.llm_base_url = c.llmBase
      form.llm_model = c.llmModel
    }
    msg.value = ''
  } catch (e) {
    msg.value = '读取失败：' + e.message; msgOk.value = false
  }
}

async function push(body) {
  busy.value = true
  try {
    const res = await fetch(`${API}/api/config`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    })
    const data = await res.json()
    if (!res.ok) { msg.value = await errorMessage(res); msgOk.value = false; return data }
    Object.assign(cfg, data)
    readProbes(data.verified || {})
    clearForm()
    msg.value = body.mode ? `已切换到${body.mode === 'mock' ? '演示模式' : '应用模式'}`
      : (isUser.value
          ? (data.stored ? '校验通过，已加密存入本账户' : '校验通过，Key 只留在这台设备的浏览器里')
          : '配置已生效（仅当前进程）')
    msgOk.value = true
    emit('changed', data)
  } catch (e) {
    msg.value = '请求失败：' + e.message; msgOk.value = false
  } finally {
    busy.value = false
  }
  return null
}

function toggleMode() {
  push({ mode: cfg.mode === 'real' ? 'mock' : 'real' })
}

/** 演示模式在多用户下是请求级的：写进本机存储，由 api.js 给每个请求补 X-Demo-Mode 头。 */
async function applyDemo() {
  saveCreds({ demo: demo.value })
  await load()
  emit('changed', { mock: demo.value, quota_exhausted: cfg.quota_exhausted })
  msg.value = demo.value ? '已切到合成路网：结果不入库、不出网' : '已切回真实路网'
  msgOk.value = true
}

function payloadFromForm() {
  const body = {}
  for (const [k, v] of Object.entries(form)) {
    const s = String(v).trim()
    if (!s) continue
    body[k] = (k === 'qps' || k === 'concurrency') ? Number(s) : s
  }
  return body
}

function readProbes(data) {
  results.value = []
  if (data.bmap) results.value.push({ k: 'bmap', ok: data.bmap.ok, label: '百度服务端 AK', detail: data.bmap.detail })
  if (data.llm) results.value.push({ k: 'llm', ok: data.llm.ok, label: '大模型接口', detail: data.llm.detail })
}

async function save() {
  const body = payloadFromForm()
  if (!isUser.value) {
    if (!Object.keys(body).length) { msg.value = '没有要保存的内容'; msgOk.value = true; return }
    await push(body)
    return
  }
  // 多用户：先把明文留在本机浏览器（默认去向），再按选择决定是否额外加密入库
  saveCreds({ bmapAk: body.server_ak || '', bmapSk: body.server_sk || '',
              llmKey: body.llm_api_key || '', llmBase: body.llm_base_url || '',
              llmModel: body.llm_model || '', store: store.value })
  body.store_keys = store.value
  const data = await push(body)
  if (data && store.value && (body.server_ak || body.llm_api_key)) {
    // 这次真的有 Key 进了账户，本机那份才可以删：否则"删了却哪儿都没有"会让人下次体检直接失败
    saveCreds({ bmapAk: '', bmapSk: '', llmKey: '', store: true })
    await load()
  }
}

async function testOnly() {
  const body = payloadFromForm()
  if (!body.server_ak && !body.llm_api_key) {
    msg.value = '先填要测试的 AK 或大模型 Key'; msgOk.value = false; return
  }
  busy.value = true
  results.value = []
  msg.value = ''
  try {
    const res = await fetch(`${API}/api/config/verify`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ server_ak: body.server_ak, server_sk: body.server_sk,
                             llm_api_key: body.llm_api_key,
                             llm_base_url: body.llm_base_url, llm_model: body.llm_model })
    })
    const data = await res.json()
    if (!res.ok) { msg.value = await errorMessage(res); msgOk.value = false; return }
    readProbes(data)
    msg.value = data.ok ? '校验通过，可点「校验并保存」写入' : '校验未通过，未做任何改动'
    msgOk.value = !!data.ok
  } catch (e) {
    msg.value = '请求失败：' + e.message; msgOk.value = false
  } finally {
    busy.value = false
  }
}

function clearForm() {
  for (const k of Object.keys(form)) form[k] = ''
}

/** 两条路一起清：本机的 localStorage，和服务端账户里那份密文。 */
async function clearAllKeys() {
  saveCreds({ bmapAk: '', bmapSk: '', llmKey: '', llmBase: '', llmModel: '', store: false })
  store.value = false
  await push({ server_ak: '', server_sk: '', llm_api_key: '', store_keys: false, verify: false })
  await load()
}

async function signOut() {
  await logout()
  emit('signed-out')
}

onMounted(load)
defineExpose({ load })
</script>

<style scoped>
.set { display: flex; flex-direction: column; gap: 15px; }
.block { display: flex; flex-direction: column; gap: 7px; }
.block + .block { padding-top: 14px; border-top: 1px solid var(--line); }
label { font-size: 10.5px; color: var(--muted); display: flex; align-items: center; gap: 6px; }
.row2 { display: grid; grid-template-columns: 1.4fr 1fr; gap: 8px; }
.row2 label { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.note { font-size: 10.5px; color: var(--dim); line-height: 1.65; }
.note strong { color: var(--accent); font-weight: 600; }
.note b { color: var(--ink); font-family: var(--font-num); }
.note code { font-size: 10px; color: var(--accent-2); font-family: ui-monospace, Consolas, monospace; }
.tag { font-size: 9px; padding: 2px 6px; border-radius: 5px; border: 1px solid var(--line);
  font-family: var(--font-num); letter-spacing: .5px; background: var(--surface-2); color: var(--dim); }
.tag.env { color: var(--accent-2); border-color: #c6d8e4; background: #eff5f9; }
.tag.rt { color: var(--accent); border-color: #b7cdc8; background: var(--accent-soft); }
.mode { display: flex; align-items: center; gap: 10px; }
.cur { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; padding: 9px 11px;
  border-radius: 9px; border: 1px solid var(--line); background: var(--surface-2); }
.cur b { font-size: 12.5px; color: var(--ink); }
.cur span { font-size: 10px; color: var(--dim); }
.cur.real { border-color: #bcd6c6; background: #eef6f0; }
.cur.mock { border-color: #e2d0b4; background: #f8f2e6; }
.mode .btn { width: auto; padding: 10px 14px; flex: none; }
.warnline { font-size: 11px; color: var(--warn); line-height: 1.6; }
.acts { display: flex; gap: 7px; flex-wrap: wrap; }
.msg { font-size: 11px; color: var(--good); }
.msg.bad { color: var(--bad); }
.probes { list-style: none; display: flex; flex-direction: column; gap: 4px; }
.probes li { font-size: 10.5px; color: var(--good); line-height: 1.6; display: flex; gap: 6px; }
.probes li.bad { color: var(--bad); }
.probes .ic { font-weight: 700; }
.secbox { border: 1px dashed var(--line-2); border-radius: 9px; padding: 0 11px; }
.secbox summary { cursor: pointer; font-size: 11px; color: var(--muted); padding: 9px 0; list-style: none; }
.secbox summary::-webkit-details-marker { display: none; }
.secbox summary::before { content: '＋ '; color: var(--accent); }
.secbox[open] summary::before { content: '－ '; }
.secbox[open] summary { border-bottom: 1px dashed var(--line); }
.secbox ul { padding: 9px 0 11px 15px; font-size: 10.5px; color: var(--dim); line-height: 1.8; }
.secbox strong { color: var(--muted); }
.secbox code { font-size: 10px; color: var(--accent-2); font-family: ui-monospace, Consolas, monospace; }

.acct { display: flex; align-items: center; gap: 12px; padding: 11px 13px;
  border: 1px solid var(--line); border-radius: 10px; background: var(--surface-2); }
.a-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.a-main b { font-size: 12.5px; color: var(--ink); }
.a-main span { font-size: 10px; color: var(--dim); word-break: break-all; }
.a-num { text-align: right; display: flex; flex-direction: column; gap: 1px; }
.a-num em { font-style: normal; font-size: 19px; font-weight: 700; color: var(--accent); }
.a-num span { font-size: 9.5px; color: var(--dim); }
.btn.ghost.bad { color: var(--bad); border-color: #e0c2c4; }
.btn.ghost.bad:hover:not(:disabled) { border-color: var(--bad); color: var(--bad); background: #f9efef; }

.check { display: flex; align-items: flex-start; gap: 8px; font-size: 11.5px; color: var(--muted);
  line-height: 1.6; cursor: pointer; }
.check input { margin-top: 2px; }
.radios { display: flex; flex-direction: column; gap: 8px; }
.radio { display: flex; align-items: flex-start; gap: 9px; padding: 10px 12px; cursor: pointer;
  border: 1px solid var(--line); border-radius: 10px; background: var(--surface);
  transition: border-color .15s, background .15s; }
.radio:hover { border-color: var(--line-2); }
.radio:has(input:checked) { border-color: var(--accent); background: var(--accent-soft); }
.radio input { margin-top: 2px; }
.radio span { font-size: 10.5px; color: var(--dim); line-height: 1.65; }
.radio b { font-size: 12px; color: var(--ink); font-weight: 600; }
.radio em { font-style: normal; font-size: 9px; margin-left: 6px; padding: 1px 6px; border-radius: 5px;
  background: var(--surface-2); color: var(--muted); border: 1px solid var(--line); }
</style>
