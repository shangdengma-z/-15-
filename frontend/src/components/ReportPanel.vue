<template>
  <div v-if="!result" class="empty">体检报告将在此显示<br /><span>（点击地图选中心点 → 开始体检）</span></div>
  <div v-else>
    <div class="score">
      <div class="num" :class="scoreClass">{{ result.health_score }}</div>
      <div class="cap">生活圈健康分</div>
    </div>
    <h3>圈内民生设施数量</h3>
    <div ref="barEl" class="chart"></div>
    <h3>各类别覆盖雷达</h3>
    <div ref="radarEl" class="chart"></div>
    <h3>服务盲区（{{ result.blindspots.length }} 处）</h3>
    <ul class="bs">
      <li v-for="(b, i) in result.blindspots.slice(0, 8)" :key="i">
        <b :class="{ v: b.verified }">{{ b.verified ? '已核验' : '疑似' }}</b>
        {{ b.missing?.join('、') || b.missing_preview?.join('、') }} 缺乏
        <span class="coord">{{ b.lat.toFixed(4) }}, {{ b.lng.toFixed(4) }}</span>
      </li>
      <li v-if="!result.blindspots.length" class="ok">未发现服务盲区</li>
    </ul>
    <p class="req">强制核验项：{{ requiredText }}</p>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({ result: Object })
const barEl = ref(null)
const radarEl = ref(null)

const scoreClass = computed(() => {
  const s = props.result?.health_score ?? 0
  return s >= 80 ? 'good' : s >= 60 ? 'mid' : 'bad'
})
const requiredText = computed(() =>
  Object.entries(props.result?.meta?.required_check || {}).map(([k, v]) => `${k}${v}`).join('；')
)

watch(() => props.result, async (res) => {
  if (!res) return
  await nextTick()
  if (!barEl.value || !radarEl.value) return
  const cats = Object.values(res.coverage)
  echarts.init(barEl.value).setOption({
    grid: { left: 8, right: 8, top: 20, bottom: 8, containLabel: true },
    xAxis: { type: 'category', data: cats.map(c => c.name) },
    yAxis: { type: 'value' },
    series: [{
      type: 'bar', barWidth: 26, itemStyle: { color: '#2f6fed', borderRadius: [4, 4, 0, 0] },
      data: cats.map(c => c.inside), label: { show: true, position: 'top' }
    }]
  })
  echarts.init(radarEl.value).setOption({
    radar: {
      indicator: cats.map(c => ({ name: c.name, max: Math.max(...Object.values(res.coverage).map(x => x.inside), 5) })),
      radius: '65%'
    },
    series: [{
      type: 'radar', areaStyle: { color: 'rgba(23,178,106,.25)' }, lineStyle: { color: '#17b26a' },
      data: [{ value: cats.map(c => c.inside) }]
    }]
  })
}, { immediate: true })
</script>

<style scoped>
.empty { color: #7a8699; text-align: center; padding-top: 80px; font-size: 14px; line-height: 2; }
.empty span { font-size: 12px; }
h3 { font-size: 13px; margin: 14px 0 6px; }
.chart { height: 180px; }
.score { text-align: center; padding: 8px 0 4px; }
.num { font-size: 44px; font-weight: 700; }
.num.good { color: #17b26a; } .num.mid { color: #f5a623; } .num.bad { color: #d43b3b; }
.cap { font-size: 12px; color: #7a8699; }
.bs { list-style: none; font-size: 12px; line-height: 1.9; }
.bs b { color: #9aa4b5; margin-right: 4px; }
.bs b.v { color: #d43b3b; }
.coord { color: #aab3c2; float: right; }
.ok { color: #17b26a; }
.req { font-size: 11px; color: #7a8699; margin-top: 10px; }
</style>
