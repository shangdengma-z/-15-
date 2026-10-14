<template>
  <div v-if="!m" class="empty">先做一次体检，这里给出全部出网计量与数据溯源</div>
  <div v-else>
    <h3>三栏计量（互不合并）</h3>
    <div class="grid">
      <div class="cell"><b>{{ m.api_calls }}</b><span>真实出网请求</span></div>
      <div class="cell"><b>{{ m.quota_units }}</b><span>折算配额单位</span></div>
      <div class="cell"><b>{{ m.cache_hits }}</b><span>磁盘缓存命中</span></div>
      <div class="cell"><b>{{ m.place_probes }}</b><span>POI 检索翻页</span></div>
    </div>
    <p class="note">批量距离矩阵按「路线对数」折配额，因此配额单位 ≥ 请求次数；两栏永远分开报，
      避免用一次批量调用冒充只花了一个配额。</p>

    <h3>按服务分桶出网</h3>
    <table>
      <thead><tr><th>服务</th><th>接口</th><th>出网</th><th>QPS 上限（个人 AK 实测）</th></tr></thead>
      <tbody>
        <tr v-for="s in SERVICES" :key="s.key">
          <td>{{ s.name }}</td><td class="mono">{{ s.path }}</td>
          <td>{{ m.by_service?.[s.key] || 0 }}</td><td>{{ s.qps }}</td>
        </tr>
      </tbody>
    </table>

    <h3>理想圈 vs 真实路网等时圈（同一把米尺）</h3>
    <div class="iso">
      <figure v-for="p in panels" :key="p.label">
        <svg viewBox="0 0 200 200" :width="p.size" :height="p.size">
          <circle cx="100" cy="100" :r="p.idealPx" class="ideal" />
          <polygon :points="p.polyPx" class="real" />
          <line x1="100" y1="100" :x2="100 + p.idealPx" y2="100" class="ruler" />
        </svg>
        <figcaption>{{ p.label }} 分钟
          <span>真实面积 {{ p.area }} km²<br />理想圆 πr²={{ p.idealArea }} km²<br />
            达成率 {{ p.ratio }}%</span></figcaption>
      </figure>
    </div>
    <p class="note">虚线是「步速 × 时间」的直线理想圆，实线是百度步行路径规划实测出的等时环。
      两者之差就是红绿灯、天桥、围墙与河道绕行的代价——这正是本项目不用直线距离做判据的原因。</p>

    <h3>数据溯源</h3>
    <ul class="prov">
      <li>data_mode：<b :class="m.data_mode === 'real' ? 'ok' : 'warn'">{{ m.data_mode }}</b></li>
      <li>evidence_id：<span class="mono">{{ m.evidence_id }}</span>
        <em>（对采样中心、射线数、半径与全部实测耗时取 sha256 前 16 位；改任一采样点即变）</em></li>
      <li>采样：{{ m.sampling.rays }} 条射线 × {{ m.sampling.radii?.join('/') }} m，
        盲区网格 {{ m.sampling.grid_step_m }} m，核验上限 {{ m.sampling.max_candidates }} 点 ×
        每类最近 {{ m.sampling.verify_nearest }} 个设施</li>
      <li>限流重试 {{ m.retry_count }} 次{{ m.degraded ? ' · 存在直线兜底点，已显式标注降级' : ' · 无降级点' }}</li>
      <li v-if="m.matrix_fallback">距离矩阵未开通，已熔断改用并发路线规划（结论口径不变，耗时上升）</li>
      <li v-if="m.quota_exhausted">今日步行配额已耗尽：后续请求只走缓存，页面不再反复出网</li>
    </ul>

    <h3>接口调用策略（赛题第 3 条要求）</h3>
    <ol class="strat">
      <li>SN 数字签名（AK+SK）走服务端，浏览器端 AK 只负责底图，密钥不出现在前端源码</li>
      <li>每服务独立令牌桶 + 并发闸：步行 3 QPS 是硬瓶颈，地点检索放开到 25 QPS</li>
      <li>status=4/401 视为秒级突发，0.5s 起指数退避；status=10 日配额耗尽即熔断到进程结束</li>
      <li>响应键入磁盘缓存（键含 mock/real 前缀），重复演示 0 出网</li>
      <li>等时圈与 POI 检索并发发起；距离矩阵一次批量替代 N 次单条路线</li>
      <li>降级分层：单点直线兜底 → 矩阵熔断改并发路线 → 配额耗尽只读缓存，每层都在报告里显式标注</li>
    </ol>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ result: Object })
const m = computed(() => props.result?.meta)

const SERVICES = [
  { key: 'direction', name: '步行路径规划', path: '/direction/v2/walking/', qps: '3' },
  { key: 'distancematrix', name: '批量步行距离矩阵', path: '/distancematrix/v1/walking/', qps: '3（按路线对数折算）' },
  { key: 'place', name: '圆形区域 POI 检索', path: '/place/v2/search', qps: '25（实测单页静默压到 20 条）' },
  { key: 'geocoding', name: '正/逆地理编码', path: '/geocoding/v3/、/reverse_geocoding/v3/', qps: '8' }
]

const SPEED = 1.25   // m/s，与后端 REFERENCE_SPEED 对齐
const RINGS = [['5', 300], ['10', 600], ['15', 900]]

function metersFrom(center, pt) {
  const dLat = (pt[1] - center.lat) * 111320
  const dLng = (pt[0] - center.lng) * 111320 * Math.cos(center.lat * Math.PI / 180)
  return Math.sqrt(dLat * dLat + dLng * dLng)
}

const panels = computed(() => {
  const res = props.result
  if (!res) return []
  const maxM = Math.max(1200, ...Object.values(res.rings).flatMap(r => r.map(p => metersFrom(res.center, p))))
  const scale = 88 / maxM                      // px per meter
  return RINGS.map(([label, sec]) => {
    const ring = res.rings[label] || []
    const idealM = SPEED * sec
    const area = ringArea(ring, res.center)
    const idealArea = Math.round(Math.PI * (idealM / 1000) ** 2 * 100) / 100
    return {
      label, idealPx: Math.round(idealM * scale), area,
      idealArea, ratio: idealArea ? Math.round(area / idealArea * 100) : 0,
      size: 150,
      polyPx: ring.map(p => {
        const dx = (p[0] - res.center.lng) * 111320 * Math.cos(res.center.lat * Math.PI / 180) * scale
        const dy = -(p[1] - res.center.lat) * 111320 * scale
        return `${(100 + dx).toFixed(1)},${(100 + dy).toFixed(1)}`
      }).join(' ')
    }
  })
})

function ringArea(ring, center) {
  if (!ring.length) return 0
  const kx = 111.32 * Math.cos(center.lat * Math.PI / 180), ky = 111.32
  let s = 0
  ring.forEach(([x1, y1], i) => { const [x2, y2] = ring[(i + 1) % ring.length]
    s += x1 * kx * y2 * ky - x2 * kx * y1 * ky })
  return Math.round(Math.abs(s / 2) * 100) / 100
}
</script>

<style scoped>
.empty { color: var(--muted); text-align: center; padding-top: 54px; font-size: 12.5px; }
h3 { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px;
  margin: 16px 0 7px; font-weight: 600; }
.grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 7px; }
.cell { background: linear-gradient(160deg, #f8fafd, #eef3fb); border: 1px solid var(--line);
  border-radius: 12px; padding: 10px 4px; text-align: center; }
.cell b { font-size: 17px; display: block; color: var(--ink); font-variant-numeric: tabular-nums; }
.cell span { font-size: 9.5px; color: var(--muted); }
table { width: 100%; border-collapse: collapse; font-size: 11px; }
th { text-align: left; color: var(--muted); font-weight: 500; border-bottom: 1px solid var(--line); }
td { padding: 6px 3px; border-bottom: 1px solid #f1f4f9; }
.mono { font-family: ui-monospace, Consolas, monospace; color: #56657f; font-size: 10px; }
.iso { display: flex; gap: 6px; }
figure { flex: 1; text-align: center; margin: 0; background: #fbfcfe; border: 1px solid var(--line);
  border-radius: 14px; padding: 8px 4px; }
figcaption { font-size: 11px; line-height: 1.55; font-weight: 600; color: var(--ink); }
figcaption span { color: var(--muted); font-size: 9.5px; font-weight: 400; }
.ideal { fill: none; stroke: #c3cfe2; stroke-width: 1.4; stroke-dasharray: 4 3; }
.real { fill: rgba(47, 111, 237, .14); stroke: #2f6fed; stroke-width: 1.6; }
.ruler { stroke: #12b26b; stroke-width: 1; }
.note { font-size: 10px; color: var(--muted); line-height: 1.7; margin-top: 8px; }
.prov { list-style: none; font-size: 11px; line-height: 1.95; }
.prov b.ok { color: #0d7a49; } .prov b.warn { color: #9a5b00; }
.prov em { color: var(--muted); font-style: normal; font-size: 10px; }
.strat { font-size: 11px; line-height: 1.9; padding-left: 18px; color: #56657f; }
.strat li { margin-bottom: 3px; }
</style>
