<template>
  <div>
    <div class="hero">
      <div>
        <h1>总览</h1>
        <p>当前到货日三腿行数、对账批次与结案状态。差额计数与批次比较轴口径联动：只数主差额，旁证桶单列。结案后当日预报/实收/回执不可再改。</p>
      </div>
      <button @click="load">刷新</button>
    </div>
    <div class="grid" v-if="d">
      <div class="stat"><div class="k">到货日</div><div class="v">{{ d.dock_day }}</div></div>
      <div class="stat"><div class="k">预报 / 实收 / 回执</div><div class="v">{{ d.expect_count }} / {{ d.receive_count }} / {{ d.confirm_count }}</div></div>
      <div class="stat"><div class="k">未决主差额</div><div class="v">{{ d.pending_variance }}</div></div>
      <div class="stat"><div class="k">旁证漂移</div><div class="v">{{ d.drift_count }}</div></div>
      <div class="stat">
        <div class="k">比较轴</div>
        <div class="v axis">{{ batchAxisLabel }}</div>
      </div>
      <div class="stat">
        <div class="k">结案</div>
        <div class="v"><span class="tag" :class="d.sealed ? 'seal' : 'bad'">{{ d.sealed ? '已结案' : '未结案' }}</span></div>
      </div>
    </div>
    <div class="panel" v-if="d && d.latest_batch">
      <h2>最近对账批次</h2>
      <p>
        {{ d.latest_batch.batch_no }} · 轴 <span class="tag seal">{{ axisLabel(d.latest_batch.axis) }}</span>
        · 匹配 {{ d.latest_batch.matched_count }} · 主差额 {{ d.latest_batch.variance_count }}
        · 旁证 {{ d.latest_batch.sidecar_count }} · {{ d.latest_batch.status }}
      </p>
      <p class="hint" v-if="d.latest_batch.axis !== d.compare_axis">注意：全局轴已切换为{{ axisLabel(d.compare_axis) }}，该批仍按落库时轴封存；重跑开放日才用新轴。</p>
    </div>
    <p class="msg">{{ err }}</p>
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
const AXIS_LABELS = { expect_receive: '预报-实收轴', receive_confirm: '实收-回执轴' }
const axisLabel = (a) => AXIS_LABELS[a] || a || '—'
const d = ref(null)
const err = ref('')
const batchAxisLabel = computed(() => {
  if (!d.value) return '—'
  if (d.value.latest_batch) return axisLabel(d.value.latest_batch.axis)
  return `${axisLabel(d.value.compare_axis)}（未跑批）`
})
async function load() {
  err.value = ''
  try { d.value = await api('/dashboard') } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
