<template>
  <header class="top print-hide">
    <div class="brand">
      <span class="logo">15′</span>
      <div>
        <h1>15 分钟生活圈 · 智能体检与规划助手</h1>
        <p>百度地图开放能力 · 真实路网等时圈 · 服务盲区核验 · 时间与人群双轴情景</p>
      </div>
    </div>
    <div class="pills">
      <span class="pill" :class="mockMode ? 'warn' : 'ok'"><i></i>{{ mockMode ? 'Mock 合成路网' : '真实路网' }}</span>
      <span v-if="quotaOut" class="pill bad"><i></i>步行日配额耗尽 · 只读缓存</span>
      <span v-if="stats.communities" class="pill"><i></i>{{ stats.communities }} 社区 ·
        出网 {{ stats.api_calls }} 次 · 命中率 {{ hitRate }}</span>
      <span v-if="result" class="pill"><i></i>{{ result.label || '当前社区' }} · {{ result.health_score }} 分</span>
    </div>
  </header>

  <div class="shell">
    <NavRail class="railslot print-hide" :items="NAV" :model="tab" :mock-mode="mockMode"
             :stats="stats" :has-result="!!result" @pick="switchTab" />

    <aside class="col left print-hide">
      <div class="card">
        <div class="card-title">选择社区</div>
        <div class="presets">
          <button v-for="p in presets" :key="p.id" class="preset" :class="{ on: isActive(p) }"
                  @click="usePreset(p)">
            <b>{{ p.name }}</b><span>{{ p.archetype }}</span>
          </button>
        </div>
        <div class="coords">
          <label>纬度<input v-model.number="lat" type="number" step="0.0001"
                            @keyup.enter="lng && setCenterFromInput()" /></label>
          <label>经度<input v-model.number="lng" type="number" step="0.0001"
                            @keyup.enter="lat && setCenterFromInput()" /></label>
        </div>
        <div v-if="center" class="picked">已选中心 <code>{{ center[0].toFixed(5) }}, {{ center[1].toFixed(5) }}</code></div>
        <button class="btn" :disabled="!center || running" @click="run">
          {{ running ? `计算中 ${elapsed}s…` : '开始体检' }}</button>
        <ol v-if="running || steps.length" class="hud">
          <li v-for="s in steps" :key="s.name" :class="s.state">
            <i>{{ s.state === 'done' ? '✓' : s.state === 'run' ? '▸' : '·' }}</i>{{ s.name }}
          </li>
        </ol>
        <p class="tip">首次约 40~60s（步行接口 3 QPS 是瓶颈）；缓存命中后 0.7s 返回、几乎 0 出网。
          图上右键或拖拽中心点可直接重跑。</p>
        <div v-if="error" class="err">{{ error }}</div>
      </div>

      <div v-if="result" class="card">
        <div class="card-title">本轮计量</div>
        <div class="tiles">
          <div class="tile"><b>{{ result.meta.api_calls }}</b><span>出网请求</span></div>
          <div class="tile"><b>{{ result.meta.quota_units }}</b><span>配额单位</span></div>
          <div class="tile"><b>{{ result.meta.cache_hits }}</b><span>缓存命中</span></div>
          <div class="tile"><b>{{ result.meta.elapsed_sec }}s</b><span>计算耗时</span></div>
        </div>
      </div>
    </aside>

    <MapView class="stage" :center="center" :result="result" :actions="agentActions" :focus="focus"
             :overlay-ring="scenarioRing" @pick="onPick" @run="runAt" />

    <section class="col right">
      <div class="drawer card">
        <div class="dhead">
          <div>
            <h2>{{ viewTitle }}</h2>
            <p>{{ viewHint }}</p>
          </div>
          <div class="dacts" v-if="tab === 'report'">
            <button v-if="runId" class="btn ghost mini" @click="exportMd">导出报告</button>
            <button class="btn ghost mini" @click="print">打印</button>
          </div>
        </div>
        <button v-if="agentNotice && tab === 'agent'" class="notice print-hide" @click="switchTab('report')">
          {{ agentNotice }}</button>
        <div class="dbody" :class="{ flush: tab === 'agent' }">
          <ReportPanel v-if="tab === 'report'" :result="result" :run-id="runId" @locate="setFocus" />
          <ScenarioPanel v-if="tab === 'scenario'" :result="result" @show-ring="showRing" />
          <ProofPanel v-if="tab === 'proof'" :result="result" />
          <AgentPanel v-if="tab === 'agent'" :center="center" @result="onAgentResult"
                      @actions="onAgentActions" @pick="onAgentPick" />
          <Archive v-if="tab === 'archive'" :history="history" :stats="stats" :compare="compareIds"
                   :comparison="comparison" @load="loadRun" @remove="removeRun" @toggle="toggleCompare" />
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import AgentPanel from './components/AgentPanel.vue'
import Archive from './components/Archive.vue'
import MapView from './components/MapView.vue'
import NavRail from './components/NavRail.vue'
import ProofPanel from './components/ProofPanel.vue'
import ReportPanel from './components/ReportPanel.vue'
import ScenarioPanel from './components/ScenarioPanel.vue'

const API = import.meta.env.VITE_API_BASE || ''

const NAV = [
  { k: 'report', n: '体检报告', d: 'M4 20V11M10 20V4M16 20v-6M2 20h20' },
  { k: 'scenario', n: '情景对比', d: 'M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5' },
  { k: 'proof', n: '计量自证', d: 'M12 3l7 3v6c0 4.5-3 7.4-7 9-4-1.6-7-4.5-7-9V6l7-3zM9 12l2 2 4-4' },
  { k: 'agent', n: 'AI 助手', d: 'M21 12a8 8 0 01-8 8H8l-4 3v-4.5A8 8 0 1121 12zM8.5 10.5h7M8.5 14h4' },
  { k: 'archive', n: '档案对比', d: 'M12 7.5V12l3 2M20.5 12a8.5 8.5 0 11-17 0 8.5 8.5 0 0117 0z' }
]

const VIEWS = {
  report: ['生活圈体检报告', '健康分、设施覆盖、可解释指标与已核验盲区清单'],
  scenario: ['情景与人群对比', '同一批实测耗时重算 5/10/15/20 分钟与三档步速，零新增 API 调用'],
  proof: ['出网计量与数据溯源', '三栏计量、分服务配额、理想圆对照与证据指纹'],
  agent: ['AI 规划助手', '大模型自主编排 5 个地图工具，结论回标到地图，轨迹落库可回放'],
  archive: ['历史档案与跨社区对比', 'SQLite 记录派生结果，点一条即可 0 配额回放']
}

const lat = ref('')
const lng = ref('')
const center = ref(null)
const running = ref(false)
const elapsed = ref(0)
const error = ref('')
const result = ref(null)
const runId = ref('')
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
const scenarioRing = ref(null)
const steps = ref([])
const agentNotice = ref('')
const activePreset = ref('')

const viewTitle = computed(() => VIEWS[tab.value][0])
const viewHint = computed(() => VIEWS[tab.value][1])
const hitRate = computed(() => stats.value.cache_hit_rate == null ? '—'
  : Math.round(stats.value.cache_hit_rate * 100) + '%')

onMounted(async () => {
  try {
    const h = await (await fetch(`${API}/api/health`)).json()
    mockMode.value = !!h.mock
    quotaOut.value = !!h.quota_exhausted
  } catch (e) { /* 后端未就绪时不显示提示 */ }
  await refreshLists()
})

function switchTab(k) {
  tab.value = k
  if (k === 'report') agentNotice.value = ''
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
function usePreset(p) {
  activePreset.value = p.id
  center.value = [p.lat, p.lng]
  lat.value = p.lat
  lng.value = p.lng
}
function onPick(c) {
  center.value = c
  lat.value = c[0]
  lng.value = c[1]
  activePreset.value = ''
}
function setCenterFromInput() {
  center.value = [lat.value, lng.value]
  activePreset.value = ''
}
function setFocus(f) { focus.value = { ...f, t: Date.now() } }
function showRing(ring) { scenarioRing.value = ring }
function exportMd() {
  const a = document.createElement('a')
  a.href = `${API}/api/runs/${runId.value}/report.md`
  a.download = `生活圈体检-${runId.value}.md`
  a.click()
}
function print() { window.print() }

// 智能体产出的体检结果写进报告视图，但不把用户从对话框里拽走
function onAgentResult(res) {
  result.value = res
  agentNotice.value = `智能体已完成体检（${res.health_score} 分 · 盲区 ${res.blindspots.length} 处）→ 查看报告`
}
function onAgentActions(actions) { agentActions.value = actions }
function onAgentPick(c) { onPick(c) }

async function runAt(c) { onPick(c); await run() }

async function loadRun(h) {
  result.value = await (await fetch(`${API}/api/runs/${h.id}`)).json()
  runId.value = h.id
  center.value = [h.lat, h.lng]
  switchTab('report')
  scenarioRing.value = null
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

const PIPELINE = ['逆地理编码定位社区', '18 条射线实测步行耗时（等时圈）', '四类设施检索与语义清洗',
                  '盲区网格初筛', '批量距离矩阵核验', '情景与人群重算']

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
  scenarioRing.value = null
  elapsed.value = 0
  markSteps(1)
  try {
    const p = presets.value.find(x => x.id === activePreset.value)
    const res = await fetch(`${API}/api/analysis`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lat: center.value[0], lng: center.value[1],
        label: p?.name || '', archetype: p?.archetype || ''
      })
    })
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
  const total = PIPELINE.length - 1
  for (let i = 0; i < 900; i++) {
    await new Promise(r => setTimeout(r, 1000))
    elapsed.value = i + 1
    markSteps(Math.min(1 + Math.floor(i / 6), total))
    const job = await (await fetch(`${API}/api/analysis/${jobId}`)).json()
    if (job.status === 'done') {
      result.value = job.result
      runId.value = job.run_id || ''
      quotaOut.value = !!job.result?.meta?.quota_exhausted
      await refreshLists()
      return
    }
    if (job.status === 'error') {
      error.value = job.quota_exhausted
        ? '今日步行配额已耗尽：请明日重试，或把 .env 的 BMAP_MOCK 设为 1 走演示模式'
        : job.error
      return
    }
  }
  error.value = '任务超时'
}
</script>

<style scoped>
.top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 20px;
  background: linear-gradient(115deg, #0e1a2b, #1b2f4c 55%, #21406b);
  color: #fff;
}
.brand { display: flex; align-items: center; gap: 12px; }
.logo {
  width: 42px; height: 42px; flex: none;
  display: grid; place-items: center;
  border-radius: 14px;
  background: linear-gradient(135deg, #2f6fed, #6d5efc);
  font-size: 15px; font-weight: 700;
  box-shadow: 0 6px 18px rgba(47, 111, 237, .45);
}
.brand h1 { font-size: 16px; font-weight: 600; }
.brand p { font-size: 11px; opacity: .62; margin-top: 3px; }
.pills { display: flex; flex-wrap: wrap; gap: 7px; justify-content: flex-end; }
.pills .pill { background: rgba(255, 255, 255, .1); color: #dbe7f7; }
.pills .pill.ok { background: rgba(23, 178, 106, .18); color: #6ee7b0; }
.pills .pill.warn { background: rgba(245, 166, 35, .2); color: #ffd28a; }
.pills .pill.bad { background: rgba(229, 72, 77, .2); color: #ffb0b3; }

.shell {
  display: grid;
  grid-template-columns: 74px 292px minmax(0, 1fr) 412px;
  gap: 12px;
  padding: 12px 16px 16px;
  height: calc(100vh - 68px);
}
.railslot { align-self: stretch; }
.col { display: flex; flex-direction: column; gap: 12px; overflow-y: auto; }
.stage { border-radius: 18px; overflow: hidden; box-shadow: var(--shadow); min-height: 0; }

.presets { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.preset {
  width: 100%;
  display: flex; flex-direction: column; align-items: flex-start; gap: 1px;
  padding: 9px 11px; border-radius: 12px;
  background: #f7f9fd; border: 1px solid var(--line);
  color: var(--text); cursor: pointer; text-align: left;
  transition: border-color .16s, background .16s, transform .16s;
}
.preset b { font-size: 12.5px; font-weight: 600; }
.preset span { font-size: 10.5px; color: var(--muted); }
.preset:hover { transform: translateX(2px); border-color: #c8d8f5; }
.preset.on { background: linear-gradient(135deg, #eaf1ff, #f1ecff); border-color: #a9c4fb; }
.coords { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 10px; }
.coords label { font-size: 11px; color: var(--muted); display: flex; flex-direction: column; gap: 4px; }
.picked { font-size: 11px; color: var(--muted); margin: -2px 0 10px; }
.picked code { color: var(--brand); font-size: 11.5px; }
.tip { font-size: 11px; color: var(--muted); line-height: 1.65; margin-top: 10px; }
.err { color: var(--bad); font-size: 11.5px; margin-top: 9px; line-height: 1.6; }

.hud { list-style: none; margin-top: 12px; font-size: 11px; line-height: 1.95; color: #a7b3c5; }
.hud li { display: flex; align-items: center; gap: 6px; }
.hud li.done { color: var(--good); }
.hud li.run { color: var(--brand); font-weight: 600; }
.hud li.run i { animation: pulse 1s ease-in-out infinite; }
.hud i { font-style: normal; width: 11px; display: inline-block; }
@keyframes pulse { 50% { opacity: .3; } }

.tiles { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.tile {
  background: linear-gradient(160deg, #f8fafd, #eef3fb);
  border: 1px solid var(--line);
  border-radius: 12px; padding: 10px 6px; text-align: center;
}
.tile b { display: block; font-size: 17px; color: var(--ink); font-variant-numeric: tabular-nums; }
.tile span { font-size: 10px; color: var(--muted); }

.right { overflow: hidden; }
.drawer { display: flex; flex-direction: column; height: 100%; padding: 14px 16px; }
.dhead { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 12px; }
.dhead h2 { font-size: 15px; font-weight: 600; }
.dhead p { font-size: 11px; color: var(--muted); margin-top: 3px; line-height: 1.5; }
.dacts { display: flex; gap: 6px; flex: none; }
.notice {
  width: 100%; margin: -4px 0 10px; padding: 9px 11px;
  font-size: 11px; text-align: left; border-radius: 10px; line-height: 1.5;
  background: #eef4ff; color: #1d4fa8; border: 1px dashed #b9cffb;
}
.dbody { flex: 1; min-height: 0; overflow-y: auto; padding-right: 2px; }
.dbody.flush { overflow: hidden; display: flex; flex-direction: column; }

@media (max-width: 1440px) {
  .shell { grid-template-columns: 70px 258px minmax(0, 1fr) 358px; padding: 10px 12px 12px; gap: 10px; }
}
/* 窄屏不藏控件：整块工作区横向滚动，避免"开始体检"这种主操作被断点吃掉 */
@media (max-width: 1180px) {
  .shell { min-width: 1160px; }
  body { overflow-x: auto; }
}
@media print {
  .top, .railslot, .left, .stage, .dacts, .notice { display: none !important; }
  .shell { display: block; height: auto; padding: 0; }
  .drawer { box-shadow: none; border: none; padding: 0; height: auto; }
}
</style>
