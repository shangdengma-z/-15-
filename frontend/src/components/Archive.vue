<template>
  <div>
    <div class="tiles">
      <div class="tile"><b class="num">{{ stats.communities || 0 }}</b><span>已建档社区</span></div>
      <div class="tile"><b class="num">{{ stats.avg_score ?? '—' }}</b><span>平均分</span></div>
      <div class="tile"><b class="num">{{ stats.min_score ?? '—' }}</b><span>最低分</span></div>
      <div class="tile"><b class="num">{{ stats.blindspots_verified || 0 }}</b><span>已核验盲区</span></div>
    </div>

    <h3 class="sec">品类缺口分布 <em>出现在几处社区的盲区里</em></h3>
    <div class="gapbar">
      <div v-for="g in stats.category_gaps" :key="g.name" class="gapitem">
        <span>{{ g.name }}</span>
        <i><em :style="{ width: pct(g) }"></em></i>
        <b class="num">{{ g.missing_runs }}/{{ g.runs }}</b>
      </div>
    </div>

    <h3 class="sec">历史档案 <em v-if="compare.length">已选 {{ compare.length }}/2</em></h3>
    <ul class="list">
      <li v-for="h in history" :key="h.id" :class="{ sel: compare.includes(h.id) }">
        <button class="row" @click="$emit('load', h)">
          <span class="sc num" :class="cls(h.score)">{{ h.score }}</span>
          <span class="who">
            <b>{{ h.label }}</b>
            <span>{{ h.archetype || h.mode }} · 盲区 {{ h.verified_bs }} · 第 {{ h.run_count }} 次</span>
          </span>
          <span class="mini">回放</span>
        </button>
        <span class="acts">
          <button class="k" @click.stop="$emit('toggle', h.id)" title="选作对比">A/B</button>
          <button class="k del" @click.stop="$emit('remove', h.id)" title="删除">×</button>
        </span>
      </li>
      <li v-if="!history.length" class="none">还没有记录：体检一次即自动写入本机 SQLite（同址重跑就地更新）</li>
    </ul>

    <div v-if="comparison" class="cmp">
      <h3 class="sec">A / B 对比</h3>
      <div class="head2">
        <div><b>{{ comparison.runs[0].label }}</b><span>{{ comparison.runs[0].archetype || '—' }}</span></div>
        <div><b>{{ comparison.runs[1].label }}</b><span>{{ comparison.runs[1].archetype || '—' }}</span></div>
      </div>
      <div class="row2"><span>健康分</span><b class="num">{{ comparison.runs[0].score }}</b>
        <b class="num">{{ comparison.runs[1].score }}</b></div>
      <div class="row2"><span>已核验盲区</span><b class="num">{{ comparison.runs[0].blindspots_verified }}</b>
        <b class="num">{{ comparison.runs[1].blindspots_verified }}</b></div>
      <div v-for="d in comparison.diff" :key="d.cat" class="row2">
        <span>{{ d.name }}圈内数</span>
        <b class="num">{{ d.a }}</b>
        <b class="num">{{ d.b }}<em :class="d.delta > 0 ? 'up' : d.delta < 0 ? 'down' : ''">
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
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 7px; animation: fade .2s ease both; }
.tile { background: var(--surface-2); border: 1px solid var(--line); border-radius: 9px;
  padding: 10px 4px; text-align: center; }
.tile b { display: block; font-size: 19px; color: var(--ink); line-height: 1.2; }
.tile span { font-size: 9.5px; color: var(--dim); }
.sec { font-family: var(--font-num); font-size: 10.5px; color: var(--muted); letter-spacing: 1.5px;
  text-transform: uppercase; margin: 17px 0 8px; font-weight: 600; }
.sec em { font-family: var(--font-body); font-style: normal; text-transform: none; letter-spacing: 0;
  color: var(--accent); font-size: 10.5px; }
.gapbar { display: flex; flex-direction: column; gap: 7px; }
.gapitem { display: grid; grid-template-columns: 52px 1fr 36px; align-items: center; gap: 8px; font-size: 11px; }
.gapitem span { color: var(--muted); }
.gapitem i { height: 5px; background: var(--surface-3); border-radius: 3px; overflow: hidden; }
.gapitem em { display: block; height: 100%; background: var(--accent-2); }
.gapitem b { text-align: right; color: var(--text); }
.list { list-style: none; }
.list li { display: flex; align-items: center; gap: 6px; border-bottom: 1px solid var(--line); }
.list li.sel { background: var(--accent-soft); border-radius: 8px; }
.row { flex: 1; display: flex; align-items: center; gap: 10px; padding: 9px 6px; background: none;
  border: none; box-shadow: none; text-align: left; cursor: pointer; }
.row:hover .who b { color: var(--accent); }
.sc { width: 32px; height: 32px; flex: none; border-radius: 8px; display: grid; place-items: center;
  font-size: 13px; font-weight: 700; border: 1px solid var(--line); background: var(--surface-2); color: var(--muted); }
.sc.good { background: #eef6f0; color: var(--good); border-color: #bcd6c6; }
.sc.mid { background: #f8f2e6; color: var(--warn); border-color: #e2d0b4; }
.sc.bad { background: #f9efef; color: var(--bad); border-color: #e0c2c4; }
.who { flex: 1; min-width: 0; }
.who b { display: block; font-size: 12px; font-weight: 600; color: var(--text); }
.who span { font-size: 10px; color: var(--dim); }
.mini { font-size: 10px; color: var(--accent); }
.acts { display: flex; gap: 3px; }
.k.del { color: var(--bad); }
.k.del:hover { border-color: var(--bad); background: #f9efef; }
.none { font-size: 11px; color: var(--dim); padding: 12px 4px; display: block; line-height: 1.7; }
.cmp { margin-top: 16px; padding: 12px; border: 1px dashed var(--line-2); border-radius: 9px;
  background: var(--surface-2); }
.cmp .sec { margin-top: 0; }
.head2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; }
.head2 b { font-size: 12px; display: block; color: var(--ink); }
.head2 span { font-size: 10px; color: var(--dim); }
.row2 { display: grid; grid-template-columns: 1fr 62px 82px; gap: 6px; font-size: 11.5px;
  padding: 6px 0; border-bottom: 1px solid var(--line); align-items: center; }
.row2 span { color: var(--muted); }
.row2 b { text-align: right; color: var(--text); }
.row2 em { font-style: normal; margin-left: 5px; font-size: 10px; }
.row2 .up { color: var(--good); } .row2 .down { color: var(--bad); }
</style>
