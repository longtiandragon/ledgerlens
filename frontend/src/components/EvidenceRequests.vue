<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, post, type Evidence } from '../api'

// runsKey changes when an investigation of this case starts or finishes, so needs it raised appear without a reload.
const props = defineProps<{ caseId: string; evidence: Evidence[]; runsKey?: string }>()
interface Candidate { record_id:string;evidence_id:string|null;kind:string;source_id:string|null;business_date:string|null;amount:string|null }
interface Request { id:string;kind:string;reference:string|null;description:string;status:'open'|'fulfilled'|'withdrawn';created_at:string;candidates:Candidate[];candidate_snapshot_id:string|null;events:{action:string;created_at:string;record_ids:string[];comment:string}[] }
const requests = ref<Request[]>([])
const suggestion = ref<{ kind:string; rule_id:string }|null>(null)
const raised = ref<string[]>([])
const kind = ref('receipt'), reference = ref(''), description = ref(''), comment = ref('')
const busy = ref(false), error = ref('')
let generation = 0
const kindLabels: Record<string,string> = { order:'订单', receipt:'收货单', invoice:'发票', bank:'银行流水', journal:'凭证分录', opening:'期初余额', trial_balance:'试算平衡表' }
const statusLabels: Record<string,string> = { open:'待取得', fulfilled:'已人工确认取得', withdrawn:'已撤回' }
const references = computed(() => [...new Set(props.evidence.flatMap(item => ['order_id','invoice_id','reference','source_id']
  .map(key => item.record?.[key]).filter((value): value is string => typeof value === 'string' && value.length > 0)))].sort())
const message = (cause: unknown) => cause instanceof Error ? cause.message : '补证事项读取失败'

async function load() {
  const current = ++generation, caseId = props.caseId
  try {
    const result = await api<{ requests: Request[]; raised_needs: string[]; suggestion: { kind:string; rule_id:string }|null }>(`/cases/${encodeURIComponent(caseId)}/evidence-requests`)
    if (current !== generation) return
    requests.value = result.requests; suggestion.value = result.suggestion; raised.value = result.raised_needs
    if (result.suggestion && !description.value) kind.value = result.suggestion.kind
  } catch (cause) { if (current === generation) error.value = message(cause) }
}
async function create() {
  busy.value = true; error.value = ''
  try {
    await post(`/cases/${encodeURIComponent(props.caseId)}/evidence-requests`, { kind: kind.value, reference: reference.value || null, description: description.value })
    description.value = ''; reference.value = ''; await load()
  } catch (cause) { error.value = message(cause) } finally { busy.value = false }
}
async function fulfil(request: Request) {
  busy.value = true; error.value = ''
  try {
    await post(`/evidence-requests/${encodeURIComponent(request.id)}/fulfil`, { snapshot_id: request.candidate_snapshot_id, record_ids: request.candidates.map(c => c.record_id), comment: comment.value })
    comment.value = ''; await load()
  } catch (cause) { error.value = message(cause) } finally { busy.value = false }
}
watch(() => props.caseId, () => { requests.value = []; error.value = ''; void load() }, { immediate: true })
watch(() => props.runsKey, (now, before) => { if (before !== undefined && now !== before) void load() })
onBeforeUnmount(() => { generation++ })
function time(value: string) { return value.replace('T', ' ').replace(/(?:\.\d+)?(?:Z|\+00:00)$/, ' UTC') }
</script>

<template>
  <section class="evidence-requests" aria-label="补证事项">
    <div class="er-head"><strong>补证事项</strong><small>新资料须经导入并形成新快照；候选记录只有在人工确认后才记为已取得。</small></div>
    <div v-if="error" class="notice error" role="alert">{{ error }}</div>
    <article v-for="item in requests" :key="item.id" class="er-item">
      <header><span :class="['badge', item.status === 'fulfilled' ? 'success' : item.status === 'open' ? 'warning' : 'neutral']">{{ statusLabels[item.status] }}</span><span>{{ kindLabels[item.kind] || item.kind }}<template v-if="item.reference"> · {{ item.reference }}</template></span></header>
      <p>{{ item.description }}</p>
      <div v-if="item.status === 'open' && item.candidates.length" class="er-candidates">
        <p>较新快照中出现 {{ item.candidates.length }} 条候选记录：</p>
        <ul><li v-for="candidate in item.candidates" :key="candidate.record_id"><code>{{ candidate.source_id }}</code> {{ candidate.business_date }} <span class="raw">{{ candidate.amount }}</span></li></ul>
        <label>核对说明<input v-model="comment" maxlength="2000" placeholder="例如：已核对原件与订单编号"/></label>
        <button class="button primary" :disabled="busy" @click="fulfil(item)">确认这些记录即所需资料</button>
      </div>
      <p v-else-if="item.status === 'open'" class="caption">尚未在较新快照中发现符合的记录。</p>
      <p v-for="event in item.events" :key="event.created_at" class="caption">{{ statusLabels[event.action] }} · {{ time(event.created_at) }}<template v-if="event.comment"> · {{ event.comment }}</template></p>
    </article>
    <div v-if="raised.length" class="er-raised"><p>调查中提出的待补资料（规则或模型建议，需人工选择种类后登记）：</p><ul><li v-for="need in raised" :key="need"><span>{{ need }}</span><button type="button" class="text-button" @click="description = need">采用</button></li></ul></div>
    <form class="er-form" @submit.prevent="create">
      <label>资料种类<select v-model="kind" aria-label="补证资料种类"><option v-for="(text, value) in kindLabels" :key="value" :value="value">{{ text }}</option></select></label>
      <label>关联编号<select v-model="reference" aria-label="补证关联编号"><option value="">不限定</option><option v-for="value in references" :key="value" :value="value">{{ value }}</option></select></label>
      <label class="er-wide">需要什么<input v-model="description" aria-label="补证说明" maxlength="1000" required placeholder="例如：订单剩余部分的收货单"/></label>
      <button class="button" :disabled="busy || !description">登记补证事项</button>
      <small v-if="suggestion" class="caption">根据规则 {{ suggestion.rule_id }}，建议的资料种类为{{ kindLabels[suggestion.kind] }}。</small>
    </form>
  </section>
</template>

<style scoped>
.evidence-requests{border:1px solid #e3e8df;border-radius:6px;padding:14px 16px;margin:16px 0}
.er-head{display:flex;flex-direction:column;gap:4px;margin-bottom:8px}.er-head small{font-size:11px;color:#6b756f}
.er-item{border-top:1px solid #edf0eb;padding:10px 0}.er-item header{display:flex;gap:10px;align-items:center;font-size:12px}
.er-item p{font-size:12px;margin:6px 0}.er-candidates{background:#f7faf5;padding:8px 10px;border-radius:4px}
.er-candidates ul{font-size:12px;margin:4px 0 8px;padding-left:18px}.er-candidates .raw{font-family:monospace}
.er-candidates label,.er-form label{display:flex;flex-direction:column;gap:4px;font-size:11px;color:#5f6b5c}
.er-candidates input,.er-form input,.er-form select{padding:7px;border:1px solid #dfe6df;border-radius:4px;font-size:12px;min-width:0}
.er-candidates .button{margin-top:8px}
.er-raised{font-size:12px;border-top:1px solid #edf0eb;padding-top:8px}.er-raised ul{padding-left:18px;margin:4px 0}.er-raised li{display:flex;gap:10px;justify-content:space-between;align-items:center}
.er-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;align-items:end;margin-top:12px}
.er-form .er-wide{grid-column:1/-1}.er-form .caption{grid-column:1/-1}
@media(max-width:600px){.er-form{grid-template-columns:minmax(0,1fr)}}
</style>
