<script setup lang="ts">
import { computed } from 'vue'
import type { Lineage } from '../api'
import Icon from './Icon.vue'

// A case is one finding on one frozen snapshot. Lineage connects it to the
// same finding on earlier or later snapshots; nothing here closes a case.
const props = defineProps<{ lineage: Lineage; busy?: boolean }>()
defineEmits<{ open: [caseId: string]; recheck: [] }>()
const short = (value: string) => value.slice(0, 8)
const matchLabels: Record<string, string> = { same_records: '同一批来源记录', shared_records: '部分来源记录相同', not_reproduced: '后续检查未再出现' }
const followups = computed(() => props.lineage.successors.filter(s => s.match === 'not_reproduced'))
const continued = computed(() => props.lineage.successors.filter(s => s.match !== 'not_reproduced'))
const view = computed(() => {
  switch (props.lineage.state) {
    case 'continued': return { tone: 'neutral', title: '已在新快照中接续', text: '资料或政策更新后重新检查，同一发现已生成接续案件；请在接续案件中继续调查和复核，本案记录保留为历史。' }
    case 'not_reproduced': return { tone: 'success', title: '后续检查未再出现', text: followups.value.length
      ? '最新检查未再触发该发现，已生成接续核对案件。系统不自动认定问题已解决：请核对补证记录后在接续核对案件中复核。'
      : '后续检查未再触发该发现。系统不自动认定问题已解决，请核对资料变化。' }
    case 'not_rechecked': return { tone: 'warning', title: '资料已更新，尚未重新检查', text: '请在最新快照重新检查：同一发现会生成接续案件，不再出现的发现会生成接续核对案件。' }
    default:
      if (!props.lineage.prior.length) return null
      return props.lineage.prior[0].match === 'not_reproduced'
        ? { tone: 'neutral', title: '核对前一案件未再出现的发现', text: '前一快照中的发现在本快照中未再触发。请核对前一案件的补证记录与资料变化，再在本案件中复核；前一案件的记录保持不变。' }
        : { tone: 'neutral', title: '接续自历史案件', text: '本案件延续此前快照中的同一发现；历史调查、补证、模拟和复核保留在原案件中。' }
  }
})
</script>

<template>
  <section v-if="view" :class="['lineage', view.tone]" aria-label="案件接续">
    <div class="lineage-head"><Icon name="layers" :size="16"/><strong>{{ view.title }}</strong></div>
    <p>{{ view.text }}</p>
    <div class="lineage-actions">
      <button v-if="lineage.state === 'not_rechecked'" class="button" :disabled="busy" @click="$emit('recheck')">在最新快照重新检查</button>
      <button v-for="item in continued" :key="item.case_id" class="button" @click="$emit('open', item.case_id)">打开接续案件 {{ short(item.case_id) }}</button>
      <button v-for="item in followups" :key="item.case_id" class="button" @click="$emit('open', item.case_id)">打开接续核对案件 {{ short(item.case_id) }}</button>
    </div>
    <ul v-if="lineage.prior.length" class="lineage-prior">
      <li v-for="item in lineage.prior" :key="item.case_id">
        <button class="text-button" @click="$emit('open', item.case_id)">前一案件 {{ short(item.case_id) }}</button>
        <span>{{ item.title }} · 快照 {{ short(item.snapshot_id) }} · {{ matchLabels[item.match] || item.match }}</span>
        <small>调查 {{ item.runs }} 次 · 补证 {{ item.evidence_requests }} 项 · 模拟 {{ item.simulations }} 个 · 复核 {{ item.reviews }} 条</small>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.lineage{border:1px solid #e3e8df;border-left-width:4px;border-radius:6px;padding:12px 14px;margin:14px 0;background:#fbfcfa}
.lineage.warning{border-left-color:#c58a1d;background:#fffaf0}.lineage.success{border-left-color:#3f8a62;background:#f5faf6}.lineage.neutral{border-left-color:#246454}
.lineage-head{display:flex;gap:8px;align-items:center;font-size:13px}.lineage p{font-size:12px;margin:6px 0 8px;color:#4c5a53}
.lineage-actions{display:flex;flex-wrap:wrap;gap:8px}.lineage-actions:empty{display:none}
.lineage-prior{list-style:none;margin:10px 0 0;padding:0;display:grid;gap:6px;font-size:12px}
.lineage-prior li{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;border-top:1px solid #edf0eb;padding-top:6px}
.lineage-prior small{color:#6b756f;flex-basis:100%}
</style>
