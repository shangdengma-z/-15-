<template>
  <div class="map-wrap">
    <div ref="el" class="map-root"></div>
    <div v-if="loadError" class="fallback">
      <b>底图未能加载</b>
      <p>{{ loadError }}</p>
    </div>

    <div class="dock" v-if="result">
      <span class="area">
        <em class="eyebrow">15 分钟可达</em>
        <b class="num">{{ result.area_km2 }}</b> km²
      </span>
      <span class="sep"></span>
      <label v-for="l in LAYERS" :key="l.key" :class="{ off: !visible[l.key] }">
        <input type="checkbox" :checked="visible[l.key]" @change="toggle(l.key)" /> {{ l.name }}
      </label>
      <span class="sep"></span>
      <span class="lg"><i class="r r5"></i>5′</span>
      <span class="lg"><i class="r r10"></i>10′</span>
      <span class="lg"><i class="r r15"></i>15′</span>
      <span class="lg"><i class="dot" style="background:#b06a6f"></i>医疗</span>
      <span class="lg"><i class="dot" style="background:#8b87ad"></i>教育</span>
      <span class="lg"><i class="dot" style="background:#c08a6a"></i>购物</span>
      <span class="lg"><i class="dot" style="background:#6f9bb5"></i>养老</span>
      <span class="lg"><i class="bs"></i>盲区</span>
    </div>

    <div v-else class="hintbar">单击选点 · 右键立即体检 · 拖动中心标记重跑</div>

    <slot />
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { loadBmapGL } from '../bmap'

const props = defineProps({ center: Array, result: Object, actions: Array, focus: Object })
const emit = defineEmits(['pick', 'run'])
const el = ref(null)
const loadError = ref('')
const visible = ref({ rings: true, heat: true, pois: true, blind: true, agent: true })

const LAYERS = [{ key: 'rings', name: '等时圈' }, { key: 'heat', name: '步行耗时面' },
                { key: 'pois', name: '圈内设施' }, { key: 'blind', name: '服务盲区' },
                { key: 'agent', name: '推荐点位' }]

let BMapGL = null
let map = null
let info = null
const groups = { rings: [], heat: [], pois: [], blind: [] }
let agentOverlays = []
let centerMarker = null
let highlightRing = null

const RING_STYLE = {
  '5': { strokeColor: '#cfa257', fillColor: '#cfa257', fillOpacity: 0.16 },
  '10': { strokeColor: '#7fa6c0', fillColor: '#7fa6c0', fillOpacity: 0.12 },
  '15': { strokeColor: '#7ea87e', fillColor: '#7ea87e', fillOpacity: 0.07 }
}
const CAT_COLORS = { medical: '#b06a6f', education: '#8b87ad', shopping: '#c08a6a', elderly: '#6f9bb5' }
const CAT_NAMES = { medical: '基础医疗', education: '教育', shopping: '购物', elderly: '养老' }
const ACTION_STYLE = { center: { color: '#6f9bb5', r: 90 }, facility: { color: '#4f8a68', r: 80 },
                       poi: { color: '#8b87ad', r: 55 }, site: { color: '#b98a4b', r: 130 } }

onMounted(async () => {
  try {
    BMapGL = await loadBmapGL()
  } catch (e) {
    loadError.value = '地图密钥没拿到：线上看后端 /env.js 是否给了 VITE_BMAP_AK，本地 dev 看 frontend/.env.local。'
    return
  }
  map = new BMapGL.Map(el.value)
  map.centerAndZoom(new BMapGL.Point(116.397, 39.908), 13)
  map.enableScrollWheelZoom(true)
  try {
    // 缩放控件挪到左栏与右栏之间的空档，别压在浮层面板下面
    map.setDisplayOptions({ ctrlButton: { left: '42%', top: 'auto', bottom: '92px' } })
  } catch (e) { /* 不支持时保持默认位置 */ }
  info = new BMapGL.InfoWindow('', { width: 250, height: 110 })
  map.addEventListener('click', (e) => {
    if (e.target && e.target._poiKey !== undefined) return  // 点设施走弹窗，不改中心
    setCenter([e.latlng.lat, e.latlng.lng])
  })
  map.addEventListener('rightclick', (e) => {
    if (!e.latlng) return
    setCenter([e.latlng.lat, e.latlng.lng])
    emit('run', [e.latlng.lat, e.latlng.lng])   // 右键直接体检，录视频时少一次点击
  })
})

function setCenter(c) { emit('pick', c) }

watch(() => props.center, (c) => { if (c && map) { setCenterMarker(c); map.panTo(new BMapGL.Point(c[1], c[0])) } })
watch(() => props.result, renderOverlays)
watch(() => props.actions, renderActions)
watch(() => props.focus, (f) => f && locate(f))

const P = (lng, lat) => new BMapGL.Point(lng, lat)

// 中心点可拖：拖动后松手即重算，"换小区"这件事在演示里变成一次手势
function setCenterMarker([lat, lng]) {
  if (!map) return
  if (centerMarker) { map.removeOverlay(centerMarker); centerMarker = null }
  centerMarker = new BMapGL.Marker(P(lng, lat), { enableDragging: true })
  centerMarker.addEventListener('dragend', (e) => {
    const p = e.target.getPosition()
    emit('pick', [p.lat, p.lng])
    emit('run', [p.lat, p.lng])
  })
  map.addOverlay(centerMarker)
}

function toggle(key) {
  visible.value[key] = !visible.value[key]
  if (key === 'agent') {
    visible.value.agent ? agentOverlays.forEach(o => map.addOverlay(o))
                        : agentOverlays.forEach(o => map.removeOverlay(o))
    return
  }
  groups[key].forEach(o => visible.value[key] ? map.addOverlay(o) : map.removeOverlay(o))
}

// 图层开关要在重算后依然生效：渲染时按当前可见性决定是否挂上图，否则勾掉的层会自己长回来
function addLayer(key, ...os) {
  os.forEach(o => {
    groups[key].push(o)
    if (visible.value[key]) map.addOverlay(o)
  })
}

function clearGroups() {
  Object.values(groups).forEach(list => list.forEach(o => map.removeOverlay(o)))
  Object.values(groups).forEach(list => (list.length = 0))
}

function heatColor(t) {
  if (t <= 300) return '#4f8a68'
  if (t <= 600) return '#cfa257'
  if (t <= 900) return '#b06a6f'
  return '#93a099'
}

function walkLink(lat, lng, tlat, tlng) {
  return `https://api.map.baidu.com/dir?origin=${lat},${lng}&destination=${tlat},${tlng}`
    + '&type=walk&coord_type=bd09ll&output=api'
}

function popupHtml(title, rows, link) {
  return `<div style="font-size:12px;line-height:1.7">
    <b style="font-size:13px">${title}</b>
    ${rows.map(r => `<div>${r}</div>`).join('')}
    ${link ? `<a href="${link}" target="_blank" rel="noopener">↗ 百度官方步行路线核对</a>` : ''}
  </div>`
}

function renderOverlays(res) {
  if (!res || !map) return
  clearGroups()
  const center = [res.center.lat, res.center.lng]

  Object.entries(res.rings).forEach(([label, ring]) => {
    const poly = new BMapGL.Polygon(ring.map(([lng, lat]) => P(lng, lat)),
                                    { ...RING_STYLE[label], strokeWeight: 2 })
    poly.addEventListener('click', () => {
      openInfo(popupHtml(`${label} 分钟可达圈`, [`顶点数 ${ring.length}`, `面积 ${areaOf(ring)} km²`,
        '圈形由 18 条射线实测耗时插值生成'], ''), P(ring[0][0], ring[0][1]))
    })
    addLayer('rings', poly)
  })

  res.heat.forEach(h => {
    const c = new BMapGL.Circle(P(h.lng, h.lat), 120,
      { strokeOpacity: 0, fillColor: heatColor(h.t), fillOpacity: 0.35, clickable: true })
    const straight = dist(center, [h.lat, h.lng])
    c.addEventListener('click', () => openInfo(popupHtml('步行采样点',
      [`实测耗时 ${Math.round(h.t / 60 * 10) / 10} 分钟`, `路网距离 ${Math.round(h.d || 0)} m`,
       `直线距离 ${Math.round(straight)} m`,
       `绕行系数 ${straight ? (h.d / straight).toFixed(2) : '—'}`], walkLink(center[0], center[1], h.lat, h.lng)),
      P(h.lng, h.lat)))
    addLayer('heat', c)
  })

  Object.entries(res.pois).forEach(([key, list]) => {
    list.forEach(p => {
      const m = new BMapGL.Circle(P(p.lng, p.lat), 60, { strokeWeight: 1, strokeColor: CAT_COLORS[key],
        fillColor: CAT_COLORS[key], fillOpacity: 0.9, clickable: true })
      m._poiKey = key
      m.addEventListener('click', () => openInfo(popupHtml(p.name,
        [`类别 ${CAT_NAMES[key]}`, `地址 ${p.address || '—'}`,
         `距中心 ${Math.round(dist(center, [p.lat, p.lng]))} m`],
        walkLink(center[0], center[1], p.lat, p.lng)), P(p.lng, p.lat)))
      addLayer('pois', m)
    })
  })

  res.blindspots.forEach((b) => {
    const items = (b.missing || b.missing_preview || []).join('、')
    const m = new BMapGL.Circle(P(b.lng, b.lat), 130, { strokeWeight: 1.5, strokeColor: '#b06a6f',
      fillColor: b.verified ? '#a8b3ab' : '#cbd4cc', fillOpacity: 0.6, clickable: true })
    m.addEventListener('click', () => openInfo(popupHtml(
      b.verified ? '已核验盲区' : '待核验盲区（未过距离核验）',
      [`缺乏：${items || '—'}`, `无法判定：${(b.undetermined || []).join('、') || '—'}`],
      walkLink(b.lat, b.lng, b.lat, b.lng)), P(b.lng, b.lat)))
    addLayer('blind', m)
  })

  // 一格一条文字会互相压住且内容重复：标注只在「缺口片区」质心处出现一次，
  // 逐格圆点仍然全部画出，地图上的连片形状本身才是信息
  ;(res.blindspot_clusters || []).forEach(c => {
    const items = (c.missing || []).join('、')
    const lbl = new BMapGL.Label(`${items || '待判定'} · ${c.cells} 格`,
      { position: P(c.lng, c.lat), offset: new BMapGL.Size(-44, -10) })
    lbl.setStyle({ fontSize: '11px', color: '#8c4f54', border: '1px solid #e0c2c4', padding: '2px 6px',
      background: '#fdf7f7', borderRadius: '4px' })
    addLayer('blind', lbl)
  })

  const b = new BMapGL.Bounds(
    P(Math.min(...res.rings['15'].map(p => p[0])), Math.min(...res.rings['15'].map(p => p[1]))),
    P(Math.max(...res.rings['15'].map(p => p[0])), Math.max(...res.rings['15'].map(p => p[1])))
  )
  map.setViewport(b)
}

function openInfo(html, point) {
  if (!info) { info = new BMapGL.InfoWindow('', { width: 250, height: 120 }) }
  info.setContent(html)
  map.openInfoWindow(info, point)
}

function dist(a, b) {
  const R = 6371000, p1 = a[0] * Math.PI / 180, p2 = b[0] * Math.PI / 180
  const dp = (b[0] - a[0]) * Math.PI / 180, dl = (b[1] - a[1]) * Math.PI / 180
  const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}

function areaOf(ring) {
  const midLat = ring.reduce((s, p) => s + p[1], 0) / ring.length
  const kx = 111.32 * Math.cos(midLat * Math.PI / 180), ky = 111.32
  let s = 0
  ring.forEach(([x1, y1], i) => { const [x2, y2] = ring[(i + 1) % ring.length]
    s += x1 * kx * y2 * ky - x2 * kx * y1 * ky })
  return Math.round(Math.abs(s / 2) * 100) / 100
}

// 报告/清单里的"定位"按钮：目标环 + 名称标签 + 飞过去，表格与地图双向对得上
function locate(f) {
  if (!map) return
  if (highlightRing) { map.removeOverlay(highlightRing); highlightRing = null }
  const p = P(f.lng, f.lat)
  highlightRing = new BMapGL.Circle(p, 150, { strokeWeight: 3, strokeColor: '#4e807a',
    fillColor: '#4e807a', fillOpacity: 0.1 })
  const lbl = new BMapGL.Label(f.label || '目标', { position: p, offset: new BMapGL.Size(12, -12) })
  lbl.setStyle({ fontSize: '11px', color: '#22302b', border: '1px solid #4e807a', background: '#fff' })
  map.addOverlay(highlightRing); map.addOverlay(lbl)
  setTimeout(() => { map.removeOverlay(lbl) }, 4000)
  map.centerAndZoom(p, Math.max(map.getZoom(), 15))
}

function renderActions(list) {
  if (!map) return
  agentOverlays.forEach(o => map.removeOverlay(o))
  agentOverlays = []
  const items = list || []
  items.forEach(a => {
    if (a.kind === 'blindspot') return   // 盲区由体检图层统一渲染，避免重叠
    const st = ACTION_STYLE[a.kind] || ACTION_STYLE.poi
    const p = P(a.lng, a.lat)
    const c = new BMapGL.Circle(p, st.r, { strokeWeight: 2, strokeColor: st.color,
      fillColor: st.color, fillOpacity: 0.25 })
    map.addOverlay(c); agentOverlays.push(c)
    if (a.label) {
      const lbl = new BMapGL.Label(a.label, { position: p, offset: new BMapGL.Size(10, -10) })
      lbl.setStyle({ fontSize: '11px', color: st.color, border: 'none', background: 'rgba(255,255,255,.92)' })
      map.addOverlay(lbl); agentOverlays.push(lbl)
    }
  })
  if (visible.value.agent === false) agentOverlays.forEach(o => map.removeOverlay(o))
  const first = items.find(a => a.kind !== 'blindspot')
  if (first && !props.result) map.panTo(P(first.lng, first.lat))
}
</script>

<style scoped>
.map-wrap { position: absolute; inset: 0; }
.map-root { width: 100%; height: 100%; background: #e9efe7; }
.fallback { position: absolute; inset: 0; z-index: 5; display: grid; place-content: center; gap: 8px;
  padding: 40px; text-align: center; background: var(--surface-2); color: var(--muted); }
.fallback b { font-size: 15px; color: var(--text); }
.fallback p { font-size: 12px; max-width: 420px; line-height: 1.8; }

/* 底部图层坞：实色白卡 + 发丝边框，不用模糊 */
.dock {
  position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%); z-index: 10;
  display: flex; align-items: center; gap: 13px; padding: 8px 14px; border-radius: 999px;
  font-size: 11.5px; white-space: nowrap; max-width: calc(100% - 28px);
  background: var(--surface); border: 1px solid var(--line-2); box-shadow: var(--shadow);
}
.dock .area { display: flex; align-items: baseline; gap: 5px; color: var(--muted); }
.dock .area em { font-style: normal; }
.dock .area b { font-size: 18px; color: var(--accent); font-weight: 700; }
.dock .sep { width: 1px; height: 16px; background: var(--line); }
.dock label { display: inline-flex; align-items: center; gap: 5px; cursor: pointer; color: var(--text);
  transition: opacity .15s, color .15s; }
.dock label.off { opacity: .4; }
.dock label:hover { color: var(--accent); }
.lg { display: inline-flex; align-items: center; gap: 4px; font-size: 10px; color: var(--dim); }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.r { width: 11px; height: 11px; border-radius: 50%; border: 2px solid; display: inline-block; }
.r.r5 { border-color: #cfa257; } .r.r10 { border-color: #7fa6c0; } .r.r15 { border-color: #7ea87e; }
.bs { width: 10px; height: 10px; border-radius: 50%; border: 1.5px dashed #b06a6f; display: inline-block; }

.hintbar { position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%); z-index: 10;
  padding: 7px 14px; border-radius: 999px; font-size: 11px; color: var(--muted);
  background: var(--surface); border: 1px solid var(--line); box-shadow: var(--shadow); }

@media (max-width: 1160px) { .dock { flex-wrap: wrap; border-radius: 14px; justify-content: center; } }
@media print { .dock, .hintbar { display: none; } }
</style>
