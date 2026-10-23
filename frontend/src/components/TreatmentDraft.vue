<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { api, type TreatmentDraft } from '../api'

// The draft is computed by the server from the case's own finding and frozen
// snapshot; the investigation is shown as context and never changes the lines.
const props = defineProps<{ caseId: string; refreshKey?: string; applied?: string | null }>()
const emit = defineEmits<{ apply: [draft: TreatmentDraft] }>()
const draft = ref<TreatmentDraft>()
const error = ref('')
let generation = 0
const statusLabels: Record<string, string> = { proposed: '可测算草稿', needs_input: '需补充字段', conditional: '有前提条件',
  needs_evidence: '待补证，不编分录', not_applicable: '无需调整分录' }
const tones: Record<string, string> = { proposed: 'success', needs_input: 'warning', conditional: 'warning', needs_evidence: 'warning', not_applicable: 'neutral' }
const kindLabels: Record<string, string> = { order: '订单', receipt: '收货单', invoice: '发票', bank: '银行流水', journal: '记账凭证', opening: '期初余额', trial_balance: '试算表' }
const modeLabels: Record<string, string> = { rules: '规则', single: '单 Agent', multi: 'Multi-Agent', active: '主动调查' }
const outcomeLabels: Record<string, string> = { supported: '证据支持', normal: '正常差异', insufficient: '证据不足', unsupported: '超出支持范围',
  failed: '运行失败', budget_exhausted: '预算耗尽', complete: '已完成' }

async function load() {
  const current = ++generation
  error.value = ''
  try {
    const value = await api<TreatmentDraft>(`/cases/${encodeURIComponent(props.caseId)}/treatment-draft`)
    if (current === generation) draft.value = value
  } catch (cause) {
    if (current === generation) { draft.value = undefined; error.value = cause instanceof Error ? cause.message : '处理草稿读取失败' }
  }
}
watch(() => [props.caseId, props.refreshKey], () => { void load() }, { immediate: true })
onBeforeUnmount(() => { generation++ })
const required = (index: number, field: string) => draft.value?.line_basis[index]?.input_required.includes(field)
</script>

<template>
  <section class="treatment" aria-label="处理草稿">
    <div v-if="error" class="notice error" role="alert">{{ error }}</div>
    <template v-else-if="draft">
      <div class="treatment-head">
        <div><strong>处理草稿</strong><small>{{ draft.version }} · 依据规则 {{ draft.rule_id }}</small></div>
        <span :class="['badge', tones[draft.status]]">{{ statusLabels[draft.status] || draft.status }}</span>
      </div>
      <h3>{{ draft.summary }}</h3>
      <p>{{ draft.reason }}</p>
      <p class="framework-note"><b>制度路径</b>{{ draft.framework_note }}</p>
      <ul v-if="draft.warnings.length" class="warnings"><li v-for="(w, i) in draft.warnings" :key="i">{{ w }}</li></ul>
      <p v-if="draft.investigation" class="investigation">
        参考调查：{{ modeLabels[draft.investigation.mode] || draft.investigation.mode }} · {{ outcomeLabels[draft.investigation.status] || draft.investigation.status }}
        <span v-if="draft.investigation.warning" class="warn"> · {{ draft.investigation.warning }}</span>
      </p>
      <p v-else class="investigation">尚未调查；草稿仅依据规则发现。</p>
      <div v-if="draft.needs.length || draft.evidence_kind" class="needs">
        <strong>需要的资料</strong>
        <ul><li v-for="(need, i) in draft.needs" :key="i">{{ need }}</li></ul>
        <small v-if="draft.evidence_kind">可在“异常调查”登记补证事项（{{ kindLabels[draft.evidence_kind] || draft.evidence_kind }}）。</small>
      </div>
      <div v-if="draft.lines.length" class="table-wrap">
        <table>
          <thead><tr><th>凭证号</th><th>科目</th><th class="number">借方</th><th class="number">贷方</th><th>业务日期</th><th>摘要</th></tr></thead>
          <tbody>
            <tr v-for="(line, i) in draft.lines" :key="i">
              <td class="mono">{{ line.voucher_id }}</td>
              <td :class="{ required: required(i, 'account') && !line.account }">{{ line.account || '待填写' }}</td>
              <td class="number">{{ line.debit }}</td><td class="number">{{ line.credit }}</td>
              <td>{{ line.business_date }}</td><td class="wrap-text">{{ line.description }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <ul v-if="draft.alternatives.length" class="alternatives"><li v-for="(alt, i) in draft.alternatives" :key="i">备选：{{ alt.summary }}</li></ul>
      <div class="treatment-actions">
        <button v-if="draft.lines.length" class="button primary" @click="emit('apply', draft)">{{ applied === draft.digest ? '重新填入草稿' : '使用此草稿填入调整分录' }}</button>
        <small>{{ draft.notice }}</small>
      </div>
    </template>
    <p v-else class="caption">正在计算处理草稿…</p>
  </section>
</template>

<style scoped>
.treatment{border:1px solid #dfe8e2;border-radius:6px;padding:14px 16px;margin:0 22px 16px;background:#fbfdfb}
.treatment-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.treatment-head small{display:block;font-size:11px;color:#6b756f;margin-top:2px}
.treatment h3{font-size:15px;margin:10px 0 4px}.treatment p{font-size:12px;line-height:1.6;margin:4px 0}
.framework-note b{margin-right:8px}.warnings,.alternatives{font-size:12px;margin:6px 0;padding-left:18px;color:#7a5a16}
.investigation{color:#4c5a53}.investigation .warn{color:#9a5b12}
.needs{font-size:12px;background:#fffaf0;border-radius:4px;padding:8px 10px;margin:8px 0}.needs ul{margin:4px 0;padding-left:18px}
td.required{color:#9a5b12;font-weight:600}
.treatment-actions{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-top:10px}.treatment-actions small{font-size:11px;color:#6b756f}
@media(max-width:760px){.treatment{margin:0 12px 16px}}
</style>
