<script setup lang="ts">
import type { OpenItem } from '../api'

// Open items are informational: they are frozen into each review, never auto-closed.
defineProps<{ items: OpenItem[]; title?: string; emptyText?: string }>()
const kindLabels: Record<string, string> = {
  evidence_request: '补证', investigation_incomplete: '调查', introduced_finding: '新问题', target_persisting: '未消除',
  simulation_violation: '约束', treatment_pending: '处理路径', treatment_not_simulated: '未模拟',
}
</script>

<template>
  <section class="open-items" :aria-label="title || '未决事项'">
    <strong>{{ title || '未决事项' }}</strong>
    <ul v-if="items.length">
      <li v-for="(item, index) in items" :key="`${item.kind}-${item.ref}-${index}`"><span class="badge warning">{{ kindLabels[item.kind] || item.kind }}</span><span>{{ item.text }}</span></li>
    </ul>
    <p v-else class="caption">{{ emptyText || '当前没有未决事项。' }}</p>
  </section>
</template>

<style scoped>
.open-items{border:1px solid #efe3c7;background:#fffcf4;border-radius:6px;padding:10px 14px;margin:12px 0}
.open-items>strong{font-size:13px}.open-items ul{list-style:none;margin:8px 0 0;padding:0;display:grid;gap:6px}
.open-items li{display:flex;gap:8px;align-items:flex-start;font-size:12px;line-height:1.5}.open-items li .badge{flex:none}
.open-items .caption{margin:6px 0 0}
</style>
