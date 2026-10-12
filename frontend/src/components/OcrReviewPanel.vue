<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, type ImportPreview } from '../api'
import type { LedgerInput, OcrReviewInput } from '../generated/contracts'
import type { OcrRun, OcrLine, OcrPage } from '../ocr'
import OcrSourcePreview from './OcrSourcePreview.vue'

const props = defineProps<{ bookId: string; period: string; currency: string }>()
const emit = defineEmits<{ preview: [value: ImportPreview] }>()
const enabled = ref(false), ready = ref(false), busy = ref(false), error = ref(''), message = ref('')
const jobs = ref<{ id: string; status: string; created_at: string }[]>([]), run = ref<OcrRun>()
const chosen = ref(''), page = ref(1), selectedLine = ref<OcrLine>(), file = ref<File>()
const kind = ref<LedgerInput['kind']>('invoice'), sourceId = ref(''), values = ref<Record<string, string>>({})
const bindings = ref<Record<string, string>>({}), comment = ref(''), confirmed = ref(false)
const controller = new AbortController()
let disposed = false, selection = 0, timer: ReturnType<typeof setTimeout> | undefined
const labels: Record<string, string> = { business_date: '业务日期', amount: '业务金额', invoice_id: '发票号', order_id: '订单号', counterparty: '交易对手', reference: '参考编号', description: '摘要', voucher_id: '凭证号', account: '科目', debit: '借方金额', credit: '贷方金额', direction: '收付方向' }
const kinds: Record<LedgerInput['kind'], string> = { invoice: '发票', bank: '银行流水', order: '采购订单', receipt: '收货记录', journal: '会计分录', opening: '期初余额', trial_balance: '试算平衡' }
const statuses: Record<string, string> = { queued: '排队中', running: '本地识别中', complete: '待人工核实', partial: '部分页面失败', failed: '识别失败' }
const pages = computed<OcrPage[]>(() => run.value?.result.pages || [{ page: 1, status: run.value?.status || '', image: run.value?.result.image, observations: run.value?.result.observations || [] }])
const currentPage = computed(() => pages.value.find(p => p.page === page.value))
const lines = computed(() => pages.value.flatMap(p => p.observations || []))
const latest = computed(() => run.value?.reviews.at(-1))
const locked = computed(() => latest.value?.import_status === 'confirmed')
const canReview = computed(() => !!run.value && ['complete', 'partial'].includes(run.value.status) && !run.value.stale && !locked.value && lines.value.length > 0)
const fields = computed(() => kind.value === 'journal' ? ['business_date', 'voucher_id', 'account', 'debit', 'credit', 'counterparty', 'reference', 'description'] : ['business_date', 'amount', ...(['opening', 'trial_balance'].includes(kind.value) ? ['account'] : []), 'invoice_id', 'order_id', 'counterparty', 'reference', 'direction', 'description'])
function required(field: string) { return ['business_date', 'amount', 'voucher_id', 'account', 'debit', 'credit'].includes(field) }
function failure(cause: unknown) { if (!disposed) error.value = cause instanceof Error ? cause.message : String(cause) }
async function request<T>(path: string, options: RequestInit = {}) { return api<T>(path, { ...options, signal: controller.signal }) }
async function refreshList() { jobs.value = await request(`/ocr/runs?book_id=${encodeURIComponent(props.bookId)}`) }
function inspect(line: OcrLine) { selectedLine.value = line; page.value = line.locator.page }
function locate(field: string) { const line = lines.value.find(l => l.observation_id === bindings.value[field]); if (line) inspect(line) }
function resetForm() {
  const record = latest.value?.payload.record
  kind.value = record?.kind || 'invoice'; sourceId.value = record?.source_id || ''; values.value = {}
  if (record) for (const [key, value] of Object.entries(record)) if (value != null) values.value[key] = String(value)
  bindings.value = Object.fromEntries(Object.keys(labels).map(key => [key, latest.value?.payload.field_evidence[key]?.[0] || '']))
  comment.value = latest.value?.payload.comment || ''; confirmed.value = false
}
async function load(id: string, reset = false) {
  const generation = selection
  const fresh = await request<OcrRun>(`/ocr/runs/${id}?book_id=${encodeURIComponent(props.bookId)}`)
  if (disposed || generation !== selection || chosen.value !== id) return
  run.value = fresh
  if (reset) { page.value = 1; selectedLine.value = undefined; resetForm() }
  const summary = jobs.value.find(j => j.id === id); if (summary) summary.status = fresh.status
  if (['queued', 'running'].includes(fresh.status)) timer = setTimeout(() => { void load(id).catch(failure) }, 1800)
}
async function selectJob() {
  selection++; clearTimeout(timer); run.value = undefined; error.value = ''; message.value = ''
  if (chosen.value) { busy.value = true; try { await load(chosen.value, true) } catch (e) { failure(e) } finally { busy.value = false } }
}
async function upload() {
  if (!file.value || busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try {
    const form = new FormData(); form.append('book_id', props.bookId); form.append('file', file.value)
    const created = await request<OcrRun>('/ocr/runs', { method: 'POST', body: form })
    if (disposed) return
    chosen.value = created.id; selection++; clearTimeout(timer)
    await refreshList(); await load(created.id, true)
  } catch (e) { failure(e) } finally { busy.value = false }
}
async function save() {
  if (!run.value || busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try {
    const record = { source_id: sourceId.value.trim(), kind: kind.value, period: props.period, currency: props.currency } as LedgerInput
    const evidence: Record<string, string[]> = {}
    for (const field of fields.value) {
      const value = values.value[field]?.trim()
      if (!value) continue
      Object.assign(record, { [field]: value }); evidence[field] = bindings.value[field] ? [bindings.value[field]!] : []
    }
    if (Object.values(evidence).some(ids => !ids.length)) throw new Error('请为每个填写字段选择原文行。')
    if (!confirmed.value) throw new Error('请先对照原件核实填写内容。')
    const payload: OcrReviewInput = { expected_parent_id: latest.value?.id || null, expected_result_digest: run.value.result_digest, expected_book_revision: run.value.book_revision, record, field_evidence: evidence, comment: comment.value, confirmed: true }
    const saved = await request<{ preview: ImportPreview }>(`/ocr/runs/${run.value.id}/reviews?book_id=${encodeURIComponent(props.bookId)}`, { method: 'POST', body: JSON.stringify(payload) })
    if (disposed) return
    emit('preview', saved.preview); await load(run.value.id); confirmed.value = false
    message.value = '人工复核已保存为新版本。请在下方预览，再确认导入并建立快照。'
  } catch (e) { failure(e) } finally { busy.value = false }
}
async function reopenPreview() {
  if (!latest.value) return
  busy.value = true; error.value = ''
  try {
    const batches = await request<ImportPreview[]>(`/books/${props.bookId}/imports`)
    const batch = batches.find(b => b.id === latest.value?.import_id)
    if (batch && !disposed) emit('preview', batch)
    if (run.value) await load(run.value.id)
  } catch (e) { failure(e) } finally { busy.value = false }
}
onMounted(async () => {
  try {
    const capability = await request<{ available: boolean }>('/ocr/capabilities'); enabled.value = capability.available
    await refreshList()
    if (jobs.value[0]) { chosen.value = jobs.value[0].id; await selectJob() }
  } catch (e) { failure(e) } finally { ready.value = true }
})
onBeforeUnmount(() => { disposed = true; selection++; controller.abort(); clearTimeout(timer) })
</script>

<template>
  <section class="panel spaced ocr-panel" aria-label="扫描件人工核实">
    <div class="panel-heading"><div><span class="section-number">OCR</span><h2>扫描件人工核实</h2></div><span class="badge neutral">本地识别 · 先核实后导入</span></div>
    <p class="intro">从原件逐项核对日期、金额与编号。识别置信度仅供参考，填写字段后须选择原文行；保存复核不会自动入账。</p>
    <p v-if="ready && !enabled" class="notice warning">本地 OCR 环境尚未启用，可查看已有识别记录。</p>
    <div class="ocr-controls"><label>扫描原件<input type="file" accept=".png,.jpg,.jpeg,.pdf" :disabled="busy || !enabled" @change="file=($event.target as HTMLInputElement).files?.[0]"/></label><button class="button primary" :disabled="!file || busy || !enabled" @click="upload">上传并本地识别</button><span>PNG / JPEG / PDF · 最大 20 MB</span></div>
    <label v-if="jobs.length" class="job-select">识别记录<select v-model="chosen" :disabled="busy" @change="selectJob"><option v-for="job in jobs" :key="job.id" :value="job.id">{{ job.created_at.slice(0,19).replace('T',' ') }} · {{ statuses[job.status] || job.status }} · {{ job.id.slice(0,8) }}</option></select></label>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p><p v-if="message" class="notice success" role="status">{{ message }}</p>
    <template v-if="run">
      <div class="run-caption"><strong>{{ run.document_name }}</strong><span class="badge" :class="run.status==='failed'?'danger':'warning'">{{ statuses[run.status] }}</span><button class="text-button" :disabled="busy" @click="selectJob">刷新记录</button></div>
      <p v-if="['queued','running'].includes(run.status)" role="status" class="intro">正在本地识别，完成后自动显示原文。此时尚未生成业务记录。</p>
      <p v-if="run.error" class="notice error">{{ run.error }}。原件已保留，可重新上传开始新任务。</p>
      <p v-if="run.stale" class="notice warning">账套政策或期间已改变，请在当前版本重新识别原件。</p>
      <p v-if="run.status==='partial'" class="notice warning">部分页面识别失败；失败页不能作为字段引用。请查看逐页状态并补充来源。</p>
      <div v-if="['complete','partial','failed'].includes(run.status)" class="ocr-workspace">
        <div class="source-column"><div class="page-bar"><h3>01 / 对照原件</h3><label v-if="pages.length>1">页码<select v-model="page" aria-label="页码"><option v-for="p in pages" :key="p.page" :value="p.page">第 {{ p.page }} 页{{ p.status==='failed'?' · 失败':'' }}</option></select></label></div>
          <OcrSourcePreview :key="run.document_id" :document-id="run.document_id" :media-type="run.media_type" :page="page" :line="selectedLine" :image="currentPage?.image"/>
          <p v-if="currentPage?.status==='failed'" class="notice error">本页识别失败：{{ currentPage.error?.type }}</p>
          <div class="ocr-lines" aria-label="待核实原文行"><button v-for="line in currentPage?.observations || []" :key="line.observation_id" type="button" :class="{selected:selectedLine?.observation_id===line.observation_id}" @click="inspect(line)"><span>{{ line.locator.line }}</span><strong>{{ line.text }}</strong><small>{{ (line.confidence*100).toFixed(1) }}%</small></button><p v-if="!currentPage?.observations?.length" class="intro">{{ currentPage?.status==='failed'?'本页没有可用引用。':'本页未检出文字，不会自动生成空记录。' }}</p></div>
        </div>
        <form class="field-column" @submit.prevent="save"><h3>02 / 人工填写字段</h3><p class="intro">每次复核形成一条业务记录。无内容的可选字段请留空。</p>
          <fieldset :disabled="!canReview || busy"><div class="context-grid"><label>记录类型<select v-model="kind" aria-label="OCR记录类型"><option v-for="(label,key) in kinds" :key="key" :value="key">{{ label }}</option></select></label><label>来源编号<input v-model="sourceId" aria-label="OCR来源编号" required maxlength="200" placeholder="填写账套内唯一编号"/></label></div><p class="context-note">会计期间 {{ period }} · 币种 {{ currency }}</p>
            <div v-for="field in fields" :key="field" class="field-binding"><label :for="`ocr-value-${field}`">{{ labels[field] }}<span v-if="required(field)"> *</span></label><input :id="`ocr-value-${field}`" v-model="values[field]" :type="field==='business_date'?'date':'text'" :inputmode="['amount','debit','credit'].includes(field)?'decimal':undefined" :required="required(field)" :maxlength="field==='description'?2000:200"/><select v-model="bindings[field]" :aria-label="`${labels[field]}原文行`" :required="!!values[field]?.trim()" @change="locate(field)"><option value="">选择支持该字段的原文行</option><option v-for="line in lines" :key="line.observation_id" :value="line.observation_id">P{{ line.locator.page }} · {{ line.locator.line }} · {{ line.text }}</option></select></div>
            <label class="review-note">核对说明<textarea v-model="comment" required maxlength="2000" rows="2" placeholder="记录人工纠正内容与核对依据"/></label><label class="confirmation"><input v-model="confirmed" type="checkbox" required/>我已对照原件核实字段、类型、期间与币种；识别文字本身仍为待核实观察。</label><button class="button primary" type="submit">保存人工复核并生成预览</button>
          </fieldset><p v-if="locked" class="notice warning">该记录已导入，原记录保持只读；后续调整请进入模拟分支。</p>
        </form>
      </div>
      <div v-if="run.reviews.length" class="review-history"><h3>复核版本</h3><ol><li v-for="review in run.reviews" :key="review.id"><strong>V{{ review.version }}</strong><span>{{ review.payload.comment }}</span><small>{{ review.import_status==='confirmed'?'已导入':'预览' }} · {{ review.created_at.slice(0,19).replace('T',' ') }}</small></li></ol><button class="button" :disabled="busy" @click="reopenPreview">查看最新导入预览</button></div>
    </template>
  </section>
</template>

<style scoped>
.ocr-panel{min-width:0;padding:0 22px 22px}.ocr-panel .panel-heading{padding-left:0;padding-right:0}.intro{font-size:12px;line-height:1.9;color:#738070;margin:12px 0 18px}.ocr-controls{display:flex;gap:16px;align-items:end;flex-wrap:wrap;padding:16px;background:#f5f7ef;border:1px solid #e0e6d9;border-radius:6px}.ocr-controls label,.job-select{display:grid;gap:8px;font-size:12px}.ocr-controls span{font-size:11px;color:#738070;padding-bottom:10px}.ocr-controls input{max-width:100%;font-size:12px}.job-select{max-width:620px;margin:18px 0}.run-caption{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:20px 0}.run-caption strong{font-size:13px;overflow-wrap:anywhere}.run-caption .text-button{margin-left:auto;font-size:12px}.ocr-workspace{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);gap:28px;border-top:1px solid #e1e6d9;padding-top:20px}.source-column,.field-column{min-width:0}.source-column{align-self:start;position:sticky;top:20px}.ocr-panel h3{font-size:13px;font-weight:600;margin:0 0 16px}.page-bar{display:flex;justify-content:space-between;align-items:center;gap:10px}.page-bar label{white-space:nowrap;display:flex;gap:6px;align-items:center;font-size:12px}.page-bar select{width:auto}.ocr-lines{margin-top:16px;max-height:260px;overflow:auto;display:grid;gap:5px}.ocr-lines button{display:grid;grid-template-columns:22px minmax(0,1fr) 42px;gap:8px;text-align:left;padding:9px;background:#fafbf7;border:1px solid #e2e8dc;border-radius:4px;color:#405747;cursor:pointer}.ocr-lines button.selected{background:#fff4d5;border-color:#bf9136}.ocr-lines span,.ocr-lines small{font-size:10px;color:#7c856f}.ocr-lines strong{font-size:12px;font-weight:400;overflow-wrap:anywhere}.field-column fieldset{border:0;padding:0;margin:0;min-width:0}.context-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.context-grid label,.review-note{display:grid;gap:8px;font-size:12px}.context-note{font-size:11px;color:#6c7d65;margin:12px 0 20px}.field-binding{display:grid;grid-template-columns:88px minmax(0,1fr);gap:7px 10px;margin-bottom:13px}.field-binding label{font-size:12px;padding-top:10px}.field-binding label span{color:#a26326}.field-binding select{grid-column:2;font-size:11px;background:#f7f9f3}.ocr-panel input:not([type=file]):not([type=checkbox]),.ocr-panel select,.ocr-panel textarea{width:100%;min-width:0;box-sizing:border-box;padding:9px 10px;border:1px solid #d7dfce;border-radius:4px;color:#354d3d;background-color:white;font:12px "Microsoft YaHei",sans-serif}.ocr-panel select{overflow:hidden;text-overflow:ellipsis}.ocr-panel input:disabled,.ocr-panel select:disabled{opacity:.65}.review-note{margin-top:20px}.confirmation{display:flex;align-items:start;gap:8px;font-size:11px;line-height:1.8;margin:16px 0}.confirmation input{margin-top:4px;accent-color:#246454;flex-shrink:0}.review-history{margin-top:24px;padding-top:20px;border-top:1px solid #e1e6d9}.review-history ol{padding:0;list-style:none;display:grid;gap:10px}.review-history li{display:flex;gap:12px;font-size:12px;flex-wrap:wrap}.review-history li span{flex:1;min-width:140px;overflow-wrap:anywhere}.review-history small{font-size:10px;color:#738070}.ocr-panel .notice{font-size:12px;line-height:1.8;overflow-wrap:anywhere}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid #246454;outline-offset:2px}@media(max-width:1050px){.ocr-workspace{grid-template-columns:1fr}.source-column{position:static}.ocr-controls{align-items:start}}@media(max-width:560px){.ocr-panel{padding:0 14px 18px}.context-grid{grid-template-columns:1fr}.field-binding{grid-template-columns:1fr}.field-binding select{grid-column:1}.field-binding label{padding-top:0}.ocr-controls label{width:100%}.ocr-controls input{width:100%}.ocr-panel .panel-heading{flex-wrap:wrap;gap:12px}.run-caption{gap:8px}}
</style>
