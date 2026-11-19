<template>
  <div>
    <div class="hero">
      <div>
        <h1>三联对账</h1>
        <p>
          比较轴决定 ⇒ 主差额集合与匹配判定。当前全局轴：<b>{{ axisLabel(currentAxis) }}</b>；
          批次落库即快照当时轴，结案后封存不可撬动。
        </p>
      </div>
    </div>
    <div class="panel">
      <div class="row">
        <input v-model="day" placeholder="到货日" />
        <button @click="run">跑三联对账</button>
        <button class="warn" @click="seal">结案封印</button>
        <button class="secondary" @click="load">刷新</button>
      </div>
      <p class="msg">{{ err }}</p>
      <p class="okmsg">{{ ok }}</p>
      <p v-if="batch">
        批次 {{ batch.batch_no }} · 轴 <span class="tag seal">{{ axisLabel(batch.axis) }}</span>
        · 匹配 {{ batch.matched_count }} · 主差额 {{ batch.variance_count }} · 旁证 {{ batch.sidecar_count }}
        · {{ batch.status }}
        <span v-if="batch.axis !== currentAxis" class="tag bad">全局轴已切换，重跑本日后生效</span>
      </p>
    </div>
    <div class="panel" v-if="batch">
      <h2>匹配表（{{ axisLabel(batch.axis) }}：{{ matchRule(batch.axis) }}）</h2>
      <table>
        <thead><tr><th>供应商</th><th>货号</th><th>预报</th><th>实收</th><th>回执</th><th>旁证偏差</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="r in matches" :key="r.id">
            <td>{{ r.supplier_code }}</td><td>{{ r.sku }}</td>
            <td><Qty :v="r.expect_qty" :has="r.has_expect" /></td>
            <td><Qty :v="r.receive_qty" :has="r.has_receive" /></td>
            <td><Qty :v="r.confirm_qty" :has="r.has_confirm" /></td>
            <td><Dev :v="r.side_dev" /></td>
            <td><span class="tag" :class="r.aligned ? 'ok' : 'bad'">{{ r.aligned ? '匹配' : '主差额' }}</span></td>
          </tr>
        </tbody>
      </table>
      <p class="hint">旁证偏差 = {{ batch.axis === 'receive_confirm' ? '预报 − 实收' : '回执 − 实收' }}，仅证据留存，不进主差额。</p>
    </div>
    <div class="panel" v-if="batch">
      <h2>主差额处置（结案写闸只数本桶未决）</h2>
      <table>
        <thead><tr><th>供应商</th><th>货号</th><th>三数量（预/实/回）</th><th>缺腿</th><th>命运</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="r in mainRows" :key="r.id">
            <td>{{ r.supplier_code }}</td><td>{{ r.sku }}</td>
            <td><Qty :v="r.expect_qty" :has="r.has_expect" /> / <Qty :v="r.receive_qty" :has="r.has_receive" /> / <Qty :v="r.confirm_qty" :has="r.has_confirm" /></td>
            <td><span v-for="m in missingLegs(r)" :key="m" class="tag miss">缺{{ m }}</span><span v-if="!missingLegs(r).length">—</span></td>
            <td>{{ r.fate }}</td>
            <td>
              <button class="secondary" @click="fate(r.id, 'accept_short')">认短</button>
              <button class="secondary" @click="fate(r.id, 'accept_over')">认溢</button>
              <button class="secondary" @click="fate(r.id, 'quarantine')">隔离</button>
            </td>
          </tr>
          <tr v-if="!mainRows.length"><td colspan="6">无主差额</td></tr>
        </tbody>
      </table>
    </div>
    <div class="panel" v-if="batch && driftRows.length">
      <h2>旁证桶（expect_drift · 预报对实收漂移）</h2>
      <table>
        <thead><tr><th>供应商</th><th>货号</th><th>三数量（预/实/回）</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="r in driftRows" :key="r.id">
            <td>{{ r.supplier_code }}</td><td>{{ r.sku }}</td>
            <td><Qty :v="r.expect_qty" :has="r.has_expect" /> / <Qty :v="r.receive_qty" :has="r.has_receive" /> / <Qty :v="r.confirm_qty" :has="r.has_confirm" /></td>
            <td><span class="tag side">旁证</span> 仅证据留存，不需处置、不挡结案</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
<script setup>
import { ref, h, onMounted } from 'vue'
import { api } from '../api'

const AXIS_LABELS = { expect_receive: '预报-实收轴', receive_confirm: '实收-回执轴' }
const MATCH_RULES = {
  expect_receive: '匹配 = 预报 = 实收；回执仅旁证',
  receive_confirm: '匹配 = 实收 = 回执；预报漂移进旁证桶',
}
const LEG_LABELS = { expect: '预报', receive: '实收', confirm: '回执' }
const axisLabel = (a) => AXIS_LABELS[a] || a || '—'
const matchRule = (a) => MATCH_RULES[a] || ''
const missingLegs = (r) => ['expect', 'receive', 'confirm'].filter((l) => !r[`has_${l}`]).map((l) => LEG_LABELS[l])

const Qty = (props) => (props.has ? String(props.v) : h('span', { class: 'tag miss' }, '缺'))
Qty.props = ['v', 'has']
const Dev = (props) => {
  if (props.v === null || props.v === undefined) return '—'
  const cls = props.v > 0 ? 'dev-pos' : props.v < 0 ? 'dev-neg' : ''
  return h('span', { class: cls }, `${props.v > 0 ? '+' : ''}${props.v}`)
}
Dev.props = ['v']

const day = ref('2026-11-18')
const batch = ref(null)
const matches = ref([])
const mainRows = ref([])
const driftRows = ref([])
const currentAxis = ref('')
const err = ref('')
const ok = ref('')

async function load() {
  err.value = ''
  const s = await api('/settings')
  currentAxis.value = s.compare_axis || 'expect_receive'
  const batches = await api(`/reconcile/batches?dock_day=${encodeURIComponent(day.value)}`)
  batch.value = batches[0] || null
  if (!batch.value) { matches.value = []; mainRows.value = []; driftRows.value = []; return }
  matches.value = await api(`/reconcile/matches?batch_id=${batch.value.id}`)
  const variances = await api(`/reconcile/variances?batch_id=${batch.value.id}`)
  mainRows.value = variances.filter((r) => r.bucket === 'main')
  driftRows.value = variances.filter((r) => r.bucket !== 'main')
}
async function run() {
  err.value = ''; ok.value = ''
  try {
    await api('/reconcile/run', { method: 'POST', body: JSON.stringify({ dock_day: day.value }) })
    ok.value = '对账完成'
    await load()
  } catch (e) { err.value = e.message }
}
async function seal() {
  err.value = ''; ok.value = ''
  try {
    await api('/reconcile/seal', { method: 'POST', body: JSON.stringify({ dock_day: day.value }) })
    ok.value = '已结案'
    await load()
  } catch (e) { err.value = e.message }
}
async function fate(id, f) {
  err.value = ''; ok.value = ''
  try {
    await api(`/reconcile/variances/${id}/fate`, { method: 'POST', body: JSON.stringify({ fate: f }) })
    ok.value = '已更新命运'
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
