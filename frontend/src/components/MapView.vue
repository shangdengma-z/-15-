<template>
  <div class="map-wrap">
    <div ref="el" class="map-root"></div>
    <div v-if="loadError" class="fallback">{{ loadError }}</div>

    <div class="toolbar">
      <button v-for="t in MAP_TYPES" :key="t.key" :class="{ on: mapType === t.key }"
              @click="switchType(t.key)">{{ t.name }}</button>
    </div>

    <div class="layers" v-if="result">
      <div class="lt">图层</div>
      <label v-for="l in LAYERS" :key="l.key">
        <input type="checkbox" :checked="visible[l.key]" @change="toggle(l.key)" /> {{ l.name }}
      </label>
      <div class="legend">
        <span><i class="dot" style="background:#e5484d"></i>医疗</span>
        <span><i class="dot" style="background:#8e4ec6"></i>教育</span>
        <span><i class="dot" style="background:#f76b15"></i>购物</span>
        <span><i class="dot" style="background:#0091ff"></i>养老</span>
        <span><i class="ring r5"></i>5min</span>
        <span><i class="ring r10"></i>10min</span>
        <span><i class="ring r15"></i>15min</span>
        <span><i class="sq"></i>服务盲区</span>
      </div>
      <div class="hint">右键/长按地图可设为中心点并体检</div>
    </div>

    <div class="scale" v-if="result">
      15 分钟圈面积 <b>{{ result.area_km2 }}</b> km² · 实测绕行系数 <b>{{ detourFactor }}</b>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { loadBmapGL } from '../bmap'

const props = defineProps({ center: Array, result: Object, actions: Array, focus: Object, overlayRing: Array })
const emit = defineEmits(['pick', 'run'])
const el = ref(null)
const loadError = ref('')
const mapType = ref('normal')
const visible = ref({ rings: true, heat: true, pois: true, blind: true, agent: true })

const MAP_TYPES = [{ key: 'normal', name: '标准' }, { key: 'satellite', name: '卫星' },
                   { key: 'earth', name: '3D' }]
const LAYERS = [{ key: 'rings', name: '等时圈 5/10/15min' }, { key: 'heat', name: '步行耗时面' },
                { key: 'pois', name: '民生设施' }, { key: 'blind', name: '服务盲区' },
                { key: 'agent', name: '智能体标注' }]

let BMapGL = null
let map = null
let info = null
const groups = { rings: [], heat: [], pois: [], blind: [] }
let agentOverlays = []
let centerMarker = null
let highlightRing = null

const RING_STYLE = {
  '5': { strokeColor: '#f5a623', fillColor: '#f5a623', fillOpacity: 0.18 },
  '10': { strokeColor: '#2f6fed', fillColor: '#2f6fed', fillOpacity: 0.12 },
  '15': { strokeColor: '#17b26a', fillColor: '#17b26a', fillOpacity: 0.06 }
}
const CAT_COLORS = { medical: '#e5484d', education: '#8e4ec6', shopping: '#f76b15', elderly: '#0091ff' }
const CAT_NAMES = { medical: '基础医疗', education: '教育', shopping: '购物', elderly: '养老' }
const ACTION_STYLE = { center: { color: '#2f6fed', r: 90 }, facility: { color: '#17b26a', r: 80 },
                       poi: { color: '#8e4ec6', r: 55 } }

const detourFactor = computed(() => props.result?.detour?.detour_factor_p50 ?? '—')

onMounted(async () => {
  try {
    BMapGL = await loadBmapGL()
  } catch (e) {
    loadError.value = '地图加载失败：Docker 走根目录 .env 的 VITE_BMAP_AK，本地 dev 走 frontend/.env.local；'
      + '留空则无密钥加载，底图可能报错但分析流程不受影响。'
    return
  }
  map = new BMapGL.Map(el.value)
  map.centerAndZoom(new BMapGL.Point(116.397, 39.908), 13)
  map.enableScrollWheelZoom(true)
  map.setDisplayOptions({ skyColors: null })
  info = new BMapGL.InfoWindow('', { width: 250, height: 110 })
  map.addEventListener('click', (e) => {
    if (e.target && e.target._poiKey !== undefined) return  // 点设施走弹窗，不改中心
    setCenter([e.latlng.lat, e.latlng.lng])
  })
  const onRight = (e) => {
    if (!e.latlng) return
    setCenter([e.latlng.lat, e.latlng.lng])
    emit('run', [e.latlng.lat, e.latlng.lng])   // 右键直接体检，录视频时少一次点击
  }
  map.addEventListener('rightclick', onRight)
})

function setCenter(c) {
  emit('pick', c)
}

function switchType(key) {
  mapType.value = key
  if (!map) return
  const t = { normal: BMapGL.NORMAL_MAP, satellite: BMapGL.SATELLITE_MAP, earth: BMapGL.EARTH_MAP }[key]
  if (t !== undefined) map.setMapType(t)
  if (key === 'earth') { map.setTilt && map.setTilt(50) } else { map.setTilt && map.setTilt(0) }
}

watch(() => props.center, (c) => { if (c && map) { setCenterMarker(c); map.panTo(new BMapGL.Point(c[1], c[0])) } })
watch(() => props.result, renderOverlays)
watch(() => props.actions, renderActions)
watch(() => props.focus, (f) => f && locate(f))
watch(() => props.overlayRing, drawScenarioRing)

let scenarioOverlay = null

// 情景对比选中的环：叠在原环之上，用紫色区分，切换情景时只换这一个覆盖物
function drawScenarioRing(ring) {
  if (!map) return
  if (scenarioOverlay) { map.removeOverlay(scenarioOverlay); scenarioOverlay = null }
  if (!ring || !ring.length) return
  scenarioOverlay = new BMapGL.Polygon(ring.map(([lng, lat]) => P(lng, lat)),
    { strokeColor: '#8e4ec6', strokeWeight: 3, fillColor: '#8e4ec6', fillOpacity: 0.1 })
  map.addOverlay(scenarioOverlay)
}

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
  if (t <= 300) return '#17b26a'
  if (t <= 600) return '#f5a623'
  if (t <= 900) return '#e5484d'
  return '#7a8699'
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
      openInfo(popupHtml(`${label} 分钟可达圈`, [`顶点数 ${ring.length}`,
        `面积 ${areaOf(ring)} km²`, `圈外多边形由射线插值生成，实测顶点见「自证」页`], ''),
        P(ring[0][0], ring[0][1]))
    })
    addLayer('rings', poly)
  })

  res.heat.forEach(h => {
    const c = new BMapGL.Circle(P(h.lng, h.lat), 120,
      { strokeOpacity: 0, fillColor: heatColor(h.t), fillOpacity: 0.35, clickable: true })
    const straight = dist(center, [h.lat, h.lng])
    c.addEventListener('click', () => openInfo(popupHtml(`步行采样点`,
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

  res.blindspots.forEach((b, i) => {
    const items = (b.missing || b.missing_preview || []).join('、')
    const m = new BMapGL.Circle(P(b.lng, b.lat), 130, { strokeWeight: 1.5, strokeColor: '#d43b3b',
      fillColor: b.verified ? '#9aa4b5' : '#c9d1dd', fillOpacity: 0.55, clickable: true })
    m.addEventListener('click', () => openInfo(popupHtml(
      `${b.verified ? '已核验盲区' : '疑似盲区（未过矩阵核验）'}`,
      [`缺乏：${items || '—'}`, `无法判定：${(b.undetermined || []).join('、') || '—'}`],
      walkLink(b.lat, b.lng, b.lat, b.lng)), P(b.lng, b.lat)))
    addLayer('blind', m)
    // 盲区常常成片相邻，文字标注只给前 6 处，否则标签互相压住反而看不清
    if (i < 6) {
      const lbl = new BMapGL.Label(items ? `盲区: ${items}` : '待判定',
        { position: P(b.lng, b.lat), offset: new BMapGL.Size(8, -8) })
      lbl.setStyle({ fontSize: '11px', color: '#d43b3b', border: 'none', background: 'rgba(255,255,255,.85)' })
      addLayer('blind', lbl)
    }
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

// 报告/清单里的"定位"按钮：蓝色目标环 + 名称标签 + 飞过去，表格与地图双向对得上
function locate(f) {
  if (!map) return
  if (highlightRing) { map.removeOverlay(highlightRing); highlightRing = null }
  const p = P(f.lng, f.lat)
  highlightRing = new BMapGL.Circle(p, 150, { strokeWeight: 3, strokeColor: '#2f6fed',
    fillColor: '#2f6fed', fillOpacity: 0.08 })
  const lbl = new BMapGL.Label(f.label || '目标', { position: p, offset: new BMapGL.Size(12, -12) })
  lbl.setStyle({ fontSize: '11px', color: '#14243d', border: '1px solid #2f6fed', background: '#fff' })
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
      lbl.setStyle({ fontSize: '11px', color: st.color, border: 'none', background: 'rgba(255,255,255,.9)' })
      map.addOverlay(lbl); agentOverlays.push(lbl)
    }
  })
  if (visible.value.agent === false) agentOverlays.forEach(o => map.removeOverlay(o))
  const first = items.find(a => a.kind !== 'blindspot')
  if (first && !props.result) map.panTo(P(first.lng, first.lat))
}
</script>

<style scoped>
.map-wrap { position: relative; width: 100%; height: 100%; }
.map-root { width: 100%; height: 100%; background: #dde4ee; }
.fallback { position: absolute; inset: 0; padding: 40px; font-size: 14px; color: #5a6577; background: #eef2f8; }
.toolbar { position: absolute; top: 12px; right: 12px; display: flex; gap: 4px; z-index: 6;
  background: rgba(255,255,255,.82); backdrop-filter: blur(8px); padding: 4px; border-radius: 12px;
  box-shadow: 0 6px 18px rgba(15,32,60,.16); }
.toolbar button { width: auto; padding: 6px 12px; font-size: 11.5px; border-radius: 9px;
  background: transparent; color: #4a5a72; box-shadow: none; font-weight: 500; }
.toolbar button.on { background: linear-gradient(135deg, #2f6fed, #6d5efc); color: #fff; }
.layers { position: absolute; top: 12px; left: 12px; z-index: 6; width: 186px;
  background: rgba(255,255,255,.88); backdrop-filter: blur(10px);
  border-radius: 14px; padding: 12px; border: 1px solid rgba(255,255,255,.7);
  box-shadow: 0 8px 24px rgba(15,32,60,.16); }
.layers .lt { font-size: 10px; font-weight: 700; letter-spacing: 1.2px; text-transform: uppercase;
  color: #7c8aa0; margin-bottom: 7px; }
.layers label { display: flex; align-items: center; gap: 7px; font-size: 11.5px; margin: 5px 0;
  cursor: pointer; color: #2c3a4f; }
.layers label:hover { color: #2f6fed; }
.legend { display: flex; flex-wrap: wrap; gap: 5px 8px; margin-top: 10px; padding-top: 9px;
  border-top: 1px dashed #dbe2ec; font-size: 10px; color: #66748c; }
.legend span { display: inline-flex; align-items: center; gap: 3px; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.ring { width: 10px; height: 10px; border-radius: 50%; border: 2px solid; display: inline-block; }
.ring.r5 { border-color: #f5a623; } .ring.r10 { border-color: #2f6fed; } .ring.r15 { border-color: #17b26a; }
.sq { width: 9px; height: 9px; background: #9aa4b5; border: 1px solid #d43b3b; display: inline-block; }
.hint { font-size: 9.5px; color: #93a1b6; margin-top: 8px; line-height: 1.5; }
.scale { position: absolute; bottom: 14px; left: 12px; z-index: 6; font-size: 11px; color: #2c3a4f;
  background: rgba(255,255,255,.88); backdrop-filter: blur(8px);
  padding: 7px 12px; border-radius: 999px; box-shadow: 0 6px 18px rgba(15,32,60,.14); }
.scale b { color: #2f6fed; font-variant-numeric: tabular-nums; }
</style>
