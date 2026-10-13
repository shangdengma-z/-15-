<template>
  <div class="agent">
    <div class="head">
      <h3>AI 规划助手</h3>
      <span class="mode" :class="{ off: !llmReady }">{{ llmReady ? '智能体模式' : '规则模式' }}</span>
    </div>
    <p class="tip">
      用自然语言提问，助手会自主决定调用哪些地图能力（地名解析、等时圈体检、真实步行测距、POI 检索），
      再基于返回的真实数据给出诊断与规划建议。
    </p>

    <div class="presets">
      <button v-for="q in PRESETS" :key="q" class="chip" :disabled="busy" @click="ask(q)">{{ q }}</button>
    </div>

    <div ref="listEl" class="msgs">
      <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
        <div class="bubble">
          <div v-if="m.steps && m.steps.length" class="steps">
            <div class="steps-title">工具调用轨迹</div>
            <div v-for="(s, j) in m.steps" :key="j" class="step" :class="{ bad: !s.ok }">
              <span class="ico">{{ s.ok ? '✓' : '✗' }}</span>
              <span class="tname">{{ TOOL_LABELS[s.tool] || s.tool }}</span>
              <span class="tsum">{{ s.summary }}</span>
            </div>
          </div>
          <div class="text">{{ m.text }}</div>
          <div v-if="m.note" class="note">{{ m.note }}</div>
        </div>
      </div>

      <div v-if="busy" class="msg assistant">
        <div class="bubble">
          <div class="steps">
            <div class="step running">
              <span class="ico spin">◐</span>
              <span class="tname">{{ progressLabel }}</span>
              <span class="tsum">{{ elapsed }}s</span>
            </div>
            <div v-for="(s, j) in liveSteps" :key="j" class="step" :class="{ bad: !s.ok }">
              <span class="ico">{{ s.ok ? '✓' : '✗' }}</span>
              <span class="tname">{{ TOOL_LABELS[s.tool] || s.tool }}</span>
              <span class="tsum">{{ s.summary }}</span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="!messages.length && !busy" class="empty">在下方输入问题，或点上面的示例开始</div>
    </div>

    <div class="input">
      <textarea v-model="draft" rows="2" :disabled="busy" placeholder="例如：这个社区适合开药店吗？"
                @keydown.enter.exact.prevent="ask(draft)"></textarea>
      <button :disabled="busy || !draft.trim()" @click="ask(draft)">
        {{ busy ? `分析中 ${elapsed}s…` : '提问' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'

const props = defineProps({ center: Array })
const emit = defineEmits(['result', 'actions', 'pick'])

const API = import.meta.env.VITE_API_BASE || ''
const PRESETS = [
  '帮我体检这个社区，指出服务盲区',
  '这里适合开一家药店吗？',
  '老人步行 15 分钟内能到养老院吗？'
]
const TOOL_LABELS = {
  geocode: '地名解析',
  health_check: '15 分钟生活圈体检',
  nearest_facility: '最近设施 + 真实步行测距',
  walking_time: '两点真实步行耗时',
  search_poi: '周边 POI 检索'
}

const messages = ref([])
const draft = ref('')
const busy = ref(false)
const elapsed = ref(0)
const liveSteps = ref([])
const progress = ref(null)
const llmReady = ref(false)
const listEl = ref(null)

const progressLabel = computed(() =>
  progress.value ? `${TOOL_LABELS[progress.value.tool] || progress.value.tool}…` : '正在规划任务…'
)

onMounted(async () => {
  try {
    const h = await (await fetch(`${API}/api/health`)).json()
    llmReady.value = !!h.llm_configured
  } catch (e) { /* 后端未就绪时保持规则模式标识 */ }
})

function scrollBottom() {
  nextTick(() => { if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight })
}

async function ask(text) {
  const q = (text || '').trim()
  if (!q || busy.value) return
  messages.value.push({ role: 'user', text: q })
  draft.value = ''
  busy.value = true
  elapsed.value = 0
  liveSteps.value = []
  progress.value = null
  scrollBottom()

  const history = messages.value
    .filter(m => m.role === 'user' || m.role === 'assistant')
    .slice(-6, -1)
    .map(m => ({ role: m.role, content: m.text }))

  try {
    const body = { message: q, history }
    if (props.center) { body.lat = props.center[0]; body.lng = props.center[1] }
    const { job_id: jobId } = await (await fetch(`${API}/api/agent`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })).json()
    await poll(jobId)
  } catch (e) {
    messages.value.push({ role: 'assistant', text: '请求失败：' + e.message, note: '请确认后端已启动' })
  } finally {
    busy.value = false
    scrollBottom()
  }
}

async function poll(jobId) {
  for (let i = 0; i < 300; i++) {
    await new Promise(r => setTimeout(r, 1000))
    elapsed.value = i + 1
    const job = await (await fetch(`${API}/api/agent/${jobId}`)).json()
    if (job.status === 'running') {
      progress.value = job.progress
      continue
    }
    if (job.status === 'error') {
      messages.value.push({ role: 'assistant', text: '分析失败：' + job.error })
      return
    }
    messages.value.push({
      role: 'assistant', text: job.reply, steps: job.steps,
      note: job.mode === 'fallback' ? (job.note || '规则模式：未配置大模型 Key') : job.note
    })
    if (job.result) emit('result', job.result)
    if (job.map_actions) emit('actions', job.map_actions)
    if (job.center) emit('pick', job.center)
    return
  }
  messages.value.push({ role: 'assistant', text: '任务超时，请稍后重试或先在地图上完成一次体检以预热缓存。' })
}
</script>

<style scoped>
.agent { display: flex; flex-direction: column; height: 100%; }
.head { display: flex; align-items: center; gap: 8px; }
.head h3 { font-size: 14px; }
.mode { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #e6f7ee; color: #17b26a; }
.mode.off { background: #fff4e5; color: #f5a623; }
.tip { font-size: 12px; color: #7a8699; line-height: 1.7; margin: 6px 0 10px; }
.presets { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.chip { width: auto; padding: 5px 10px; font-size: 12px; background: #eef2fb; color: #2f6fed; border-radius: 14px; }
.chip:disabled { background: #f1f3f7; color: #b7c4d8; }
.msgs { flex: 1; overflow-y: auto; padding-right: 4px; }
.msg { margin-bottom: 10px; }
.msg.user { text-align: right; }
.bubble { display: inline-block; text-align: left; max-width: 100%; padding: 8px 10px; border-radius: 8px;
          background: #f3f5f9; font-size: 13px; line-height: 1.75; }
.msg.user .bubble { background: #2f6fed; color: #fff; }
.text { white-space: pre-wrap; }
.steps { border-left: 2px solid #d5dbe5; padding-left: 8px; margin-bottom: 8px; }
.steps-title { font-size: 11px; color: #7a8699; margin-bottom: 4px; }
.step { font-size: 11px; line-height: 1.8; color: #4a5568; }
.step .ico { color: #17b26a; margin-right: 4px; }
.step.bad .ico { color: #d43b3b; }
.step .tname { color: #1f2733; font-weight: 600; margin-right: 4px; }
.step .tsum { color: #7a8699; }
.step.running .ico { color: #2f6fed; display: inline-block; animation: spin 1.1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.note { font-size: 11px; color: #f5a623; margin-top: 6px; }
.empty { color: #aab3c2; font-size: 12px; text-align: center; padding: 30px 0; }
.input { display: flex; gap: 6px; margin-top: 10px; }
.input textarea { flex: 1; padding: 7px 8px; border: 1px solid #d5dbe5; border-radius: 6px;
                  font-size: 13px; font-family: inherit; resize: none; }
.input button { width: 84px; flex: none; font-size: 13px; padding: 0; }
</style>
