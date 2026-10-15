<template>
  <div class="adv">
    <div class="eyebrow">业态</div>
    <div class="bizs">
      <button v-for="b in bizs" :key="b.key" class="biz" :class="{ on: biz === b.key }" :disabled="busy"
              @click="pick(b.key)">
        <b>{{ b.name }}</b><span>同类间距 ≥{{ b.spacing_m }}m</span>
      </button>
    </div>

    <div class="go">
      <div class="who">
        <b>{{ label || '未选中心点' }}</b>
        <span>{{ center ? center[0].toFixed(5) + ', ' + center[1].toFixed(5) : '在地图上点一个社区' }}</span>
      </div>
      <button class="btn" :disabled="busy || !center" @click="ask">
        {{ busy ? '复核中…' : '生成选址建议' }}
      </button>
    </div>

    <p v-if="error" class="err">{{ error }}</p>

    <div v-if="out" class="res">
      <div class="rank">
        <div v-for="(c, i) in out.candidates" :key="i" class="cand" :class="{ best: i === 0 }">
          <span class="no num">{{ i + 1 }}</span>
          <div class="body">
            <div class="top">
              <b class="num">{{ c.score }}</b><em>综合分</em>
              <span class="pill" :class="c.within_15min_walk ? 'ok' : 'warn'">
                <i></i>{{ c.within_15min_walk ? '路网 15 分钟内' : '路网超出 15 分钟' }}</span>
              <span v-if="c.fills_verified_blindspot" class="pill bad"><i></i>补缺点位</span>
            </div>
            <div class="facts">
              <span>500m 内小区 <b class="num">{{ c.housing_within_500m }}</b></span>
              <span>距同类 <b class="num">{{ c.nearest_same_type_m ?? '—' }}</b>m</span>
              <span>到中心 <b class="num">{{ Math.round(c.walk_sec_to_center / 6) / 10 }}</b>分 /
                <b class="num">{{ c.walk_m_to_center }}</b>m</span>
            </div>
            <button class="k" @click="$emit('locate', { lat: c.lat, lng: c.lng, label: `候选 ${i + 1}` })">
              在图上查看</button>
            <code class="co">{{ c.lat.toFixed(5) }}, {{ c.lng.toFixed(5) }}</code>
          </div>
        </div>
        <p v-if="!out.candidates.length" class="none">圈内没有达到人气门槛的候选点：
          这说明该社区 15 分钟范围内住宅小区分布过散，不建议新增该业态。</p>
      </div>

      <details class="basis">
        <summary>建议口径与出网成本</summary>
        <p>需求强度：{{ out.basis.demand_proxy }}</p>
        <p>可达性复核：候选点各需 1 次步行路线实测（本轮 {{ out.candidates.length }} 个候选）；
          本次实际出网 {{ out.meta.api_calls }} 次 / 折算配额 {{ out.meta.quota_units }} 单位，
          命中磁盘缓存时为 0。</p>
        <p>权重：人气 {{ out.basis.weights.demand }} · 距同类 {{ out.basis.weights.same_type_spacing }}
          · 补盲加分 {{ out.basis.weights.blindspot_bonus }}（候选彼此至少间隔 400m）</p>
        <p>建议是空间分布上的相对排序，不是营业许可意义上的选址结论。</p>
      </details>
    </div>

    <div v-else-if="!busy" class="placeholder">
      先在左栏选社区并体检，再回到这里生成建议——<br />
      建议复用已建档的等时圈与设施池，只为最终 3 个候选点各补一次真实步行实测。
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'

const props = defineProps({ center: Array, label: String })
const emit = defineEmits(['locate', 'actions'])
const API = import.meta.env.VITE_API_BASE || ''

const bizs = ref([])
const biz = ref('pharmacy')
const out = ref(null)
const busy = ref(false)
const error = ref('')

onMounted(async () => {
  try {
    bizs.value = (await (await fetch(`${API}/api/biz-types`)).json()).items
  } catch (e) { /* 接口未就绪时按钮区留空 */ }
})

function pick(k) { biz.value = k }

async function ask() {
  if (!props.center || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const res = await fetch(`${API}/api/advise`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat: props.center[0], lng: props.center[1], biz: biz.value })
    })
    const data = await res.json()
    if (!res.ok) { error.value = data.detail || `请求失败（${res.status}）`; out.value = null; return }
    out.value = data
    emit('actions', data.candidates.map(c => ({
      kind: 'site', lat: c.lat, lng: c.lng, label: `候选 ${c.score} 分`
    })))
  } catch (e) {
    error.value = '请求失败：' + e.message
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.adv { display: flex; flex-direction: column; gap: 15px; }
.eyebrow { margin-bottom: -6px; }
.bizs { display: flex; flex-wrap: wrap; gap: 6px; }
.biz { width: auto; padding: 7px 10px; border-radius: 9px; text-align: left; cursor: pointer;
  background: var(--surface-2); border: 1px solid var(--line); box-shadow: none;
  display: flex; flex-direction: column; gap: 1px; transition: border-color .15s, background .15s; }
.biz b { font-size: 11.5px; color: var(--text); font-weight: 600; }
.biz span { font-size: 9.5px; color: var(--dim); }
.biz:hover:not(:disabled) { border-color: var(--line-2); }
.biz.on { border-color: var(--accent); background: var(--accent-soft); }
.biz.on b { color: var(--accent); }
.biz:disabled { opacity: .5; }
.go { display: flex; align-items: center; gap: 10px; }
.who { flex: 1; min-width: 0; }
.who b { display: block; font-size: 12.5px; color: var(--ink); }
.who span { font-size: 10px; color: var(--dim); font-family: var(--font-num); }
.go .btn { width: auto; padding: 10px 16px; flex: none; }
.err { color: var(--bad); font-size: 11.5px; line-height: 1.7; }
.rank { display: flex; flex-direction: column; gap: 8px; animation: fade .22s ease both; }
.cand { display: flex; gap: 10px; padding: 10px 11px; border-radius: 9px;
  background: var(--surface-2); border: 1px solid var(--line); }
.cand.best { border-color: var(--accent); background: var(--accent-soft); }
.no { width: 22px; height: 22px; flex: none; border-radius: 6px; display: grid; place-items: center;
  font-size: 12px; font-weight: 700; background: var(--surface-3); color: var(--muted); }
.cand.best .no { background: var(--accent); color: #fff; }
.body { flex: 1; min-width: 0; }
.top { display: flex; align-items: baseline; gap: 7px; flex-wrap: wrap; }
.top b { font-size: 20px; font-weight: 700; color: var(--accent); line-height: 1; }
.top em { font-style: normal; font-size: 10px; color: var(--dim); }
.facts { display: flex; flex-wrap: wrap; gap: 10px; font-size: 10.5px; color: var(--muted); margin: 6px 0 8px; }
.facts b { font-size: 12.5px; color: var(--ink); }
.co { display: block; font-size: 10px; color: var(--dim); font-family: ui-monospace, Consolas, monospace;
  margin-top: 6px; }
.none { font-size: 11.5px; color: var(--muted); line-height: 1.8; }
.basis { border: 1px dashed var(--line-2); border-radius: 9px; padding: 0 11px; }
.basis summary { cursor: pointer; font-size: 11px; color: var(--muted); padding: 9px 0; list-style: none; }
.basis summary::-webkit-details-marker { display: none; }
.basis summary::before { content: '＋ '; color: var(--accent); }
.basis[open] summary::before { content: '－ '; }
.basis p { font-size: 10.5px; color: var(--dim); line-height: 1.75; margin-bottom: 7px; }
.placeholder { font-size: 11.5px; color: var(--dim); line-height: 1.9; text-align: center; padding: 26px 10px;
  border: 1px dashed var(--line-2); border-radius: 9px; }
</style>
