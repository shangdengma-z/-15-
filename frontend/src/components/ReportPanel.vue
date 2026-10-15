<template>
  <div v-if="!result" class="empty">
    <div class="ring"></div>
    <b>还没有体检结果</b>
    <span>左栏选一个社区点「开始体检」，或直接在地图上单击 / 右键</span>
  </div>
  <div v-else class="rep">
    <div class="hero">
      <div class="dial">
        <svg viewBox="0 0 120 120" class="arc">
          <circle class="track" cx="60" cy="60" r="50" />
          <circle class="fill" cx="60" cy="60" r="50" :style="arcStyle" />
        </svg>
        <div class="ticks"></div>
        <div class="val"><b class="num">{{ score }}</b><em>分</em></div>
      </div>
      <div class="meta">
        <div class="lab" :class="grade.cls">{{ grade.text }}</div>
        <b>{{ result.label || `${result.center.lat.toFixed(4)}, ${result.center.lng.toFixed(4)}` }}</b>
        <p>{{ result.archetype || '自定义点位' }}
          <span class="pill" :class="result.meta.data_mode === 'real' ? 'ok' : 'warn'">
            <i></i>{{ modeText }}</span></p>
      </div>
    </div>

    <div class="kpis">
      <div><b class="num">{{ result.area_km2 }}</b><span>km² 可达面积</span></div>
      <div><b class="num">{{ verified }}</b><span>处已核验盲区</span></div>
      <div><b class="num">{{ density }}</b><span>处/km² 设施密度</span></div>
    </div>

    <h3 class="sec">设施覆盖 <em>圈内可达 / 检索总量</em></h3>
    <div ref="barEl" class="chart"></div>

    <template v-if="std.items.length">
      <h3 class="sec">导则达标对照 <em>{{ std.passed }}/{{ std.total }} 项达标</em></h3>
      <div class="guide">
        <div class="ghead">
          <span></span><span>项目</span><span class="rt">最近</span><span class="rt">参考</span><span class="rt">圈内</span>
        </div>
        <div v-for="it in std.items" :key="it.name" class="grow" :title="it.name + '：' + it.basis + ' · ' + it.status">
          <span class="st2" :class="stCls(it.status)">{{ it.status }}</span>
          <span class="gname"><b>{{ it.name }}</b></span>
          <span class="gval rt num" :class="{ na: it.nearest_m === null }">{{ it.nearest_m ?? '—' }}</span>
          <span class="gref rt num">{{ it.ref_m }}</span>
          <span class="gval rt num" :class="{ na: it.inside === 0 }">{{ it.inside }}</span>
          <i class="gbar"><em :class="stCls(it.status)" :style="{ width: barPct(it) }"></em></i>
        </div>
      </div>
      <p class="src">参考半径取 GB 50180-2018《城市居住区规划设计标准》与上海 15 分钟社区生活圈导则的常用值，
        属规划参考口径。状态按<strong>中心点直线最近距离</strong>初筛（路网实测结论见下方盲区清单）；
        「圈内」按等时圈多边形计数，两者可能不一致——设施在 500m 直线范围内却落在路网圈外，
        正是绕行、河桥、围挡造成的真实缺口。</p>
    </template>

    <h3 class="sec">服务盲区 <em>{{ clusters.length }} 个片区 · {{ result.blindspots.length }} 格（{{ verified }} 格已核验）</em></h3>
    <ul v-if="clusters.length" class="clusters">
      <li v-for="(c, i) in clusters" :key="i" class="cl" :class="{ dim: !c.verified_cells }">
        <span class="lv" :class="c.verified_cells ? 'v' : 'p'">{{ c.level }}</span>
        <div class="clb">
          <div class="cltop"><b>{{ c.missing.join('、') || '待判定' }}</b>
            <span class="num">{{ c.area_km2 }} km²</span></div>
          <div class="clmeta">{{ c.cells }} 格连片 · 已核验 {{ c.verified_cells }} 格 ·
            待核验 {{ c.suspect_cells }} 格
            <em v-if="c.undetermined && c.undetermined.length" class="und">｜无法判定
              {{ c.undetermined.join('、') }}</em></div>
          <button class="k" @click="$emit('locate', { lat: c.lat, lng: c.lng, label: '缺口片区' })">
            定位片区</button>
        </div>
      </li>
    </ul>
    <p v-else class="ok">四类设施与菜市场 / 药店 / 小学均达标，未发现服务盲区。</p>

    <details v-if="result.blindspots.length" class="cellsd">
      <summary>展开 {{ result.blindspots.length }} 个网格点</summary>
      <p class="src2">300 m 网格逐格判定，所以一片连片缺口会呈现为多格；地图上的形状就是这些格子，
        上表按邻格连通（≤450 m）并成片区后只需要读 {{ clusters.length }} 行。
        片区面积约等于格数 × 0.09 km²，是网格邻域的尺度近似，不是边界精确面积。</p>
      <table>
        <thead><tr><th>状态</th><th>缺乏设施</th><th></th></tr></thead>
        <tbody>
          <tr v-for="(b, i) in result.blindspots" :key="i">
            <td><span class="st" :class="b.verified ? 'v' : 'p'">{{ b.verified ? '已核验' : '疑似' }}</span></td>
            <td>{{ b.missing?.join('、') || b.missing_preview?.join('、') || '—' }}
              <em v-if="b.undetermined?.length" class="und">｜无法判定 {{ b.undetermined.join('、') }}</em></td>
            <td class="co num">{{ b.lat.toFixed(4) }}, {{ b.lng.toFixed(4) }}</td>
          </tr>
        </tbody>
      </table>
    </details>

    <h3 class="sec">圈内设施 <em>每类前 3</em></h3>
    <ul class="fac">
      <li v-for="f in topFacilities" :key="f.key">
        <i :style="{ background: f.color }"></i>
        <span class="nm">{{ f.name }}</span>
        <span class="dim num">{{ f.dist }}m</span>
        <button class="k" @click="$emit('locate', { lat: f.lat, lng: f.lng, label: f.name })">定位</button>
      </li>
    </ul>

    <details class="how">
      <summary>指标口径与算法</summary>
      <ul class="metrics">
        <li v-for="m in result.metrics" :key="m.key">
          <div class="top"><b>{{ m.label }}</b><span class="num">{{ m.value }} {{ m.unit }}</span></div>
          <code>{{ m.formula }}</code>
          <p>{{ m.note }}</p>
        </li>
      </ul>
      <p class="foot">绕行系数由本次实测自标定（P50 {{ detour.detour_factor_p50 ?? '—' }} ·
        P90 {{ detour.detour_factor_p90 ?? '—' }} · 隐含步速 {{ detour.implied_speed_mps ?? '—' }} m/s），
        只作受阻代理，不进入健康分。</p>
    </details>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({ result: Object })
defineEmits(['locate'])
const barEl = ref(null)
let barChart = null

const CAT_COLORS = { medical: '#b06a6f', education: '#8b87ad', shopping: '#c08a6a', elderly: '#6f9bb5' }

const score = computed(() => props.result?.health_score ?? 0)
const verified = computed(() => (props.result?.blindspots || []).filter(b => b.verified).length)
const density = computed(() => props.result?.metrics?.find(m => m.key === 'coverage')?.value ?? '—')
const detour = computed(() => props.result?.detour || {})
const std = computed(() => props.result?.standards || { items: [], passed: 0, total: 0 })
// 早期建档记录可能没有片区字段：退化成一格一片区，保证列表不空
const clusters = computed(() => {
  const cs = props.result?.blindspot_clusters
  if (cs && cs.length) return cs
  return (props.result?.blindspots || []).map(b => ({
    cells: 1, area_km2: 0.09, lat: b.lat, lng: b.lng,
    missing: b.missing || b.missing_preview || [], verified_cells: b.verified ? 1 : 0,
    suspect_cells: b.verified ? 0 : 1, undetermined: b.undetermined || [],
    level: b.verified ? '已核验' : '待核验'
  }))
})
const stCls = (s) => ({ 达标: 'ok', 临界: 'mid', 缺口: 'bad', 无点位: 'bad' }[s] || 'mid')
// 细条按"最近距离 ÷ 参考半径"给长度，超过 1.5 倍就顶格：一眼看出是压线还是差得远
const barPct = (it) => (it.nearest_m === null ? '100%'
  : Math.min(100, Math.round(it.nearest_m / it.ref_m / 1.5 * 100)) + '%')
const modeText = computed(() => ({ real: '真实路网', mock: '演示合成', 'real+degraded': '真实 · 部分降级' }
  [props.result?.meta?.data_mode] || '真实路网'))
const grade = computed(() => score.value >= 85 ? { text: '配套完善', cls: 'good' }
  : score.value >= 70 ? { text: '总体可用', cls: 'mid' }
  : score.value >= 55 ? { text: '存在短板', cls: 'warn' } : { text: '设施缺口明显', cls: 'bad' })

const arcStyle = computed(() => {
  const c = score.value >= 80 ? '#4f8a68' : score.value >= 60 ? '#b98a4b' : '#b06a6f'
  return { stroke: c, strokeDasharray: `${(score.value / 100) * 314} 314` }
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
  if (!barEl.value) return
  barChart?.dispose()
  barChart = echarts.init(barEl.value)
  barChart.setOption({
    grid: { left: 4, right: 8, top: 26, bottom: 2, containLabel: true },
    legend: { top: 0, itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 10, color: '#6e7c73' } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
      backgroundColor: '#ffffff', borderColor: '#c3cdbe', textStyle: { color: '#2f3b36', fontSize: 11 } },
    xAxis: { type: 'category', data: cats.map(c => c.name),
      axisLabel: { fontSize: 11, color: '#2f3b36' }, axisLine: { lineStyle: { color: '#c3cdbe' } } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#e6ebe2' } },
      axisLabel: { fontSize: 10, color: '#93a099' } },
    series: [
      { name: '圈内可达', type: 'bar', barWidth: 19, barGap: '-100%', data: cats.map(c => c.inside),
        itemStyle: { color: (p) => colorOf(p.dataIndex), borderRadius: [5, 5, 0, 0] },
        label: { show: true, position: 'top', fontSize: 10, color: '#2f3b36' } },
      { name: '检索总量', type: 'bar', barWidth: 19, data: cats.map(c => c.total),
        itemStyle: { color: '#dfe6db', borderRadius: [5, 5, 0, 0] } }
    ]
  })
}

onBeforeUnmount(() => barChart?.dispose())
</script>

<style scoped>
.rep { animation: fade .2s ease both; }
.empty { display: grid; place-content: center; gap: 6px; text-align: center; padding-top: 46px;
  color: var(--dim); }
.empty b { font-size: 13.5px; color: var(--muted); font-weight: 600; }
.empty span { font-size: 11px; line-height: 1.8; }
.ring { width: 62px; height: 62px; margin: 0 auto 12px; border-radius: 50%;
  border: 3px solid var(--line); border-top-color: var(--accent); animation: spin 2.4s linear infinite; }

.hero { display: flex; align-items: center; gap: 15px; }
.dial { position: relative; width: 104px; height: 104px; flex: none; }
.arc { position: absolute; inset: 0; transform: rotate(-90deg); }
.arc circle { fill: none; stroke-width: 8; }
.track { stroke: var(--surface-3); }
.fill { stroke-linecap: round; transition: stroke-dasharray .9s cubic-bezier(.2, .8, .2, 1); }
.ticks { position: absolute; inset: -8px; border-radius: 50%;
  background: repeating-conic-gradient(from 0deg, #c3cdbe 0 .35deg, transparent .35deg 6deg);
  -webkit-mask: radial-gradient(circle, transparent 60%, #000 61%, #000 67%, transparent 68%);
  mask: radial-gradient(circle, transparent 60%, #000 61%, #000 67%, transparent 68%); }
.val { position: absolute; inset: 0; display: grid; place-content: center; text-align: center; }
.val b { font-size: 33px; font-weight: 700; color: var(--ink); line-height: 1; }
.val em { font-style: normal; font-size: 10px; color: var(--dim); }
.lab { font-size: 14px; font-weight: 600; letter-spacing: .3px; margin-bottom: 3px; }
.lab.good { color: var(--good); } .lab.mid { color: var(--accent); }
.lab.warn { color: var(--warn); } .lab.bad { color: var(--bad); }
.meta > b { font-size: 13px; display: block; }
.meta > p { font-size: 10.5px; color: var(--dim); margin-top: 5px; display: flex; align-items: center;
  gap: 7px; flex-wrap: wrap; }

.kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 14px 0 4px;
  padding: 11px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.kpis div { text-align: center; }
.kpis b { display: block; font-size: 19px; color: var(--ink); line-height: 1.25; }
.kpis span { font-size: 9.5px; color: var(--dim); }

.sec { font-family: var(--font-num); font-size: 10.5px; color: var(--muted); letter-spacing: 1.5px;
  text-transform: uppercase; margin: 17px 0 8px; font-weight: 600; }
.sec em { font-family: var(--font-body); font-style: normal; text-transform: none; letter-spacing: 0;
  color: var(--accent); font-size: 10.5px; }
.chart { height: 170px; }

.st { font-size: 10px; padding: 2px 8px; border-radius: 999px; white-space: nowrap; }
.st.v { background: #f9efef; color: var(--bad); border: 1px solid #e0c2c4; }
.st.p { background: var(--surface-2); color: var(--muted); border: 1px solid var(--line); }
.und { color: var(--warn); font-style: normal; font-size: 10px; }
.ok { color: var(--good); font-size: 12px; }

.guide { font-size: 11.5px; }
.ghead, .grow { display: grid; grid-template-columns: 48px minmax(0, 1fr) 54px 50px 34px;
  align-items: center; column-gap: 8px; }
.ghead { padding: 0 0 5px; border-bottom: 1px solid var(--line-2); }
.ghead span { font-family: var(--font-num); font-size: 9px; letter-spacing: 1.2px;
  text-transform: uppercase; color: var(--dim); }
.grow { padding: 5px 0 7px; border-bottom: 1px solid var(--line);
  grid-template-rows: auto 2px; row-gap: 5px; }
.grow:last-child { border-bottom: none; }
.gname { min-width: 0; }
.gname b { font-size: 11.5px; color: var(--ink); font-weight: 500; display: block;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* 余量条压在整行底部，横贯全宽：不挤名称列，又能比出"压线"还是"差得远" */
.gbar { grid-column: 1 / -1; height: 2px; border-radius: 2px; background: var(--surface-3);
  display: block; overflow: hidden; }
.gbar em { display: block; height: 100%; border-radius: 2px; background: var(--line-2);
  transition: width .5s cubic-bezier(.2, .8, .2, 1); }
.gbar em.ok { background: #9bbfa8; }
.gbar em.mid { background: #d8bd8f; }
.gbar em.bad { background: #d6a5a9; }
.rt { text-align: right; }
.num { font-family: var(--font-num); font-variant-numeric: tabular-nums; }
.gval { font-size: 13px; color: var(--ink); }
.gval.na { color: var(--dim); }
.gref { font-size: 11px; color: var(--dim); }

.clusters { list-style: none; display: flex; flex-direction: column; gap: 7px; }
.cl { display: flex; gap: 10px; align-items: flex-start; padding: 10px 11px; border-radius: 9px;
  background: var(--surface-2); border: 1px solid var(--line); animation: rise .26s ease both; }
.cl.dim { opacity: .75; }
.lv { flex: none; font-size: 10px; padding: 3px 7px; border-radius: 6px; white-space: nowrap; margin-top: 2px; }
.lv.v { background: #f9efef; color: var(--bad); border: 1px solid #e0c2c4; }
.lv.p { background: var(--surface-3); color: var(--muted); border: 1px solid var(--line); }
.clb { flex: 1; min-width: 0; }
.cltop { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.cltop b { font-size: 13px; color: var(--ink); }
.cltop .num { font-size: 15px; color: var(--accent); }
.clmeta { font-size: 10.5px; color: var(--dim); margin: 3px 0 7px; }
.cellsd { margin-top: 10px; border: 1px dashed var(--line-2); border-radius: 9px; padding: 0 11px; }
.cellsd summary { cursor: pointer; font-size: 11px; color: var(--muted); padding: 8px 0; list-style: none; }
.cellsd summary::-webkit-details-marker { display: none; }
.cellsd summary::before { content: '＋ '; color: var(--accent); }
.cellsd[open] summary::before { content: '－ '; }
.cellsd[open] summary { border-bottom: 1px dashed var(--line); }
.cellsd table { margin-top: 8px; }
.src2 { font-size: 10px; color: var(--dim); line-height: 1.7; margin: 8px 0; }
.co { font-size: 10px; color: var(--dim); white-space: nowrap; text-align: right; }

.fac { list-style: none; font-size: 11.5px; }
.fac li { display: flex; align-items: center; gap: 8px; padding: 6px 0;
  border-bottom: 1px solid var(--line); }
.fac i { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.fac .nm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fac .dim { color: var(--muted); font-size: 11px; }

.how { margin-top: 18px; border: 1px dashed var(--line-2); border-radius: 9px; padding: 0 11px; }
.how summary { cursor: pointer; font-size: 11px; color: var(--muted); padding: 9px 0; list-style: none; }
.how summary::-webkit-details-marker { display: none; }
.how summary::before { content: '＋ '; color: var(--accent); }
.how[open] summary::before { content: '－ '; }
.how[open] summary { border-bottom: 1px dashed var(--line); }
.metrics { list-style: none; padding-top: 9px; }
.metrics li { padding: 8px 0; border-bottom: 1px solid var(--line); }
.metrics .top { display: flex; justify-content: space-between; align-items: baseline; }
.metrics b { font-size: 12px; color: var(--ink); }
.metrics span { font-size: 14px; font-weight: 700; color: var(--accent); }
.metrics code { display: block; font-size: 10px; color: var(--muted); margin-top: 4px;
  font-family: ui-monospace, Consolas, monospace; }
.metrics p { font-size: 10px; color: var(--dim); margin-top: 3px; line-height: 1.6; }
.foot { font-size: 10px; color: var(--dim); margin: 10px 0; line-height: 1.7; }
.src { font-size: 9.5px; color: var(--dim); line-height: 1.7; margin-top: 7px; }
.src strong, .src > strong { color: var(--muted); }
</style>
