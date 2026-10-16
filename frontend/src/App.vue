<template>
  <AuthPanel v-if="gate" @done="onLogin" />

  <header v-if="!gate" class="top">
    <div class="brand">
      <span class="mark num">15′</span>
      <div class="txt">
        <h1>15 分钟生活圈 · 智能体检与规划助手</h1>
        <p>百度地图真实路网 · 可达圈测绘 · 服务盲区核验</p>
      </div>
    </div>
    <div class="pills">
      <span class="pill" :class="mockMode ? 'warn' : 'ok'"><i></i>{{ mockMode ? '演示合成路网' : '真实路网' }}</span>
      <span v-if="quotaOut" class="pill bad"><i></i>今日路线规划配额已用完</span>
      <span v-if="stats.communities" class="pill">{{ stats.communities }} 个社区已建档</span>
      <span v-if="account" class="pill cur">{{ account.nickname || account.email }}</span>
      <span v-if="result" class="pill cur">{{ resultLabel }} · <b class="num">{{ result.health_score }}</b> 分</span>
      <button v-if="runId" class="btn ghost" :class="{ done: linkCopied }" @click="copyLink"
              title="复制这条报告的直达链接，打开即从本机回放，不产生调用">
        {{ linkCopied ? '链接已复制' : '复制链接' }}</button>
    </div>
  </header>

  <div v-if="!gate" class="shell" :class="{ folded: !drawerOpen, 'hide-left': !consoleOpen }">
    <nav class="rail">
      <button v-for="it in navItems" :key="it.k" class="rbtn" :class="{ on: tab === it.k }"
              :title="it.n" @click="switchTab(it.k)">
        <svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true">
          <path :d="it.d" fill="none" stroke="currentColor" stroke-width="1.7"
                stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <em v-if="badge(it)" class="num">{{ badge(it) }}</em>
        <span>{{ it.n.slice(0, 2) }}</span>
      </button>
      <div class="rfoot" :class="mockMode ? 'warn' : 'ok'">{{ mockMode ? 'DEMO' : 'REAL' }}</div>
    </nav>

    <aside class="console">
      <div class="chead">
        <span class="eyebrow">选点与体检</span>
        <button class="fold" @click="consoleOpen = false" title="隐藏左栏，地图加宽">隐藏选点 ⟩</button>
      </div>
      <section>
        <div class="eyebrow">搜索地点</div>
        <input v-model="searchQ" placeholder="省 / 市 / 县或县级市 / 乡镇 / 门店名"
               @input="onSearchInput" @keyup.enter="doSearch" />
        <ul v-if="searchOpen" class="hits">
          <li v-for="(h, i) in searchHits" :key="i">
            <button @click="useHit(h)">
              <b>{{ h.name }}</b>
              <span>{{ [h.city, h.district, h.address].filter(Boolean).join(' · ') || '无详细地址' }}</span>
              <em :class="h.kind">{{ h.kind === 'region' ? `${h.level} · 区划中心` : '精确点位' }}</em>
            </button>
          </li>
        </ul>
        <p v-else-if="searching" class="tip">搜索中…</p>
        <p v-else-if="searchMiss" class="tip">没搜到匹配的区划或地点：换个更完整的名字，或直接看图选点。</p>
      </section>

      <section>
        <div class="eyebrow">选择社区</div>
        <div class="presets">
          <button v-for="p in presets" :key="p.id" class="preset" :class="{ on: isActive(p) }" @click="usePreset(p)">
            <b>{{ p.name }}</b><span>{{ p.archetype }}</span>
            <em v-if="histOf(p)" class="num">{{ histOf(p).score }}</em>
          </button>
        </div>
      </section>

      <section>
        <div class="eyebrow">坐标 · BD09LL</div>
        <div class="coords">
          <label>纬度<input v-model.number="lat" type="number" step="0.0001" @keyup.enter="lng && setCenterFromInput()" /></label>
          <label>经度<input v-model.number="lng" type="number" step="0.0001" @keyup.enter="lat && setCenterFromInput()" /></label>
        </div>
        <div v-if="center" class="picked">中心点 <code class="num">{{ center[0].toFixed(5) }}, {{ center[1].toFixed(5) }}</code></div>
        <button class="btn" :disabled="!center || running" @click="run">
          {{ running ? `测绘中 ${elapsed}s` : '开始体检' }}</button>
        <ol v-if="running || steps.length" class="hud">
          <li v-for="s in steps" :key="s.name" :class="s.state">
            <i>{{ s.state === 'done' ? '✓' : s.state === 'run' ? '▸' : '·' }}</i>{{ s.name }}
          </li>
        </ol>
        <p v-if="error" class="err">{{ error }}</p>
        <p v-else class="tip">图上单击选点、右键立即重跑；拖动中心标记也会重新测绘。</p>
      </section>
    </aside>

    <MapView class="stage" :center="center" :result="result" :actions="agentActions" :focus="focus"
             @pick="onPick" @run="runAt">
      <button v-if="!consoleOpen" class="unfold left" @click="consoleOpen = true">⟨ 选点</button>
      <button v-if="!drawerOpen" class="unfold" @click="drawerOpen = true">展开面板 ⟩</button>
    </MapView>

    <section class="drawer">
      <div class="dhead">
        <div class="dh-t">
          <h2>{{ VIEWS[tab][0] }}</h2>
          <p>{{ VIEWS[tab][1] }}</p>
        </div>
        <button class="fold" @click="drawerOpen = false" title="隐藏右侧面板，地图占满工作区">隐藏面板 ⟩</button>
      </div>
      <button v-if="agentNotice && tab === 'agent'" class="notice" @click="switchTab('report')">
        {{ agentNotice }}</button>
      <div class="dbody" :class="{ flush: tab === 'agent' }">
        <ReportPanel v-if="tab === 'report'" :result="result" @locate="setFocus" />
        <AdvisePanel v-if="tab === 'advise'" :center="center" :label="resultLabel"
                     @locate="setFocus" @actions="onAgentActions" />
        <AgentPanel v-if="tab === 'agent'" :center="center" @result="onAgentResult"
                    @actions="onAgentActions" @pick="onAgentPick" />
        <Archive v-if="tab === 'archive'" :history="history" :stats="stats" :compare="compareIds"
                 :comparison="comparison" @load="loadRun" @remove="removeRun" @toggle="toggleCompare" />
        <SettingsPanel v-if="tab === 'settings'" @changed="onConfigChanged" @signed-out="onSignedOut" />
        <AccountPanel v-if="tab === 'account'" :account="account" :stats="stats"
                      @renamed="onRenamed" @signed-out="onSignedOut" @goto="switchTab" />
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import AgentPanel from './components/AgentPanel.vue'
import AdvisePanel from './components/AdvisePanel.vue'
import Archive from './components/Archive.vue'
import AuthPanel from './components/AuthPanel.vue'
import AccountPanel from './components/AccountPanel.vue'
import MapView from './components/MapView.vue'
import ReportPanel from './components/ReportPanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import { UNAUTHORIZED, errorMessage, me } from './api'

const API = import.meta.env.VITE_API_BASE || ''

const NAV = [
  { k: 'report', n: '体检报告', d: 'M4 20V11M10 20V4M16 20v-6M2 20h20' },
  { k: 'advise', n: '选址建议', d: 'M12 21s7-5.4 7-11a7 7 0 10-14 0c0 5.6 7 11 7 11zM12 12.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5z' },
  { k: 'agent', n: 'AI 助手', d: 'M21 12a8 8 0 01-8 8H8l-4 3v-4.5A8 8 0 1121 12zM8.5 10.5h7M8.5 14h4' },
  { k: 'archive', n: '历史档案', d: 'M12 7.5V12l3 2M20.5 12a8.5 8.5 0 11-17 0 8.5 8.5 0 0117 0z' },
  { k: 'settings', n: '接入设置', d: 'M12 15.5a3.5 3.5 0 100-7 3.5 3.5 0 000 7zM19.4 13a7.6 7.6 0 000-2l1.8-1.4-1.7-3-2.1.9a7.7 7.7 0 00-1.7-1L15.3 6h-3.4l-.4 2.1a7.7 7.7 0 00-1.7 1L7.7 8.2 6 11.2'
      + ' 7.8 12.6a7.6 7.6 0 000 2L6 16l1.7 3 2.1-.9c.5.4 1.1.8 1.7 1l.4 2.1h3.4l.4-2.1c.6-.2 1.2-.6 1.7-1l2.1.9 1.7-3-1.8-1.4z' },
  // 单机模式没有账号，这一栏就不出现
  { k: 'account', n: '个人中心', onlyAuth: true,
    d: 'M12 12a4 4 0 100-8 4 4 0 000 8zM4.5 20.5a7.5 7.5 0 0115 0' }
]

const navItems = computed(() => NAV.filter(it => !it.onlyAuth || authOn.value))

const VIEWS = {
  report: ['生活圈体检报告', '健康分、可达圈、导则达标与已核验盲区'],
  advise: ['选址建议', '按人气代理、同类间距与补盲价值推荐候选点位'],
  agent: ['AI 规划助手', '大模型自主编排地图工具，结论直接回标到图上'],
  archive: ['历史档案', '本机 SQLite 记录，点一条即可 0 配额回放'],
  settings: ['接入设置', '填入你自己的百度 AK 与大模型 Key，并选择它们存在哪里'],
  account: ['个人中心', '账号、密钥去向与退出登录']
}

const lat = ref('')
const lng = ref('')
const center = ref(null)
const running = ref(false)
const elapsed = ref(0)
const error = ref('')
const result = ref(null)
const tab = ref('report')
const agentActions = ref([])
const mockMode = ref(false)
const quotaOut = ref(false)
const presets = ref([])
const history = ref([])
const stats = ref({})
const focus = ref(null)
const compareIds = ref([])
const comparison = ref(null)
const steps = ref([])
const agentNotice = ref('')
const activePreset = ref('')
const drawerOpen = ref(true)
const consoleOpen = ref(true)
const runId = ref('')
const linkCopied = ref(false)
const authOn = ref(false)
const account = ref(null)

// 单机模式（后端 AUTH_ENABLED=0）authOn 恒为 false，这一关直接放行，交付路径与录屏不受影响
const gate = computed(() => authOn.value && !account.value)

const resultLabel = computed(() => result.value?.label || '当前社区')

let hashBound = false

onMounted(async () => {
  // 会话过期是"任何请求都可能撞上"的事，所以由 fetch 包装统一广播，各组件不用各自处理
  window.addEventListener(UNAUTHORIZED, onSignedOut)
  const who = await me()
  authOn.value = !!who.auth_enabled
  account.value = who.user || null       // 必须先赋值再算 gate：反了就会把已登录的人挡在登录页
  if (gate.value) return          // 停在登录视图：没登录就不发业务请求，省得满屏 401
  await boot()
})

async function boot() {
  try {
    // 登录模式下要读 /api/config：/api/health 的 mock 是部署级的，
    // 不反映"我这个请求带了 X-Demo-Mode"，顶栏就会把演示结果标成真实路网
    const src = await (await fetch(`${API}/api/${authOn.value ? 'config' : 'health'}`)).json()
    mockMode.value = !!src.mock
    quotaOut.value = !!src.quota_exhausted
  } catch (e) { /* 后端未就绪时不显示状态条 */ }
  await refreshLists()
  await replayFromHash()
  if (!hashBound) {
    hashBound = true
    // 应用已打开时别人发来链接、或自己在地址栏改 hash，也要能直接回放
    window.addEventListener('hashchange', replayFromHash)
  }
}

async function onLogin(user) {
  account.value = user
  await refreshSession()
  await boot()
}

/** me() 是会话的唯一来源：登录、跑完一次体检、改名后都重新取一次 */
async function refreshSession() {
  const who = await me()
  if (who.user) account.value = who.user
}

function onRenamed(nickname) {
  account.value = { ...account.value, nickname }   // 顶栏那颗 pill 要立刻跟上
}

function onSignedOut() {
  account.value = null
  result.value = null
  history.value = []
  stats.value = {}
  runId.value = ''
  tab.value = 'report'    // 退出回首页：重新登录后落在体检报告，而不是停在个人中心
}

function switchTab(k) {
  tab.value = k
  if (k === 'report') agentNotice.value = ''
  drawerOpen.value = true   // 收起状态下点左栏任意视图必须把面板叫回来
}

function badge(it) {
  if (it.k === 'archive') return stats.value.communities || ''
  if (it.k === 'agent') return stats.value.agent_turns || ''
  if (it.k === 'report') return result.value ? '●' : ''
  return ''
}

async function refreshLists() {
  try {
    const [c, s] = await Promise.all([
      (await fetch(`${API}/api/communities`)).json(),
      (await fetch(`${API}/api/stats`)).json()])
    presets.value = c.presets || []
    history.value = c.history || []
    stats.value = s
  } catch (e) { /* 列表失败不影响主流程 */ }
}

function isActive(p) { return activePreset.value === p.id }
function histOf(p) { return history.value.find(h => h.label === p.name && h.mode !== 'mock') }
function usePreset(p) {
  activePreset.value = p.id
  center.value = [p.lat, p.lng]
  lat.value = p.lat
  lng.value = p.lng
  pickedLabel.value = ''
  pickedArchetype.value = ''
}
function onPick(c) {
  center.value = c
  lat.value = c[0]
  lng.value = c[1]
  activePreset.value = ''
  pickedLabel.value = ''      // 手动选点没有名字，别把上一次搜索的地名带进报告
  pickedArchetype.value = ''
}
function setCenterFromInput() {
  center.value = [lat.value, lng.value]
  activePreset.value = ''
  pickedLabel.value = ''
  pickedArchetype.value = ''
}
function setFocus(f) { focus.value = { ...f, t: Date.now() } }

// ---- 搜索定位：区划（到县与县级市）也能落到具体门店 ----
const searchQ = ref('')
const searchHits = ref([])
const searchOpen = ref(false)
const searching = ref(false)
const searchMiss = ref(false)
// 搜索结果带来的名字要跟着体检请求走，否则报告里只剩一串坐标
const pickedLabel = ref('')
const pickedArchetype = ref('')
let searchTimer = null

function onSearchInput() {
  clearTimeout(searchTimer)
  if (searchQ.value.trim().length < 2) {
    searchHits.value = []; searchOpen.value = false; searchMiss.value = false; return
  }
  searchTimer = setTimeout(doSearch, 350)   // 防抖：中文输入法逐字上屏时不要每个字烧一次配额
}

async function doSearch() {
  const q = searchQ.value.trim()
  if (q.length < 2) return
  searching.value = true
  searchMiss.value = false
  try {
    const res = await fetch(`${API}/api/search?q=${encodeURIComponent(q)}`)
    const d = await res.json().catch(() => ({}))
    if (!res.ok) { error.value = await errorMessage(res); searchHits.value = []; searchOpen.value = false; return }
    searchHits.value = d.results || []
    searchOpen.value = searchHits.value.length > 0
    searchMiss.value = !searchOpen.value
  } catch (e) {
    error.value = '搜索失败：' + e.message
  } finally {
    searching.value = false
  }
}

function useHit(h) {
  center.value = [h.lat, h.lng]
  lat.value = h.lat
  lng.value = h.lng
  activePreset.value = ''
  pickedLabel.value = h.name
  pickedArchetype.value = h.kind === 'region'
    ? `区划搜索（${h.level}，取区划中心点）`
    : `地点搜索（${[h.city, h.district].filter(Boolean).join('') || 'POI'}）`
  searchOpen.value = false
}

// 智能体产出的体检结果写进报告视图，但不把用户从对话框里拽走
function onAgentResult(res) {
  result.value = res
  agentNotice.value = `智能体已完成体检（${res.health_score} 分 · 盲区 ${res.blindspots.length} 处）→ 查看报告`
}
function onAgentActions(actions) { agentActions.value = actions }
function onAgentPick(c) { onPick(c) }

// 接入设置里切换模式后，顶栏标识与配额提示要跟上
async function onConfigChanged(data) {
  mockMode.value = !!data.mock
  quotaOut.value = !!data.quota_exhausted
  if (data.mock) result.value = null
  await refreshLists()
}

async function runAt(c) { onPick(c); await run() }

async function loadRun(h) {
  result.value = await (await fetch(`${API}/api/runs/${h.id}`)).json()
  runId.value = h.id
  center.value = [h.lat, h.lng]
  switchTab('report')
}
async function removeRun(id) {
  await fetch(`${API}/api/runs/${id}`, { method: 'DELETE' })
  compareIds.value = compareIds.value.filter(x => x !== id)
  comparison.value = null
  await refreshLists()
}
async function toggleCompare(id) {
  const i = compareIds.value.indexOf(id)
  compareIds.value = i >= 0 ? compareIds.value.filter(x => x !== id)
    : [...compareIds.value, id].slice(-2)
  comparison.value = compareIds.value.length === 2
    ? await (await fetch(`${API}/api/compare?ids=${compareIds.value.join(',')}`)).json()
    : null
}

// 分享链接：#run=<id> 打开即从本机 SQLite 回放，0 出网
async function replayFromHash() {
  const id = (location.hash.match(/run=([^&]+)/) || [])[1]
  if (!id) return
  try {
    const payload = await (await fetch(`${API}/api/runs/${encodeURIComponent(decodeURIComponent(id))}`)).json()
    if (!payload || !payload.center) return
    result.value = payload
    runId.value = id
    center.value = [payload.center.lat, payload.center.lng]
    lat.value = payload.center.lat
    lng.value = payload.center.lng
    switchTab('report')
  } catch (e) { /* 链接里的记录不存在时保持初始视图 */ }
}

async function copyLink() {
  if (!runId.value) return
  const url = `${location.origin}${location.pathname}#run=${encodeURIComponent(runId.value)}`
  window.history.replaceState(null, '', url)
  try {
    await navigator.clipboard.writeText(url)
  } catch (e) {
    const ta = document.createElement('textarea')
    ta.value = url
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    ta.remove()
  }
  linkCopied.value = true
  setTimeout(() => { linkCopied.value = false }, 2400)
}

const PIPELINE = ['逆地理编码定位社区', '18 条射线实测步行耗时', '四类设施检索与清洗',
                  '盲区网格初筛', '批量距离核验']

function markSteps(doneUpTo) {
  steps.value = PIPELINE.map((name, i) => ({
    name, state: i < doneUpTo ? 'done' : i === doneUpTo ? 'run' : 'wait'
  }))
}

async function run() {
  if (!center.value) return
  running.value = true
  error.value = ''
  result.value = null
  runId.value = ''
  agentActions.value = []
  elapsed.value = 0
  markSteps(1)
  drawerOpen.value = true
  try {
    const p = presets.value.find(x => x.id === activePreset.value)
    const res = await fetch(`${API}/api/analysis`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lat: center.value[0], lng: center.value[1],
        label: p?.name || pickedLabel.value, archetype: p?.archetype || pickedArchetype.value
      })
    })
    // 提交失败要说人话：429 是限速窗口，400 是没配 AK，不能让它掉进"任务超时"
    const fail = await errorMessage(res)
    if (fail) { error.value = fail; return }
    const { job_id: jobId } = await res.json()
    await poll(jobId)
  } catch (e) {
    error.value = '请求失败：' + e.message
  } finally {
    running.value = false
    steps.value = []
  }
}

async function poll(jobId) {
  for (let i = 0; i < 900; i++) {
    await new Promise(r => setTimeout(r, 1000))
    elapsed.value = i + 1
    markSteps(Math.min(1 + Math.floor(i / 4), PIPELINE.length))
    const job = await (await fetch(`${API}/api/analysis/${jobId}`)).json()
    if (job.status === 'done') {
      result.value = job.result
      runId.value = job.run_id || ''
      quotaOut.value = !!job.result?.meta?.quota_exhausted
      if (authOn.value) await refreshSession()
      await refreshLists()
      return
    }
    if (job.status === 'error') {
      error.value = job.quota_exhausted
        ? '今日路线规划配额已耗尽：请明天再跑，或切到演示模式继续演示'
        : job.error
      return
    }
  }
  error.value = '任务超时'
}
</script>

<style scoped>
.top {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  height: 56px; padding: 0 18px;
  background: var(--surface); border-bottom: 1px solid var(--line);
}
.brand { display: flex; align-items: center; gap: 11px; }
.mark {
  width: 34px; height: 34px; flex: none; display: grid; place-items: center;
  border-radius: 8px; font-size: 14px; font-weight: 700;
  background: var(--accent); color: #fff;
}
.brand h1 { font-size: 14.5px; }
.brand p { font-size: 10.5px; color: var(--dim); margin-top: 2px; }
.pills { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; justify-content: flex-end; }
.pill.cur { border-color: var(--line-2); color: var(--text); background: var(--surface-2); }
.pill.cur b { color: var(--accent); font-size: 14px; margin: 0 1px; }
.btn.ghost.done { border-color: #bcd6c6; color: var(--good); background: #eef6f0; }

/* 列宽走变量：两种隐藏（左栏 / 右面板）可以任意组合，不用穷举四种 grid 写法 */
.shell {
  --rail: 62px; --left: 288px; --right: 408px;
  display: grid;
  grid-template-columns: var(--rail) var(--left) minmax(0, 1fr) var(--right);
  gap: 10px; padding: 10px 14px 14px;
  height: calc(100vh - 56px);
  transition: grid-template-columns .22s ease;
}
.shell.folded { --right: 0px; }
.shell.hide-left { --left: 0px; }

/* 左导轨：实色，激活用细条 + 淡底 */
.rail {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  padding: 10px 6px; background: var(--surface);
  border: 1px solid var(--line); border-radius: var(--r);
}
.rbtn {
  position: relative; width: 100%; padding: 8px 2px 6px; border-radius: 9px;
  background: none; border: none; cursor: pointer; color: var(--dim);
  font-family: var(--font-num); font-size: 9.5px; letter-spacing: .6px; line-height: 1.3;
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  transition: color .15s, background .15s;
}
.rbtn:hover { color: var(--text); background: var(--surface-2); }
.rbtn.on { color: var(--accent); background: var(--accent-soft); }
.rbtn.on::before {
  content: ''; position: absolute; left: -6px; top: 50%; transform: translateY(-50%);
  width: 3px; height: 18px; border-radius: 2px; background: var(--accent);
}
.rbtn em {
  position: absolute; top: -4px; right: 2px; font-style: normal; font-size: 9px; font-weight: 700;
  min-width: 14px; padding: 1px 3px; border-radius: 7px; text-align: center;
  background: var(--accent); color: #fff;
}
.rfoot {
  margin-top: auto; padding: 5px 0; width: 100%; text-align: center;
  font-family: var(--font-num); font-size: 9px; letter-spacing: 1px; border-radius: 7px;
  background: #eef6f0; color: var(--good); border: 1px solid #bcd6c6;
}
.rfoot.warn { background: #f8f2e6; color: var(--warn); border-color: #e2d0b4; }

.console {
  display: flex; flex-direction: column; gap: 14px; padding: 13px; min-width: 0;
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r);
  overflow-y: auto; transition: opacity .18s ease, padding .22s ease, border-width .22s ease;
}
.shell.hide-left .console { opacity: 0; padding: 0; border-width: 0; pointer-events: none; overflow: hidden; }
.chead { display: flex; align-items: center; justify-content: space-between; gap: 8px;
  margin-bottom: -4px; }
.console section + section { padding-top: 13px; border-top: 1px dashed var(--line); }
.console .eyebrow { margin-bottom: 9px; }

.presets { display: flex; flex-direction: column; gap: 6px; margin-bottom: 13px; }
/* 搜索结果：名称 + 归属 + 精度标签，标签把"区划中心"和"精确点位"分开 */
.hits { list-style: none; display: flex; flex-direction: column; gap: 5px; margin-top: 9px; }
.hits button {
  position: relative; width: 100%; text-align: left; cursor: pointer;
  display: flex; flex-direction: column; align-items: flex-start; gap: 1px;
  padding: 8px 76px 8px 10px; border-radius: 8px; box-shadow: none;
  background: var(--surface-2); border: 1px solid var(--line); transition: border-color .15s;
}
.hits button:hover { border-color: var(--accent); }
.hits b { font-size: 12px; color: var(--ink); font-weight: 600; }
.hits span { font-size: 9.5px; color: var(--dim); max-width: 100%; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.hits em { position: absolute; right: 9px; top: 50%; transform: translateY(-50%);
  font-style: normal; font-size: 9px; padding: 2px 6px; border-radius: 5px;
  background: var(--surface); border: 1px solid var(--line-2); color: var(--muted); }
.hits em.poi { color: var(--good); border-color: #bcd6c6; background: #eef6f0; }
.preset {
  position: relative; width: 100%; text-align: left; cursor: pointer;
  display: flex; flex-direction: column; align-items: flex-start; gap: 1px;
  padding: 9px 42px 9px 11px; border-radius: 9px;
  background: var(--surface-2); border: 1px solid var(--line); color: var(--text);
  transition: border-color .15s, background .15s, transform .15s;
}
.preset b { font-size: 12.5px; font-weight: 600; }
.preset span { font-size: 10px; color: var(--dim); }
.preset em {
  position: absolute; right: 11px; top: 50%; transform: translateY(-50%);
  font-style: normal; font-size: 16px; font-weight: 700; color: var(--dim);
}
.preset:hover { transform: translateX(2px); border-color: var(--line-2); }
.preset.on { border-color: var(--accent); background: var(--accent-soft); }
.preset.on em { color: var(--accent); }

.coords { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 9px; }
.coords label { font-size: 10px; color: var(--muted); display: flex; flex-direction: column; gap: 4px; }
.picked { font-size: 10.5px; color: var(--dim); margin: -2px 0 9px; }
.picked code { color: var(--accent); font-size: 11.5px; }
.tip { font-size: 10.5px; color: var(--dim); line-height: 1.7; margin-top: 9px; }
.err { color: var(--bad); font-size: 11px; margin-top: 9px; line-height: 1.6; }

.hud { list-style: none; margin-top: 11px; font-size: 11px; line-height: 1.95; color: var(--dim); }
.hud li { display: flex; align-items: center; gap: 7px; }
.hud li.done { color: var(--good); }
.hud li.run { color: var(--accent); font-weight: 600; }
.hud li.run i { animation: spin 1.4s linear infinite; }
.hud i { font-style: normal; width: 11px; display: inline-block; }

.stage { position: relative; border-radius: var(--r); overflow: hidden;
  border: 1px solid var(--line); min-height: 0; }
.unfold {
  position: absolute; right: 12px; top: 12px; z-index: 12; width: auto;
  padding: 7px 12px; font-size: 11.5px; border-radius: 8px; cursor: pointer;
  background: var(--surface); border: 1px solid var(--line-2); color: var(--muted);
  box-shadow: var(--shadow); transition: border-color .15s, color .15s;
}
.unfold.left { left: 12px; right: auto; }
.unfold:hover { border-color: var(--accent); color: var(--accent); }

.drawer {
  display: flex; flex-direction: column; min-width: 0; min-height: 0;
  padding: 13px 14px; background: var(--surface);
  border: 1px solid var(--line); border-radius: var(--r);
  transition: opacity .18s ease, padding .22s ease, border-width .22s ease;
}
.shell.folded .drawer { opacity: 0; padding: 0; border-width: 0; pointer-events: none; overflow: hidden; }
.dhead { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 12px; }
.dh-t { flex: 1; min-width: 0; }
.dhead h2 { font-size: 14px; }
.dhead p { font-size: 10.5px; color: var(--dim); margin-top: 3px; line-height: 1.5; }
.fold {
  flex: none; width: auto; padding: 6px 10px; font-size: 11px; border-radius: 8px; cursor: pointer;
  background: var(--surface-2); border: 1px solid var(--line); color: var(--muted); box-shadow: none;
}
.fold:hover { border-color: var(--accent); color: var(--accent); }
.notice {
  width: 100%; margin: -2px 0 10px; padding: 9px 11px; font-size: 11px; text-align: left;
  border-radius: 9px; line-height: 1.5; cursor: pointer;
  background: var(--accent-soft); color: var(--accent); border: 1px dashed var(--accent-2);
}
.dbody { flex: 1; min-height: 0; overflow-y: auto; padding-right: 3px; }
.dbody.flush { overflow: hidden; display: flex; flex-direction: column; }

@media (max-width: 1400px) {
  .shell { --rail: 58px; --left: 262px; --right: 372px; }
}
@media (max-width: 1160px) {
  .brand p { display: none; }
  .shell { --rail: 54px; --left: 238px; --right: 330px; gap: 8px; padding: 8px 10px 10px; }
}
@media print {
  .top, .rail, .console, .stage, .fold, .unfold, .notice { display: none !important; }
  .shell { display: block; height: auto; padding: 0; }
  .drawer, .console { opacity: 1; padding: 0; border: none; }
  .console { display: none; }
}
</style>
