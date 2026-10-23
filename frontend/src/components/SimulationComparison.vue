<script setup lang="ts">
import { computed } from 'vue'
import type { Simulation } from '../api'

// What the draft did to the snapshot's findings, recomputed by the program.
const props = defineProps<{ simulation: Simulation }>()
const targetLabels: Record<string, string> = { resolved: '本案发现已消除', persisting: '本案发现仍存在', not_reproduced: '本案发现无法在快照中复现' }
const groups = computed(() => {
  const c = props.simulation.comparison
  return c ? [
    { key: 'resolved', label: '已消除', tone: 'success', items: c.resolved },
    { key: 'introduced', label: '新引入', tone: 'danger', items: c.introduced },
    { key: 'persisting', label: '仍存在', tone: 'warning', items: c.persisting },
    { key: 'normal', label: '正常观察', tone: 'neutral', items: c.normal },
  ] : []
})
const origin = computed(() => {
  const basis = props.simulation.basis
  if (!basis) return '分录来源未记录（0.2.0 之前的模拟）'
  if (basis.source === 'manual') return '人工编制的调整分录'
  const filled = basis.completed_inputs?.length || 0
  return `来自处理草稿“${basis.summary}”；${basis.edited ? '提交前有人工修改' : '未修改草稿分录'}${filled ? `，补齐 ${filled} 个必填字段` : ''}`
})
</script>

<template>
  <section class="comparison" aria-label="调整前后发现对比">
    <p class="origin">{{ origin }}</p>
    <p v-if="simulation.target" :class="['target', simulation.target.status]">{{ targetLabels[simulation.target.status] || simulation.target.status }}</p>
    <div v-if="groups.length" class="groups">
      <div v-for="group in groups" :key="group.key" class="group">
        <span :class="['badge', group.tone]">{{ group.label }} {{ group.items.length }}</span>
        <ul v-if="group.items.length"><li v-for="(finding, i) in group.items" :key="i">{{ finding.title }}<small> · {{ finding.rule_id }}</small></li></ul>
      </div>
    </div>
    <p v-else class="caption">该模拟生成于 0.2.0 之前，没有调整前后的发现对比。</p>
  </section>
</template>

<style scoped>
.comparison{margin:12px 0}.origin{font-size:12px;color:#4c5a53;margin:0 0 6px}
.target{font-size:13px;font-weight:600;margin:4px 0 10px}.target.resolved{color:#2f7a52}.target.persisting{color:#9a5b12}.target.not_reproduced{color:#8a3a2e}
.groups{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
.group{border:1px solid #e8ece6;border-radius:6px;padding:8px 10px}.group ul{margin:6px 0 0;padding-left:16px;font-size:12px}.group small{color:#6b756f}
</style>
