<template>
  <div>
    <div class="hero"><div><h1>设置</h1><p>拨到货日（夹具日，非墙钟强绑）；选定全局比较轴。</p></div></div>
    <div class="panel">
      <h2>到货日</h2>
      <div class="row">
        <input v-model="day" placeholder="YYYY-MM-DD" />
        <button @click="save">保存到货日</button>
      </div>
      <p class="msg">{{ err }}</p>
      <p class="okmsg">{{ ok }}</p>
    </div>
    <div class="panel">
      <h2>比较轴（全局只贯彻一种）</h2>
      <p class="hint">切换只影响之后新跑的对账批次；已落库批次的轴快照与分桶集合封存不变，已结案批次绝不被撬动。</p>
      <label class="radio" v-for="a in axes" :key="a.value">
        <input type="radio" name="axis" :value="a.value" v-model="axis" />
        <b>{{ a.label }}</b>：{{ a.main }}；{{ a.sidecar }}；{{ a.match }}
      </label>
      <div class="row"><button @click="saveAxis">保存比较轴</button></div>
      <p class="msg">{{ axisErr }}</p>
      <p class="okmsg">{{ axisOk }}</p>
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const day = ref('')
const axis = ref('')
const axes = ref([])
const err = ref('')
const ok = ref('')
const axisErr = ref('')
const axisOk = ref('')
async function load() {
  const s = await api('/settings')
  day.value = s.dock_day || ''
  axis.value = s.compare_axis || 'expect_receive'
  axes.value = await api('/settings/axes')
}
async function save() {
  err.value = ''; ok.value = ''
  try {
    await api('/settings/dock-day', { method: 'POST', body: JSON.stringify({ dock_day: day.value }) })
    ok.value = '已保存'
  } catch (e) { err.value = e.message }
}
async function saveAxis() {
  axisErr.value = ''; axisOk.value = ''
  try {
    const r = await api('/settings/axis', { method: 'POST', body: JSON.stringify({ axis: axis.value }) })
    axisOk.value = `已切换为${r.label}，对之后新跑批次生效`
  } catch (e) { axisErr.value = e.message }
}
onMounted(load)
</script>
