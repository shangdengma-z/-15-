<template>
  <header class="bar">
    <h1>15 分钟生活圈智能体检与规划助手</h1>
    <span class="sub">百度地图开放能力 · 真实路网等时圈 · 服务盲区诊断</span>
    <span v-if="mockMode" class="mocktag" title="数据由本地 Mock 生成，仅用于无密钥演示与自动化测试">
      ⚠ Mock 演示模式 · 数据为本地合成，非真实路网
    </span>
  </header>
  <main class="layout">
    <aside class="panel side">
      <section>
        <h3>1. 选择社区中心点</h3>
        <p class="tip">在地图上点击任意位置，或输入坐标后回车。</p>
        <label>纬度 <input v-model.number="lat" type="number" step="0.0001" @keyup.enter="lng && setCenterFromInput()" /></label>
        <label>经度 <input v-model.number="lng" type="number" step="0.0001" @keyup.enter="lat && setCenterFromInput()" /></label>
        <div class="picked" v-if="center">已选：{{ center[0].toFixed(5) }}, {{ center[1].toFixed(5) }}</div>
      </section>
      <section>
        <h3>2. 生成体检报告</h3>
        <button :disabled="!center || running" @click="run">{{ running ? `计算中 ${elapsed}s…` : '开始体检' }}</button>
        <p class="tip">首次计算约 1~2 分钟（受 QPS 限制），结果缓存后秒回。</p>
        <div v-if="error" class="err">{{ error }}</div>
      </section>
      <section v-if="result">
        <h3>3. 运行指标</h3>
        <ul class="meta">
          <li>API 调用：{{ result.meta.api_calls }} 次</li>
          <li>缓存命中：{{ result.meta.cache_hits }} 次</li>
          <li>耗时：{{ result.meta.elapsed_sec }} s</li>
          <li v-if="result.meta.retry_count">限流重试：{{ result.meta.retry_count }} 次（已自动退避恢复）</li>
          <li v-if="result.meta.matrix_fallback">距离矩阵未开通，已改用并发路线规划核验</li>
          <li v-if="result.meta.degraded" class="err">⚠ 部分点位路网获取失败，已降级为直线估算</li>
        </ul>
      </section>
    </aside>
    <MapView class="map" :center="center" :result="result" :actions="agentActions" @pick="onPick" />
    <div class="panel right" :class="{ flexed: tab === 'agent' }">
      <div class="tabs">
        <button :class="{ on: tab === 'report' }" @click="tab = 'report'">体检报告</button>
        <button :class="{ on: tab === 'agent' }" @click="tab = 'agent'">AI 规划助手</button>
      </div>
      <ReportPanel v-if="tab === 'report'" :result="result" />
      <AgentPanel v-if="tab === 'agent'" :center="center" @result="onAgentResult"
                  @actions="onAgentActions" @pick="onAgentPick" />
    </div>
  </main>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import AgentPanel from './components/AgentPanel.vue'
import MapView from './components/MapView.vue'
import ReportPanel from './components/ReportPanel.vue'

const API = import.meta.env.VITE_API_BASE || ''
const lat = ref('')
const lng = ref('')
const center = ref(null)   // [lat, lng]
const running = ref(false)
const elapsed = ref(0)
const error = ref('')
const result = ref(null)
const tab = ref('report')
const agentActions = ref([])
const mockMode = ref(false)

// Mock 模式下界面与真实数据完全同形，必须显式标注，避免演示/评审时误把合成数据当真实结果
onMounted(async () => {
  try {
    const h = await (await fetch(`${API}/api/health`)).json()
    mockMode.value = !!h.mock
  } catch (e) { /* 后端未就绪时不显示提示 */ }
})

function onPick(c) {
  center.value = c
  lat.value = c[0]
  lng.value = c[1]
}
function setCenterFromInput() {
  center.value = [lat.value, lng.value]
}

// 智能体的产出与手动体检共用同一套渲染：结果进地图与报告，指令进地图标注
function onAgentResult(res) { result.value = res }
function onAgentActions(actions) { agentActions.value = actions }
function onAgentPick(c) { onPick(c) }

async function run() {
  running.value = true
  error.value = ''
  result.value = null
  agentActions.value = []
  elapsed.value = 0
  try {
    const res = await fetch(`${API}/api/analysis`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat: center.value[0], lng: center.value[1] })
    })
    const { job_id: jobId } = await res.json()
    await poll(jobId)
  } catch (e) {
    error.value = '请求失败：' + e.message
  } finally {
    running.value = false
  }
}

async function poll(jobId) {
  for (let i = 0; i < 600; i++) {
    await new Promise(r => setTimeout(r, 1000))
    elapsed.value = i + 1
    const job = await (await fetch(`${API}/api/analysis/${jobId}`)).json()
    if (job.status === 'done') { result.value = job.result; return }
    if (job.status === 'error') { error.value = job.error; return }
  }
  error.value = '任务超时'
}
</script>

<style>
* { box-sizing: border-box; margin: 0; }
body { font-family: "PingFang SC", "Microsoft YaHei", sans-serif; background: #f3f5f9; color: #1f2733; }
.bar { display: flex; align-items: baseline; gap: 16px; padding: 14px 24px; background: #14243d; color: #fff; }
.bar h1 { font-size: 18px; font-weight: 600; }
.bar .sub { font-size: 12px; opacity: .7; }
.mocktag { margin-left: auto; flex: none; font-size: 12px; padding: 4px 10px; border-radius: 12px;
           background: #fff4e5; color: #8a5200; border: 1px solid #f5a623; }
.layout { display: grid; grid-template-columns: 300px 1fr 340px; gap: 12px; padding: 12px 24px; height: calc(100vh - 60px); }
.panel { background: #fff; border-radius: 10px; padding: 16px; overflow-y: auto; box-shadow: 0 1px 4px rgba(20,36,61,.08); }
.side section { margin-bottom: 18px; }
.side h3 { font-size: 14px; margin-bottom: 8px; }
.side label { display: block; font-size: 13px; margin: 6px 0; }
.side input { width: 100%; padding: 6px 8px; border: 1px solid #d5dbe5; border-radius: 6px; margin-top: 4px; }
.tip { font-size: 12px; color: #7a8699; margin-top: 6px; }
.picked { font-size: 12px; color: #2f6fed; margin-top: 6px; }
button { width: 100%; padding: 10px; border: none; border-radius: 8px; background: #2f6fed; color: #fff; font-size: 14px; cursor: pointer; }
button:disabled { background: #b7c4d8; cursor: not-allowed; }
.err { color: #d43b3b; font-size: 12px; margin-top: 8px; }
.map { border-radius: 10px; overflow: hidden; box-shadow: 0 1px 4px rgba(20,36,61,.08); }
.meta { font-size: 13px; line-height: 1.9; list-style: none; }
.right { display: flex; flex-direction: column; }
.right.flexed { overflow: hidden; }
.right > *:not(.tabs) { flex: 1; min-height: 0; overflow-y: auto; }
.right.flexed > *:not(.tabs) { overflow: hidden; }
.tabs { display: flex; gap: 6px; margin-bottom: 12px; flex: none; }
.tabs button { flex: 1; padding: 8px; background: #eef2fb; color: #4a5568; font-size: 13px; border-radius: 8px; }
.tabs button.on { background: #2f6fed; color: #fff; }
</style>
