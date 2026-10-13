<template>
  <div ref="el" class="map-root">
    <div v-if="loadError" class="fallback">{{ loadError }}</div>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { loadBmapGL } from '../bmap'

const props = defineProps({ center: Array, result: Object })
const emit = defineEmits(['pick'])
const el = ref(null)
const loadError = ref('')

let BMapGL = null
let map = null
let overlays = []
let centerMarker = null

const RING_STYLE = {
  '5': { strokeColor: '#f5a623', fillColor: '#f5a623', fillOpacity: 0.18 },
  '10': { strokeColor: '#2f6fed', fillColor: '#2f6fed', fillOpacity: 0.12 },
  '15': { strokeColor: '#17b26a', fillColor: '#17b26a', fillOpacity: 0.06 }
}
const CAT_COLORS = { medical: '#e5484d', education: '#8e4ec6', shopping: '#f76b15', elderly: '#0091ff' }

onMounted(async () => {
  try {
    BMapGL = await loadBmapGL()
  } catch (e) {
    loadError.value = '地图加载失败：请在 frontend/.env.local 配置 VITE_BMAP_AK（浏览器端 AK）后重启 dev server'
    return
  }
  map = new BMapGL.Map(el.value)
  map.centerAndZoom(new BMapGL.Point(116.397, 39.908), 13)
  map.enableScrollWheelZoom(true)
  map.addEventListener('click', (e) => {
    const { lat, lng } = e.latlng
    emit('pick', [lat, lng])
    setCenterMarker([lat, lng])
  })
})

watch(() => props.center, (c) => { if (c && map) { setCenterMarker(c); map.panTo(new BMapGL.Point(c[1], c[0])) } })
watch(() => props.result, renderOverlays)

function setCenterMarker([lat, lng]) {
  if (!map) return
  if (centerMarker) map.removeOverlay(centerMarker)
  centerMarker = new BMapGL.Marker(new BMapGL.Point(lng, lat))
  map.addOverlay(centerMarker)
}

function clearOverlays() {
  overlays.forEach(o => map.removeOverlay(o))
  overlays = []
}

function heatColor(t) {
  if (t <= 300) return '#17b26a'
  if (t <= 600) return '#f5a623'
  if (t <= 900) return '#e5484d'
  return '#7a8699'
}

function renderOverlays(res) {
  if (!res || !map) return
  clearOverlays()
  const P = (lng, lat) => new BMapGL.Point(lng, lat)

  // 等时圈三层（5/10/15 分钟），由内向外叠加形成等值面
  Object.entries(res.rings).forEach(([label, ring]) => {
    const poly = new BMapGL.Polygon(ring.map(([lng, lat]) => P(lng, lat)), {
      ...RING_STYLE[label], strokeWeight: 2
    })
    map.addOverlay(poly); overlays.push(poly)
  })

  // 热力散点：采样点真实步行耗时着色
  res.heat.forEach(h => {
    const c = new BMapGL.Circle(P(h.lng, h.lat), 120, {
      strokeOpacity: 0, fillColor: heatColor(h.t), fillOpacity: 0.35
    })
    map.addOverlay(c); overlays.push(c)
  })

  // POI 分类标点
  Object.entries(res.pois).forEach(([key, list]) => {
    list.forEach(p => {
      const m = new BMapGL.Circle(P(p.lng, p.lat), 60, {
        strokeWeight: 1, strokeColor: CAT_COLORS[key], fillColor: CAT_COLORS[key], fillOpacity: 0.9
      })
      map.addOverlay(m); overlays.push(m)
    })
  })

  // 服务盲区（灰色区域）
  res.blindspots.forEach(b => {
    const m = new BMapGL.Circle(P(b.lng, b.lat), 130, {
      strokeWeight: 1.5, strokeColor: '#d43b3b', fillColor: '#9aa4b5', fillOpacity: 0.55
    })
    const lbl = new BMapGL.Label(`盲区: ${b.missing?.join('、') || b.missing_preview?.join('、')}`, {
      position: P(b.lng, b.lat), offset: new BMapGL.Size(8, -8)
    })
    lbl.setStyle({ fontSize: '11px', color: '#d43b3b', border: 'none', background: 'rgba(255,255,255,.85)' })
    map.addOverlay(m); map.addOverlay(lbl)
    overlays.push(m, lbl)
  })

  const b = new BMapGL.Bounds(
    P(Math.min(...res.rings['15'].map(p => p[0])), Math.min(...res.rings['15'].map(p => p[1]))),
    P(Math.max(...res.rings['15'].map(p => p[0])), Math.max(...res.rings['15'].map(p => p[1])))
  )
  map.setViewport(b)
}
</script>

<style scoped>
.map-root { width: 100%; height: 100%; background: #dde4ee; }
.fallback { padding: 40px; font-size: 14px; color: #5a6577; }
</style>
