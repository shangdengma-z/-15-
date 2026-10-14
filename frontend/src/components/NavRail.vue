<template>
  <nav class="rail print-hide">
    <div class="mark" title="15 分钟生活圈">15′</div>
    <button v-for="it in items" :key="it.k" class="rbtn" :class="{ on: model === it.k }"
            :title="it.n" @click="$emit('pick', it.k)">
      <span class="glyph">
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path :d="it.d" fill="none" stroke="currentColor" stroke-width="1.8"
                stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <em v-if="badge(it)">{{ badge(it) }}</em>
      </span>
      <span class="lbl">{{ it.n }}</span>
    </button>
    <div class="spacer"></div>
    <div class="foot" :class="mockMode ? 'warn' : 'ok'">
      <i></i>{{ mockMode ? 'Mock' : 'Real' }}
    </div>
  </nav>
</template>

<script setup>
const props = defineProps({ items: Array, model: String, mockMode: Boolean,
                            stats: Object, hasResult: Boolean })
defineEmits(['pick'])

function badge(it) {
  if (it.k === 'history') return props.stats?.communities || ''
  if (it.k === 'agent') return props.stats?.agent_turns || ''
  if (it.k === 'proof' || it.k === 'scenario') return props.hasResult ? '●' : ''
  return ''
}
</script>

<style scoped>
.rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  width: 74px;
  padding: 12px 8px 14px;
  background: linear-gradient(180deg, #0e1a2b, #17293f 60%, #132234);
  border-radius: 18px;
  box-shadow: 0 10px 26px rgba(10, 24, 45, .28);
}
.mark {
  font-size: 15px;
  font-weight: 700;
  color: #fff;
  letter-spacing: .5px;
  padding: 8px 0 10px;
  background: linear-gradient(135deg, #2f6fed, #6d5efc);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
.rbtn {
  width: 100%;
  padding: 8px 2px 6px;
  border-radius: 12px;
  background: transparent;
  color: #93a6c1;
  font-size: 10px;
  line-height: 1.35;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  cursor: pointer;
  transition: background .18s, color .18s, transform .18s;
}
.rbtn:hover { color: #dbe7f7; background: rgba(255, 255, 255, .07); }
.rbtn.on { color: #fff; background: rgba(47, 111, 237, .22); box-shadow: inset 0 0 0 1px rgba(120, 170, 255, .35); }
.rbtn.on .glyph { color: #7fa9ff; }
.glyph { position: relative; display: block; color: inherit; }
.glyph em {
  position: absolute;
  top: -5px;
  right: -11px;
  font-style: normal;
  font-size: 9px;
  min-width: 14px;
  padding: 1px 3px;
  border-radius: 8px;
  background: #17b26a;
  color: #fff;
  line-height: 1.3;
}
.lbl { white-space: nowrap; }
.spacer { flex: 1; }
.foot {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 9px;
  padding: 4px 7px;
  border-radius: 8px;
  background: rgba(23, 178, 106, .16);
  color: #68d9a4;
}
.foot.warn { background: rgba(245, 166, 35, .18); color: #ffcf7d; }
.foot i { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
</style>
