<template>
  <div v-if="!result" class="empty">
    <div class="ghost-ring"></div>
    还没有体检结果<br /><span>左栏选一个社区，或直接在地图上点 / 拖 / 右键</span>
  </div>
  <div v-else>
    <div class="hero">
      <div class="gauge" :style="gaugeStyle">
        <div class="in"><b>{{ score }}</b><span>/ 100</span></div>
      </div>
      <div class="meta">
        <div class="lab" :class="grade.cls">{{ grade.text }}</div>
        <b>{{ result.label || `${result.center.lat.toFixed(4)}, ${result.center.lng.toFixed(4)}` }}</b>
        <p>{{ result.archetype || result.meta.data_mode }} ·
          <span class="pill" :class="result.meta.data_mode === 'real' ? 'ok' : 'warn'">
            <i></i>{{ modeText }}</span></p>
        <div class="kpis">
          <span><b>{{ result.area_km2 }}</b>km² 可达</span>
          <span><b>{{ verified }}</b>处已核验盲区</span>
          <span><b>{{ density }}</b>处/km²</span>
        </div>
      </div>
    </div>

    <h3 class="sec">设施覆盖 · 圈内可达 vs 检索总量</h3>
    <div ref="barEl" class="chart"></div>

    <div class="two">
      <div>
        <h3 class="sec">类别覆盖环</h3>
        <div ref="donutEl" class="chart sm"></div>
      </div>
      <div>
        <h3 class="sec">受阻代理</h3>
        <ul class="dl">
          <li><b>{{ detour.detour_factor_p50 }}</b><span>绕行系数 P50</span></li>
          <li><b>{{ detour.detour_factor_p90 }}</b><span>P90</span></li>
          <li><b>{{ detour.implied_speed_mps }}</b><span>隐含步速 m/s</span></li>
          <li><b>{{ detour.slow_samples }}</b><span>缓慢采样点</span></li>
        </ul>
      </div>
    </div>

    <h3 class="sec">可解释指标</h3>
    <ul class="metrics">
      <li v-for="m in result.metrics" :key="m.key">
        <div class="top"><b>{{ m.label }}</b><span>{{ m.value }} {{ m.unit }}</span></div>
        <code>{{ m.formula }}</code>
        <p>{{ m.note }}</p>
      </li>
    </ul>

    <h3 class="sec">服务盲区 <em>{{ verified }} 处已核验 / 共 {{ result.blindspots.length }} 处</em></h3>
    <table v-if="result.blindspots.length">
      <thead><tr><th>状态</th><th>缺乏设施</th><th>坐标</th><th></th></tr></thead>
      <tbody>
        <tr v-for="(b, i) in result.blindspots.slice(0, 12)" :key="i">
          <td><span class="st" :class="b.verified ? 'v' : 'p'">{{ b.verified ? '已核验' : '疑似' }}</span></td>
          <td>{{ b.missing?.join('、') || b.missing_preview?.join('、') || '—' }}
            <em v-if="b.undetermined?.length" class="und">｜无法判定 {{ b.undetermined.join('、') }}</em></td>
          <td class="co">{{ b.lat.toFixed(4) }}, {{ b.lng.toFixed(4) }}</td>
          <td><button class="k" @click="$emit('locate', { lat: b.lat, lng: b.lng, label: '盲区' })">定位</button></td>
        </tr>
      </tbody>
    </table>
    <p v-else class="ok">四类设施与菜市场/药店/小学全部达标，未发现服务盲区。</p>

    <h3 class="sec">圈内设施（每类前 3）</h3>
    <ul class="fac">
      <li v-for="f in topFacilities" :key="f.key">
        <i :style="{ background: f.color }"></i>
        <span class="nm">{{ f.name }}</span>
        <span class="dim">{{ f.dist }}m</span>
        <button class="k" @click="$emit('locate', { lat: f.lat, lng: f.lng, label: f.name })">定位</button>
      </li>
    </ul>
    <p class="foot">绕行系数由本次实测自标定，只作受阻代理，不进入健康分。</p>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({ result: Object, runId: String })
defineEmits(['locate'])
const barEl = ref(null)
const donutEl = ref(null)
let barChart = null, donutChart = null

const CAT_COLORS = { medical: '#e5484d', education: '#8e4ec6', shopping: '#f76b15', elderly: '#0091ff' }

const score = computed(() => props.result?.health_score ?? 0)
const verified = computed(() => (props.result?.blindspots || []).filter(b => b.verified).length)
const density = computed(() => props.result?.metrics?.find(m => m.key === 'coverage')?.value ?? '—')
const detour = computed(() => props.result?.detour || {})
const modeText = computed(() => ({ real: '真实路网', mock: 'Mock 合成', 'real+degraded': '真实 · 部分降级' }
  [props.result?.meta?.data_mode] || '真实路网'))
const grade = computed(() => score.value >= 85 ? { text: '配套完善', cls: 'good' }
  : score.value >= 70 ? { text: '总体可用', cls: 'mid' }
  : score.value >= 55 ? { text: '存在短板', cls: 'warn' } : { text: '设施缺口明显', cls: 'bad' })
const gaugeStyle = computed(() => {
  const c = score.value >= 80 ? '#12b26b' : score.value >= 60 ? '#f5a623' : '#e5484d'
  return { background: `conic-gradient(${c} ${score.value * 3.6}deg, #e9eef6 0deg)` }
})
const topFacilities = computed(() => {
  const out = []
  const c = props.result?.center || { lat: 0, lng: 0 }
  Object.entries(props.result?.pois || {}).forEach(([key, list]) => {
    list.slice(0, 3).forEach(p => out.push({
      key: key + p.name, name: p.name, lat: p.lat, lng: p.lng, color: CAT_COLORS[key],
      dist: Math.round(Math.hypot((p.lat - c.lat) * 111.32, (p.lng - c.lng) * 111.32
        * Math.cos(c.lat * Math.PI / 180)) * 1000)
    }))
  })
  return out.slice(0, 12)
})

watch(() => props.result, async (res) => {
  if (!res) return
  await nextTick()
  draw(res)
}, { immediate: true })

function draw(res) {
  const entries = Object.entries(res.coverage)
  const cats = entries.map(([, v]) => v)
  const colorOf = (i) => CAT_COLORS[entries[i][0]]
  if (barEl.value) {
    barChart?.dispose()
    barChart = echarts.init(barEl.value)
    barChart.setOption({
      grid: { left: 6, right: 6, top: 28, bottom: 4, containLabel: true },
      legend: { top: 0, itemWidth: 9, itemHeight: 9, textStyle: { fontSize: 10, color: '#7c8aa0' } },
      xAxis: { type: 'category', data: cats.map(c => c.name),
        axisLabel: { fontSize: 11, color: '#5a6577' }, axisLine: { lineStyle: { color: '#e6ebf3' } } },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: '#f1f4f9' } },
        axisLabel: { fontSize: 10, color: '#9aa4b5' } },
      series: [
        { name: '圈内可达', type: 'bar', barWidth: 20, barGap: '-100%', data: cats.map(c => c.inside),
          itemStyle: { color: (p) => colorOf(p.dataIndex), borderRadius: [6, 6, 0, 0] },
          label: { show: true, position: 'top', fontSize: 10, color: '#1f2733' } },
        { name: '检索总量', type: 'bar', barWidth: 20, data: cats.map(c => c.total),
          itemStyle: { color: '#e9eef6', borderRadius: [6, 6, 0, 0] } }
      ]
    })
  }
  if (donutEl.value) {
    donutChart?.dispose()
    donutChart = echarts.init(donutEl.value)
    donutChart.setOption({
      tooltip: { trigger: 'item' },
      series: [{
        type: 'pie', radius: ['58%', '82%'], center: ['50%', '50%'],
        label: { show: false },
        data: cats.map((c, i) => ({ name: c.name, value: c.inside || 1,
          itemStyle: { color: c.inside ? colorOf(i) : '#dfe6ef' } }))
      }]
    })
  }
}

onBeforeUnmount(() => { barChart?.dispose(); donutChart?.dispose() })
</script>

<style scoped>
.empty { color: var(--muted); text-align: center; padding-top: 54px; font-size: 13px; line-height: 2; }
.empty span { font-size: 11px; }
.ghost-ring {
  width: 74px; height: 74px; margin: 0 auto 14px; border-radius: 50%;
  border: 6px solid #e9eef6; border-top-color: #c5d5ee;
}
.hero { display: flex; align-items: center; gap: 14px; padding: 2px 0 6px; }
.gauge {
  width: 92px; height: 92px; border-radius: 50%; flex: none;
  display: grid; place-items: center;
  box-shadow: 0 8px 20px rgba(20, 40, 70, .1);
}
.gauge .in {
  width: 70px; height: 70px; border-radius: 50%; background: #fff;
  display: grid; place-items: center; line-height: 1.1; text-align: center;
}
.gauge b { font-size: 26px; font-weight: 700; color: var(--ink); font-variant-numeric: tabular-nums; }
.gauge span { font-size: 9px; color: var(--muted); display: block; }
.lab { font-size: 13px; font-weight: 700; margin-bottom: 2px; }
.lab.good { color: #0d7a49; } .lab.mid { color: #9a5b00; }
.lab.warn { color: #b06a00; } .lab.bad { color: #b62c31; }
.meta > b { font-size: 13px; color: var(--text); }
.meta > p { font-size: 11px; color: var(--muted); margin: 3px 0 8px; display: flex; align-items: center; gap: 6px; }
.kpis { display: flex; gap: 10px; font-size: 10px; color: var(--muted); }
.kpis b { display: block; font-size: 13px; color: var(--ink); font-variant-numeric: tabular-nums; }
.sec { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px;
  margin: 16px 0 6px; font-weight: 600; }
.sec em { font-style: normal; text-transform: none; letter-spacing: 0; color: var(--brand); }
.chart { height: 168px; }
.chart.sm { height: 132px; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.dl { list-style: none; font-size: 11px; }
.dl li { display: flex; justify-content: space-between; align-items: baseline; padding: 6px 0;
  border-bottom: 1px dashed #eef1f7; }
.dl b { font-size: 15px; color: var(--ink); font-variant-numeric: tabular-nums; }
.dl span { color: var(--muted); font-size: 10.5px; }
.metrics { list-style: none; }
.metrics li { border: 1px solid var(--line); border-radius: 12px; padding: 9px 11px; margin-bottom: 7px;
  background: linear-gradient(160deg, #fbfcfe, #f5f8fc); }
.metrics .top { display: flex; justify-content: space-between; align-items: baseline; }
.metrics b { font-size: 12.5px; color: var(--ink); }
.metrics span { font-size: 14px; font-weight: 700; color: var(--brand); font-variant-numeric: tabular-nums; }
.metrics code { display: block; font-size: 10px; color: #5a6a83; margin-top: 4px;
  font-family: ui-monospace, Consolas, monospace; }
.metrics p { font-size: 10px; color: var(--muted); margin-top: 2px; }
.st { font-size: 10px; padding: 2px 7px; border-radius: 999px; }
.st.v { background: #fdeaea; color: #b62c31; }
.st.p { background: #eef1f7; color: #7c8aa0; }
.und { color: #b06a00; font-style: normal; font-size: 10px; }
.co { color: #9aa4b5; font-size: 10px; white-space: nowrap; }
.k { width: auto; padding: 3px 8px; font-size: 10px; border-radius: 7px; background: #eef2fb; color: var(--brand);
  box-shadow: none; }
.ok { color: #0d7a49; font-size: 12px; }
.fac { list-style: none; font-size: 11px; }
.fac li { display: flex; align-items: center; gap: 7px; padding: 5px 0; border-bottom: 1px dashed #f1f4f9; }
.fac i { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.fac .nm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fac .dim { color: var(--muted); font-size: 10px; }
.foot { font-size: 10px; color: var(--muted); margin-top: 12px; line-height: 1.6; }
</style>
