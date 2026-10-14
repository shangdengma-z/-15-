<template>
  <div v-if="!sc" class="empty">先做一次体检，这里给出时间轴与人群轴的情景对比<br />
    <span>（同一批实测耗时重算，零新增 API 调用）</span></div>
  <div v-else>
    <div class="badge">api_calls_for_scenarios = {{ sc.api_calls_for_scenarios }}</div>

    <h3>时间阈值对比</h3>
    <table>
      <thead><tr><th>情景</th><th>面积 km²</th><th>圈内设施</th><th>外推顶点</th><th>置信</th><th></th></tr></thead>
      <tbody>
        <tr v-for="(s, k) in sc.by_minutes" :key="k" :class="{ on: picked === k }">
          <td>{{ k }}</td><td>{{ s.area_km2 }}</td><td>{{ s.facilities_inside }}</td>
          <td :class="{ warn: s.vertex_kind.extrapolated > s.vertex_kind.measured }">
            {{ s.vertex_kind.extrapolated }}</td>
          <td>{{ s.confidence }}</td>
          <td><button class="loc" @click="show(s, k)">上地图</button></td>
        </tr>
      </tbody>
    </table>

    <h3>人群公平性（同一片路网，不同步速）</h3>
    <table>
      <thead><tr><th>人群</th><th>步速 m/s</th><th>等价阈值(秒)</th><th>可达面积</th><th>圈内设施</th><th></th></tr></thead>
      <tbody>
        <tr v-for="(s, k) in sc.by_crowd" :key="k" :class="{ on: picked === k }">
          <td>{{ CROWD_NAMES[k] }}</td><td>{{ s.speed_mps }}</td><td>{{ s.target_sec }}</td>
          <td>{{ s.area_km2 }} km²</td><td>{{ s.facilities_inside }}</td>
          <td><button class="loc" @click="show(s, k)">上地图</button></td>
        </tr>
      </tbody>
    </table>
    <p class="gap">老年人相对成人的可达面积缺口：
      <b :class="gapClass">{{ areaGap }}</b>
      <span>—— 同一社区、同一张路网，只换步速假设。</span></p>

    <h3>逐类别可达数量随情景变化</h3>
    <div ref="el" class="chart"></div>
    <p class="note">口径：面积来自等时环鞋带公式；「外推顶点」是采样半径不足以覆盖该阈值时的估计，
      数量越大说明该情景已超出实测范围，只作趋势不作结论。</p>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({ result: Object })
const emit = defineEmits(['show-ring'])
const el = ref(null)
let chart = null
const picked = ref('')

const CROWD_NAMES = { adult: '成人', elderly: '老年人', child: '儿童' }
const sc = computed(() => props.result?.scenarios)
const areaGap = computed(() => {
  const a = sc.value?.by_crowd?.adult?.area_km2, e = sc.value?.by_crowd?.elderly?.area_km2
  if (!a) return '—'
  return `−${Math.round((1 - e / a) * 100)}%（${e} vs ${a} km²）`
})
const gapClass = computed(() => (sc.value?.by_crowd?.elderly?.area_km2 || 0) <
  (sc.value?.by_crowd?.adult?.area_km2 || 0) * 0.6 ? 'bad' : 'mid')

watch(sc, async () => { await nextTick(); draw() }, { immediate: true })

function draw() {
  if (!el.value || !sc.value) return
  chart?.dispose()
  chart = echarts.init(el.value)
  const rows = [...Object.entries(sc.value.by_minutes), ...Object.entries(sc.value.by_crowd)]
  const cats = Object.keys(sc.value.by_minutes['15min'].per_category)
  chart.setOption({
    grid: { left: 8, right: 12, top: 30, bottom: 8, containLabel: true },
    legend: { top: 0, itemWidth: 10, textStyle: { fontSize: 10 } },
    xAxis: { type: 'category', data: rows.map(([k]) => k), axisLabel: { fontSize: 10, rotate: 30 } },
    yAxis: { type: 'value', name: '圈内设施数', nameTextStyle: { fontSize: 10 } },
    series: cats.map((c, i) => ({
      name: rows[0][1].per_category[c].name, type: 'line', smooth: true, symbolSize: 6,
      data: rows.map(([, s]) => s.per_category[c].inside), lineWidth: 2,
      itemStyle: { color: ['#e5484d', '#8e4ec6', '#f76b15', '#0091ff'][i] }
    }))
  })
}

function show(s, k) { picked.value = k; emit('show-ring', s.ring) }
onBeforeUnmount(() => chart?.dispose())
</script>

<style scoped>
.empty { color: var(--muted); text-align: center; padding-top: 54px; font-size: 12.5px; line-height: 2; }
.empty span { font-size: 11px; }
h3 { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px;
  margin: 16px 0 7px; font-weight: 600; }
table { width: 100%; border-collapse: collapse; font-size: 11px; }
th { text-align: left; color: var(--muted); font-weight: 500; border-bottom: 1px solid var(--line); }
td { padding: 6px 3px; border-bottom: 1px solid #f1f4f9; font-variant-numeric: tabular-nums; }
tr.on td { background: #f2f7ff; }
.warn { color: var(--bad); font-weight: 700; }
.loc { width: auto; padding: 3px 9px; font-size: 10px; border-radius: 7px; background: #eef2fb;
  color: var(--brand); box-shadow: none; }
.badge { display: inline-block; font-size: 10px; background: #e4f7ee; color: #0d7a49;
  border-radius: 999px; padding: 4px 10px; margin-bottom: 4px; font-weight: 600; }
.gap { font-size: 12px; margin-top: 10px; }
.gap b.bad { color: var(--bad); font-size: 14px; }
.gap b.mid { color: var(--warn); }
.gap span { color: var(--muted); font-size: 10.5px; display: block; margin-top: 2px; }
.chart { height: 186px; }
.note { font-size: 10px; color: var(--muted); line-height: 1.7; margin-top: 8px; }
</style>
