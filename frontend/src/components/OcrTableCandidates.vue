<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import type { LedgerInput } from '../generated/contracts'
import type { OcrTables, OcrTableCandidate } from '../ocr'

const props = defineProps<{ runId: string; resultDigest: string; bookId: string; page: number; disabled: boolean }>()
const emit = defineEmits<{ append: [rows: OcrTableCandidate[], kind: LedgerInput['kind']]; locate: [id: string] }>()
const result = ref<OcrTables>(), selected = ref<string[]>([]), recordKind = ref<LedgerInput['kind'] | ''>('')
const busy = ref(false), error = ref(''), note = ref('')
let generation = 0, controller: AbortController | undefined
const rows = computed(() => result.value?.tables.flatMap(t => t.rows) || [])
const selectedRows = computed(() => rows.value.filter(r => selected.value.includes(r.candidate_id) && r.copyable))
const kinds: Record<LedgerInput['kind'], string> = { invoice: '发票', bank: '银行流水', order: '采购订单', receipt: '收货记录', journal: '会计分录', opening: '期初余额', trial_balance: '试算平衡' }
const reasons: Record<string, string> = {
  outside_recognized_table: '未归入可识别表格', ambiguous_header: '表头重复或存在未知列', overlapping_header: '表头位置重叠',
  footer_requires_manual_review: '合计或备注需单独核实', row_budget_exhausted: '超过每页100行候选上限',
  no_text: '本页未检出文字', failed_page_has_no_candidates: '识别失败页不能生成候选',
  crossing_or_outside_column: '文字跨列或位于列外', multiple_observations_in_cell: '一个单元格含多段文字，归属不明确',
  invalid_date: '日期格式或值无法确认', invalid_amount: '金额含歧义字符或格式不明确',
  missing_valid_field: '缺少可解析的必填字段', invalid_text_length: '字段文字为空或长度超限', too_many_observations: '本页文字数量超限',
}
function reason(code: string) { const [name, field] = code.split(':'); return (reasons[name!] || '未支持的结构') + (field ? `（${({ amount: '金额', business_date: '日期', description: '摘要' } as Record<string, string>)[field] || field}）` : '') }
function clear() { generation++; controller?.abort(); result.value = undefined; selected.value = []; recordKind.value = ''; busy.value = false; error.value = ''; note.value = '' }
async function extract() {
  clear(); controller = new AbortController(); const current = generation; busy.value = true
  try {
    const response = await api<OcrTables>(`/ocr/runs/${props.runId}/table-candidates?book_id=${props.bookId}&page=${props.page}`, { signal: controller.signal })
    if (current !== generation) return
    if (response.schema_version !== 'ocr-table-candidates-v1' || response.run_id !== props.runId || response.result_digest !== props.resultDigest || response.page !== props.page) throw new Error('表格候选与当前原文不一致，请刷新识别任务。')
    result.value = response
  } catch (e) { if (current === generation) error.value = e instanceof Error ? e.message : String(e) }
  finally { if (current === generation) busy.value = false }
}
function append() {
  if (!recordKind.value || !selectedRows.value.length || props.disabled) return
  emit('append', selectedRows.value, recordKind.value)
  selected.value = []; note.value = '已发送所选候选，请在下方逐条核对草稿；全部字段核实前不能导入。'
}
watch(() => [props.runId, props.resultDigest, props.page], clear)
onBeforeUnmount(clear)
</script>

<template>
  <section class="table-candidates" aria-label="表格候选提取">
    <div class="candidate-heading"><strong>表格候选 · 第 {{ page }} 页</strong><button class="button" type="button" :disabled="disabled || busy" @click="extract">{{ busy ? '正在分析文字坐标…' : '识别本页表格候选' }}</button></div>
    <p>仅支持明确的日期、金额等表头和单行单元格。候选仍需对照原件核实，不自动判断业务类型或生成记录。</p>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p><p v-if="note" role="status">{{ note }}</p>
    <template v-if="result">
      <p v-if="result.status==='no_table'">本页未找到支持的表格结构，请继续手工逐条核实。</p>
      <p v-if="result.status==='limit_exceeded' || result.status==='page_failed'" class="notice warning">本页提取未完整完成，请核对所有未归类文字。</p>
      <p v-for="issue in result.issues" :key="issue" class="issue">{{ reason(issue) }}</p>
      <div v-for="table in result.tables" :key="table.table_id" class="table-scroll">
        <table><thead><tr><th>选择</th><th v-for="header in table.headers" :key="header.field"><button type="button" @click="emit('locate',header.observation_id)">{{ header.text }}</button></th></tr></thead><tbody>
          <template v-for="(row,index) in table.rows" :key="row.candidate_id"><tr><td><input v-model="selected" :aria-label="`选择候选记录 ${index+1}`" type="checkbox" :value="row.candidate_id" :disabled="disabled || !row.copyable"/></td><td v-for="header in table.headers" :key="header.field"><button v-if="row.cells[header.field]?.observation_ids.length" type="button" @click="emit('locate',row.cells[header.field]!.observation_ids[0]!)">{{ row.cells[header.field]?.text }}</button><span v-else>缺失</span></td></tr><tr v-if="row.issues.length"><td :colspan="table.headers.length+1" class="issue">{{ row.issues.map(reason).join('；') }}{{ row.copyable ? '。可加入草稿后人工补齐。' : '。不自动划分为记录，请手工核实。' }}</td></tr></template>
        </tbody></table>
      </div>
      <div v-if="rows.length" class="candidate-actions"><label>候选记录类型<select v-model="recordKind" :disabled="disabled"><option value="">请人工选择业务类型</option><option v-for="(label,key) in kinds" :key="key" :value="key">{{ label }}</option></select></label><button class="button" type="button" :disabled="disabled || !recordKind || !selectedRows.length" @click="append">将所选 {{ selectedRows.length }} 条加入人工草稿</button></div>
      <details v-if="result.unassigned.length"><summary>未归类文字（{{ result.unassigned.reduce((n,g)=>n+g.observation_ids.length,0) }} 段）</summary><ul><li v-for="(group,index) in result.unassigned" :key="index">{{ reason(group.reason) }} <button v-for="(id,position) in group.observation_ids" :key="id" type="button" @click="emit('locate',id)">查看原文 {{ position+1 }}</button></li></ul></details>
    </template>
  </section>
</template>

<style scoped>
.table-candidates{padding:16px;border:1px solid #dce4d3;border-radius:5px;background:#f6f8f1;margin:18px 0;font-size:12px;min-width:0}.candidate-heading{display:flex;align-items:center;gap:12px;flex-wrap:wrap;justify-content:space-between}.candidate-heading .button{font-size:11px}.table-candidates p{font-size:11px;line-height:1.85;color:#687b60}.table-scroll{overflow:auto;margin:14px 0;border:1px solid #e1e6d9}.table-scroll table{border-collapse:collapse;width:100%;min-width:440px;background:white}.table-scroll th,.table-scroll td{padding:9px 10px;border-bottom:1px solid #e1e6d9;text-align:left;white-space:pre-wrap}.table-scroll th{background:#edf2e6}.table-candidates button:not(.button){border:0;background:none;color:#315b45;cursor:pointer;text-align:left;font:inherit;text-decoration:underline;text-decoration-color:#b1c2a9;text-underline-offset:3px;overflow-wrap:anywhere}.table-scroll input{accent-color:#246454}.candidate-actions{display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin:16px 0}.candidate-actions label{display:grid;gap:7px;min-width:0;font-size:11px}.candidate-actions select{padding:8px;border:1px solid #d7dfce;border-radius:4px;background:white;color:#354d3d;max-width:100%;font:inherit}.candidate-actions .button{font-size:11px}.issue{font-size:11px!important;color:#926128!important;line-height:1.8}.table-candidates details{margin-top:14px;font-size:11px;line-height:1.9}.table-candidates summary{cursor:pointer}.table-candidates ul{padding-left:16px}.table-candidates li button{margin:0 5px}.table-candidates button:focus-visible,.table-candidates select:focus-visible,.table-candidates input:focus-visible{outline:2px solid #246454;outline-offset:2px}@media(max-width:560px){.table-candidates{padding:12px}.candidate-actions{align-items:start;flex-direction:column}}
</style>
