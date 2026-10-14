<template>
  <div>
    <div class="tiles">
      <div class="tile"><b>{{ stats.communities || 0 }}</b><span>已建档社区</span></div>
      <div class="tile"><b>{{ stats.total_runs || 0 }}</b><span>累计体检轮次</span></div>
      <div class="tile"><b>{{ stats.avg_score ?? '—' }}</b><span>平均分</span></div>
      <div class="tile"><b>{{ stats.min_score ?? '—' }}</b><span>最低分</span></div>
      <div class="tile"><b>{{ stats.api_calls || 0 }}</b><span>累计出网</span></div>
      <div class="tile"><b>{{ stats.quota_units || 0 }}</b><span>配额单位</span></div>
      <div class="tile"><b>{{ stats.blindspots_verified || 0 }}</b><span>已核验盲区</span></div>
      <div class="tile"><b>{{ stats.agent_turns || 0 }}</b><span>智能体轮次</span></div>
    </div>

    <div class="gapbar">
      <div v-for="g in stats.category_gaps" :key="g.name" class="gapitem">
        <span>{{ g.name }}</span>
        <i><em :style="{ width: pct(g) }"></em></i>
        <b>{{ g.missing_runs }}/{{ g.runs }}</b>
      </div>
    </div>

    <h3 class="sec">历史档案 <em v-if="compare.length">（对比已选 {{ compare.length }}/2）</em></h3>
    <ul class="list">
      <li v-for="h in history" :key="h.id" :class="{ sel: compare.includes(h.id) }">
        <button class="row" @click="$emit('load', h)">
          <span class="sc" :class="cls(h.score)">{{ h.score }}</span>
          <span class="who">
            <b>{{ h.label }}</b>
            <span>{{ h.archetype || h.mode }} · 盲区 {{ h.verified_bs }} ·
              {{ h.updated_at.slice(5, 16) }} · 第 {{ h.run_count }} 次</span>
          </span>
          <span class="mini">回放</span>
        </button>
        <span class="acts">
          <button class="k" @click.stop="$emit('toggle', h.id)" title="选作对比">A/B</button>
          <button class="k del" @click.stop="$emit('remove', h.id)" title="删除">×</button>
        </span>
      </li>
      <li v-if="!history.length" class="none">还没有记录：体检一次即自动写入 SQLite（同址重跑就地更新）</li>
    </ul>

    <div v-if="comparison" class="cmp card2">
      <h3 class="sec">A / B 对比</h3>
      <div class="head2">
        <div><b>{{ comparison.runs[0].label }}</b><span>{{ comparison.runs[0].archetype || '—' }}</span></div>
        <div><b>{{ comparison.runs[1].label }}</b><span>{{ comparison.runs[1].archetype || '—' }}</span></div>
      </div>
      <div class="row2">
        <span>健康分</span><b>{{ comparison.runs[0].score }}</b><b>{{ comparison.runs[1].score }}</b>
      </div>
      <div class="row2">
        <span>已核验盲区</span><b>{{ comparison.runs[0].blindspots_verified }}</b>
        <b>{{ comparison.runs[1].blindspots_verified }}</b>
      </div>
      <div v-for="d in comparison.diff" :key="d.cat" class="row2">
        <span>{{ d.name }}圈内数</span>
        <b>{{ d.a }}</b>
        <b>{{ d.b }}<em :class="d.delta > 0 ? 'up' : d.delta < 0 ? 'down' : ''">
          {{ d.delta > 0 ? '+' : '' }}{{ d.delta }}</em></b>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({ history: Array, stats: Object, compare: Array, comparison: Object })
defineEmits(['load', 'remove', 'toggle'])

const cls = (s) => (s >= 80 ? 'good' : s >= 60 ? 'mid' : 'bad')
const pct = (g) => Math.round((g.missing_runs / Math.max(g.runs, 1)) * 100) + '%'
</script>

<style scoped>
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 7px; }
.tile { background: linear-gradient(160deg, #f8fafd, #eef3fb); border: 1px solid var(--line);
  border-radius: 12px; padding: 9px 4px; text-align: center; }
.tile b { display: block; font-size: 16px; color: var(--ink); font-variant-numeric: tabular-nums; }
.tile span { font-size: 9.5px; color: var(--muted); }
.sec { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px;
  margin: 16px 0 8px; font-weight: 600; }
.sec em { font-style: normal; color: var(--brand); text-transform: none; letter-spacing: 0; }
.gapbar { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
.gapitem { display: grid; grid-template-columns: 52px 1fr 40px; align-items: center; gap: 8px; font-size: 11px; }
.gapitem span { color: var(--muted); }
.gapitem i { height: 6px; background: #eef1f7; border-radius: 4px; overflow: hidden; display: block; }
.gapitem em { display: block; height: 100%; background: linear-gradient(90deg, #f5a623, #e5484d); }
.gapitem b { text-align: right; color: var(--text); font-variant-numeric: tabular-nums; }
.list { list-style: none; }
.list li { display: flex; align-items: center; gap: 6px; border-bottom: 1px solid #f1f4f9; }
.list li.sel { background: #f2f7ff; border-radius: 10px; }
.row { flex: 1; display: flex; align-items: center; gap: 9px; padding: 8px 6px; background: none;
  border: none; box-shadow: none; text-align: left; cursor: pointer; }
.row:hover .who b { color: var(--brand); }
.sc { width: 30px; height: 30px; flex: none; border-radius: 10px; display: grid; place-items: center;
  font-size: 12.5px; font-weight: 700; }
.sc.good { background: #e4f7ee; color: #0d7a49; }
.sc.mid { background: #fff3e2; color: #9a5b00; }
.sc.bad { background: #fdeaea; color: #b62c31; }
.who { flex: 1; min-width: 0; }
.who b { display: block; font-size: 12px; color: var(--text); font-weight: 600; }
.who span { font-size: 10px; color: var(--muted); }
.mini { font-size: 10px; color: var(--brand); }
.acts { display: flex; gap: 3px; }
.k { width: auto; padding: 3px 7px; font-size: 10px; border-radius: 7px; background: #f1f4fa; color: #56657f;
  box-shadow: none; }
.k.del { color: var(--bad); }
.none { font-size: 11px; color: var(--muted); padding: 10px 4px; display: block; }
.card2 { margin-top: 14px; padding: 12px; background: #fafbfe; }
.head2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; }
.head2 b { font-size: 12px; display: block; }
.head2 span { font-size: 10px; color: var(--muted); }
.row2 { display: grid; grid-template-columns: 1fr 60px 78px; gap: 6px; font-size: 11.5px;
  padding: 5px 0; border-bottom: 1px dashed #eef1f7; align-items: center; }
.row2 span { color: var(--muted); }
.row2 b { text-align: right; font-variant-numeric: tabular-nums; }
.row2 em { font-style: normal; margin-left: 5px; font-size: 10px; }
.row2 .up { color: var(--good); } .row2 .down { color: var(--bad); }
</style>
