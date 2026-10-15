<template>
  <div class="set">
    <div class="block">
      <div class="eyebrow">数据模式</div>
      <div class="mode">
        <div class="cur" :class="cfg.mode">
          <i></i><b>{{ cfg.mode === 'real' ? '应用模式 · 真实路网' : '演示模式 · 合成路网' }}</b>
          <span>{{ cfg.mode === 'real' ? `出网按 ${cfg.qps} QPS 令牌桶` : '零出网，可反复演示；结果不写入本机档案' }}</span>
        </div>
        <button class="btn" :disabled="busy" @click="toggleMode">
          {{ busy ? '切换中…' : (cfg.mode === 'real' ? '一键切到演示模式' : '一键切到应用模式') }}
        </button>
      </div>
      <p v-if="cfg.quota_exhausted" class="warnline">今日路线规划配额已用完：切到演示模式可继续完整演示，
        或填入另一个 AK 后继续。</p>

      <div class="eyebrow">限流档位 <span class="tag" :class="tagCls(cfg.qps_source)">配额 {{
          SRC_LABEL[cfg.qps_source] || '来自 .env' }}</span></div>
      <div class="row2">
        <label>路线规划 QPS<input v-model="form.qps" type="number" min="1" max="100" step="1"
                                  :placeholder="String(cfg.qps)" /></label>
        <label>并发闸<input v-model="form.concurrency" type="number" min="1" max="32" step="1"
                            :placeholder="String(cfg.concurrency)" /></label>
      </div>
      <p class="note">当前进程 <b class="num">{{ cfg.qps }}</b> QPS / 并发 <b class="num">{{
          cfg.concurrency }}</b>。配额升档后不必重启后端，填新值点「应用配置」即可重建令牌桶；
        若频繁出现 401，把并发降到 3。</p>
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
        <button class="btn" :disabled="busy" @click="save">校验并应用</button>
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
        大模型发一条 1 token 的 ping。测不过就不会写入，也不会切换模式。</p>
    </div>

    <details class="secbox">
      <summary>密钥会去哪里（以及为什么没有登录）</summary>
      <ul>
        <li>只写进<strong>当前后端进程的内存</strong>：不落盘、不写日志、接口只回掩码（前 4 后 4），
          进程重启就回到 <code>.env</code> 的值。</li>
        <li>本工具默认 <code>127.0.0.1</code> 本机单用户运行，所以没有做账号体系：
          多用户登录会带来密码存储、会话、越权与审计成本，对"社区规划人员填一次自己的 key"这件事并不划算。</li>
        <li>如果你要把它放到公网给多人用，<strong>不要在公网实例里填真实密钥</strong>；
          正确做法是反代加 BasicAuth/访问口令，或按 <code>docker-compose</code> 用 <code>.env</code> 注入密钥并只在内网开放端口。</li>
        <li>需要更轻的门槛时，可以加一个"访问口令"（单串口令 + 哈希比较，约 30 行），
          比完整登录系统便宜得多；这一步我留着没做，等你决定。</li>
      </ul>
    </details>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'

const API = import.meta.env.VITE_API_BASE || ''
const SRC_LABEL = { env: '来自 .env', runtime: '运行时设置', empty: '未配置' }

const cfg = reactive({ mode: 'real', mock: false, qps: 0, concurrency: 1, qps_source: 'env',
                       quota_exhausted: false,
                       bmap: { ak_masked: '', ak_source: 'empty', sk_set: false, sk_source: 'empty' },
                       llm: { configured: false, model: '', base_url: '', key_masked: '', key_source: 'empty' } })
const form = reactive({ server_ak: '', server_sk: '', llm_api_key: '', llm_base_url: '', llm_model: '',
                        qps: '', concurrency: '' })
const busy = ref(false)
const msg = ref('')
const msgOk = ref(true)
const results = ref([])

const tagCls = (s) => ({ runtime: 'rt', env: 'env', empty: 'off' }[s] || 'off')

const emit = defineEmits(['changed'])

async function load() {
  try {
    Object.assign(cfg, await (await fetch(`${API}/api/config`)).json())
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
    if (!res.ok) { msg.value = data.detail || `请求失败（${res.status}）`; msgOk.value = false; return }
    Object.assign(cfg, data)
    readProbes(data.verified || {})
    clearForm()
    msg.value = body.mode ? `已切换到${body.mode === 'mock' ? '演示模式' : '应用模式'}` : '配置已生效（仅当前进程）'
    msgOk.value = true
    emit('changed', data)
  } catch (e) {
    msg.value = '请求失败：' + e.message; msgOk.value = false
  } finally {
    busy.value = false
  }
}

function toggleMode() {
  push({ mode: cfg.mode === 'real' ? 'mock' : 'real' })
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

function save() {
  const body = payloadFromForm()
  if (!Object.keys(body).length) { msg.value = '没有要保存的内容'; msgOk.value = true; return }
  push(body)
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
    if (!res.ok) { msg.value = data.detail || `请求失败（${res.status}）`; msgOk.value = false; return }
    readProbes(data)
    msg.value = data.ok ? '校验通过，可点「校验并应用」写入当前进程' : '校验未通过，未做任何改动'
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
</style>
