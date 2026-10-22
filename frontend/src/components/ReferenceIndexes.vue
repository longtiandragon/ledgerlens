<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import type { ReferenceChoiceInput, ReferenceSearchInput, ReferenceSupportInput } from '../generated/contracts'

const props = defineProps<{ bookId: string; snapshotId: string | null }>()
const emit = defineEmits<{ changed: [] }>()
type Parent = { source_id: string; text: string; source_binding: { metadata: { title: string; url: string }; page: number | null; offset: number; end: number } }
type Hit = { parent_chunk_id: string; score: number; text: string; parent_reference: Parent }
type Search = { id: string; query: string; status: string; stale: boolean; result_digest: string | null; error: string | null; result?: { hits?: Hit[] }; answer_boundary?: { answer_status: string; notice: string } }
type Support = { decision: string; notice: string; requirements: { kind: string; text: string; status: string; missing_evidence: string[]; matches: { parent_id: string; start: number; end: number; text: string }[] }[] }
type Choice = { id: string; version: number; stale: boolean; snapshot_id: string; payload: { mode: string; query?: string; references?: Parent[] } }
type History = { book_revision: number; choices: Choice[] }
type Capabilities = { available: boolean; error?: string; indexes: { index_id: string; title: string }[] }
const caps = ref<Capabilities>(), history = ref<History>(), searches = ref<Search[]>([]), run = ref<Search>()
const indexId = ref(''), query = ref(''), selected = ref<string[]>([]), confirmed = ref(false)
const busy = ref(false), loading = ref(true), error = ref(''), notice = ref('')
const requirementKind = ref<ReferenceSupportInput['requirements'][number]['kind']>('unspecified'), requirementText = ref(''), support = ref<Support>()
let supportEpoch = 0
let epoch = 0, runEpoch = 0, controller: AbortController | undefined, poll: ReturnType<typeof setTimeout> | undefined
const current = computed(() => history.value?.choices[0])
const explicit = computed(() => current.value?.payload.mode === 'persistent_bge' && !current.value.stale)
const pending = computed(() => run.value && ['queued', 'running'].includes(run.value.status))
const parents = computed(() => (run.value?.result?.hits || []).filter((hit, index, all) => all.findIndex(h => h.parent_chunk_id === hit.parent_chunk_id) === index))
const canChoose = computed(() => run.value?.status === 'complete' && !run.value.stale && selected.value.length > 0 && confirmed.value)
const labels: Record<string, string> = { queued: '等待本地检索', running: '正在本地检索', complete: '待人工选择', failed: '检索失败' }
const message = (e: unknown) => e instanceof Error ? e.message : String(e)
function safeUrl(value: string) { try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined } catch { return undefined } }
function resetSelection() { selected.value = []; confirmed.value = false; support.value = undefined; supportEpoch++ }
function stop() { epoch++; runEpoch++; controller?.abort(); clearTimeout(poll) }
async function request<T>(path: string, body?: unknown): Promise<T> {
  return api<T>(`/reference-indexes${path}`, { signal: controller?.signal, ...(body ? { method: 'POST', body: JSON.stringify(body) } : {}) })
}
async function loadHistory() {
  const version = epoch
  const [h, s] = await Promise.all([request<History>(`/choices?book_id=${props.bookId}`), request<Search[]>(`/searches?book_id=${props.bookId}`)])
  if (version !== epoch) return
  history.value = h; searches.value = s
}
async function openRun(id: string) {
  clearTimeout(poll); runEpoch++; resetSelection(); error.value = ''; run.value = undefined
  if (!id) return
  const version = epoch
  busy.value = true
  try { await readRun(id, version) } catch (e) { if (version === epoch) error.value = message(e) }
  finally { if (version === epoch) busy.value = false }
}
async function readRun(id: string, version: number, selection = runEpoch) {
  const value = await request<Search>(`/searches/${id}?book_id=${props.bookId}`)
  if (version !== epoch || selection !== runEpoch) return
  run.value = value
  searches.value = searches.value.map(item => item.id === value.id ? { ...item, status: value.status } : item)
  if (['queued', 'running'].includes(value.status)) poll = setTimeout(() => {
    readRun(id, version, selection).catch(e => { if (version === epoch && selection === runEpoch) error.value = message(e) })
  }, 1500)
}
async function initialize() {
  stop(); controller = new AbortController(); const version = epoch
  loading.value = true; busy.value = false; error.value = ''; notice.value = ''; run.value = undefined
  history.value = undefined; searches.value = []; resetSelection()
  try {
    const value = await request<Capabilities>('/capabilities')
    if (version !== epoch) return
    caps.value = value; indexId.value = value.indexes[0]?.index_id || ''
    await loadHistory()
  } catch (e) { if (version === epoch) error.value = message(e) }
  finally { if (version === epoch) loading.value = false }
}
async function search() {
  const version = epoch; busy.value = true; error.value = ''; notice.value = ''; clearTimeout(poll); runEpoch++; resetSelection(); run.value = undefined
  try {
    const body: ReferenceSearchInput = { book_id: props.bookId, index_id: indexId.value, query: query.value.trim() }
    const value = await request<Search>('/searches', body)
    if (version !== epoch) return
    run.value = value; await loadHistory(); if (version === epoch) await readRun(value.id, version)
  } catch (e) { if (version === epoch) error.value = message(e) }
  finally { if (version === epoch) busy.value = false }
}
async function checkSupport() {
  const active = run.value
  if (!active?.result_digest) return
  const version = epoch, selection = runEpoch, check = ++supportEpoch
  support.value = undefined; busy.value = true; error.value = ''
  try {
    const body: ReferenceSupportInput = { book_id: props.bookId, search_id: active.id,
      expected_result_digest: active.result_digest,
      requirements: [{ kind: requirementKind.value, text: requirementText.value.trim() || active.query }] }
    const value = await request<Support>('/support-checks', body)
    if (version === epoch && selection === runEpoch && check === supportEpoch) support.value = value
  } catch (e) { if (version === epoch && selection === runEpoch && check === supportEpoch) error.value = message(e) }
  finally { if (version === epoch) busy.value = false }
}
async function choose(mode: 'keyword' | 'persistent_bge') {
  if (!history.value) return
  const version = epoch; busy.value = true; error.value = ''; notice.value = ''
  try {
    const body: ReferenceChoiceInput = { book_id: props.bookId, mode, confirmed: true,
      expected_book_revision: history.value.book_revision, expected_parent_id: current.value?.id || null,
      ...(mode === 'persistent_bge' ? { search_id: run.value?.id, expected_result_digest: run.value?.result_digest, selected_parent_ids: selected.value } : {}) }
    await request('/choices', body)
    if (version !== epoch) return
    resetSelection(); await loadHistory()
    if (version !== epoch) return
    if (run.value) run.value.stale = true
    notice.value = '已追加参考选择并建立新快照；原始记录未改变，旧复核需要重新进行。'
    emit('changed')
  } catch (e) { if (version === epoch) { error.value = message(e); await loadHistory().catch(() => undefined) } }
  finally { if (version === epoch) busy.value = false }
}
// A new snapshot invalidates in-flight responses even while this panel is busy.
watch([() => props.bookId, () => props.snapshotId], initialize, { immediate: true })
watch([requirementKind, requirementText], () => { support.value = undefined; supportEpoch++ })
onBeforeUnmount(stop)
</script>

<template>
  <section class="panel spaced reference-panel" aria-label="参考条文选择">
    <div class="panel-heading"><div><span class="section-number">REF</span><h2>参考条文选择</h2></div><span class="badge" :class="explicit ? 'warning' : 'neutral'">{{ explicit ? '人工冻结条文' : '默认关键词检索' }}</span></div>
    <p class="intro">候选条文供人工核对适用性，不构成业务事实。实验性向量检索在现有离线比较中未优于关键词；仅在确认选择后，才会写入本账套的新快照。</p>
    <div v-if="error" class="notice error" role="alert">{{ error }}</div>
    <div v-if="notice" class="notice" role="status">{{ notice }}</div>
    <p v-if="loading" role="status">正在读取参考选择…</p>
    <template v-else>
      <div v-if="current" class="current-choice">
        <strong>选择版本 {{ current.version }} · {{ current.stale ? '账套政策已改变，选择已失效' : explicit ? '沿用已冻结条文' : '沿用默认关键词' }}</strong>
        <p v-if="explicit">{{ current.payload.references?.length }} 段参考随后续数据快照保留；账套政策或期间改变后需重新选择。</p>
        <button v-if="explicit" class="button" :disabled="busy" @click="choose('keyword')">恢复默认关键词并建立快照</button>
      </div>
      <form v-if="caps?.available" class="query-form" @submit.prevent="search">
        <label>本地实验索引<select v-model="indexId" :disabled="busy || pending"><option v-for="index in caps.indexes" :key="index.index_id" :value="index.index_id">{{ index.title }}</option></select></label>
        <label>要查阅的政策问题<textarea v-model="query" maxlength="1000" rows="2" :disabled="busy || pending" placeholder="例如：存货采购成本包含哪些费用"/></label>
        <button class="button primary" :disabled="busy || pending || !query.trim() || !indexId">查询本地参考</button>
        <small>本地模型与持久索引；不调用付费模型。</small>
      </form>
      <p v-else class="intro">{{ caps?.error || '本机未启用实验索引。可继续使用默认关键词检索；历史冻结条文仍可查阅。' }}</p>
      <label v-if="searches.length" class="history-select">本账套检索记录<select :value="run?.id || ''" :disabled="busy" @change="openRun(($event.target as HTMLSelectElement).value)"><option value="">选择一次查询</option><option v-for="item in searches" :key="item.id" :value="item.id">{{ item.query }} · {{ labels[item.status] || item.status }}</option></select></label>
      <div v-if="run" class="search-result">
        <div class="result-heading"><strong>{{ labels[run.status] || run.status }} · {{ run.query }}</strong><button class="text-button" :disabled="busy" @click="openRun(run.id)">刷新查询状态</button></div>
        <p v-if="run.stale" class="notice warning">检索后账套已有变化；请重新查询后选择。</p>
        <p v-if="run.error" class="notice error" role="alert">{{ run.error }}</p>
        <p v-if="pending" role="status">正在核验索引并检索，请稍候。离开页面不会取消后台任务。</p>
        <p v-if="run.answer_boundary" class="notice warning">未生成答案。{{ run.answer_boundary.notice }}</p>
        <p v-if="run.status === 'complete' && !parents.length">没有符合当前账套范围的候选条文。</p>
        <form v-if="run.status === 'complete' && !run.stale" class="query-form support-form" @submit.prevent="checkSupport">
          <label>本次需要核对什么<select v-model="requirementKind" :disabled="busy"><option value="unspecified">尚未明确需求</option><option value="policy_excerpt">查阅原文片语</option><option value="business_fact">核验企业交易事实</option><option value="applicability">判断条文适用性</option></select></label>
          <label>{{ requirementKind === 'policy_excerpt' ? '要在候选原文中逐字查找的片语（至少 4 字）' : '需要核验的事实或条件（至少 4 字）' }}<textarea v-model="requirementText" rows="2" maxlength="200" :disabled="busy" :placeholder="run.query"/></label>
          <button class="button" :disabled="busy || (requirementText.trim() || run.query).length < 4 || (requirementText.trim() || run.query).length > 200">检查证据需求</button>
          <small>选择需求类型不会授权自动回答；逐字命中也不代表语义支持。</small>
        </form>
        <div v-if="support" class="notice" role="status">
          <strong>{{ support.decision === 'manual_review' ? '已找到原文片语，仍需人工复核' : '证据不足，暂不能形成答案' }}</strong>
          <div v-for="(item, index) in support.requirements" :key="index">
            <p v-for="missing in item.missing_evidence" :key="missing">需要：{{ missing }}</p>
            <p v-for="match in item.matches" :key="match.parent_id">逐字命中「{{ match.text }}」：父条文 {{ match.parent_id }}，字符 {{ match.start }}–{{ match.end }}。请阅读下面完整父条文。</p>
          </div>
        </div>
        <div class="reference-grid">
          <article v-for="hit in parents" :key="hit.parent_chunk_id" class="reference-card">
            <label class="candidate"><input v-model="selected" type="checkbox" :value="hit.parent_chunk_id" :disabled="busy || run.stale" @change="confirmed = false"/><strong>{{ hit.parent_reference.source_binding.metadata.title }}</strong></label>
            <p class="locator">{{ hit.parent_reference.source_binding.page ? `第 ${hit.parent_reference.source_binding.page} 页 · ` : '' }}字符 {{ hit.parent_reference.source_binding.offset }}–{{ hit.parent_reference.source_binding.end }} · 相似度 {{ hit.score.toFixed(3) }}（非可信度）</p>
            <p class="matched-text">{{ hit.text }}</p>
            <details><summary>阅读已核验父条文</summary><p class="reference-text">{{ hit.parent_reference.text }}</p></details>
            <a v-if="safeUrl(hit.parent_reference.source_binding.metadata.url)" :href="safeUrl(hit.parent_reference.source_binding.metadata.url)" target="_blank" rel="noopener noreferrer">查阅来源原文 ↗</a>
          </article>
        </div>
        <template v-if="parents.length && !run.stale">
          <label class="confirmation"><input v-model="confirmed" type="checkbox" :disabled="busy || !selected.length"/>我已阅读所选条文并核对适用范围，确认建立新快照，旧复核需要重新进行。</label>
          <button class="button primary" :disabled="busy || !canChoose" @click="choose('persistent_bge')">冻结所选条文并建立快照（{{ selected.length }}）</button>
        </template>
      </div>
      <details v-if="history?.choices.length" class="choice-history"><summary>参考选择历史（最近 {{ history.choices.length }} 次）</summary><ol><li v-for="item in history.choices" :key="item.id"><strong>版本 {{ item.version }} · {{ item.payload.mode === 'keyword' ? '默认关键词' : '人工冻结条文' }}</strong><span>快照 {{ item.snapshot_id }}</span><p v-if="item.payload.query">{{ item.payload.query }}</p><details v-for="parent in item.payload.references || []" :key="parent.source_id"><summary>{{ parent.source_binding.metadata.title }}</summary><p class="reference-text">{{ parent.text }}</p></details></li></ol></details>
    </template>
  </section>
</template>

<style scoped>
.reference-panel{min-width:0;padding:0 22px 22px}.panel-heading{padding-left:0;padding-right:0;gap:12px;flex-wrap:wrap}.intro{font-size:12px;line-height:1.9;color:#738070;margin:12px 0 18px}.current-choice{background:#f5f7ef;border-left:3px solid #48755a;padding:14px 16px;margin-bottom:20px;font-size:12px}.current-choice p{line-height:1.8}.query-form{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr);gap:14px;align-items:end}.query-form label,.history-select{display:grid;gap:8px;font-size:12px;min-width:0}.query-form small{color:#738070;font-size:11px;align-self:center}.query-form .button{justify-self:start}.reference-panel select,.reference-panel textarea{box-sizing:border-box;width:100%;min-width:0;padding:10px;border:1px solid #d7dfce;border-radius:4px;color:#354d3d;background:white;font:12px "Microsoft YaHei",sans-serif}.history-select{max-width:640px;margin:24px 0 16px}.result-heading{display:flex;gap:12px;align-items:center;justify-content:space-between;font-size:13px;line-height:1.8}.result-heading strong{overflow-wrap:anywhere}.result-heading button{flex-shrink:0}.reference-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:18px 0}.reference-card{align-self:start;border:1px solid #e0e6d9;padding:16px;border-radius:6px;min-width:0}.candidate,.confirmation{display:flex;align-items:start;gap:9px;line-height:1.8;font-size:12px}.candidate input,.confirmation input{margin-top:5px;accent-color:#246454;flex-shrink:0}.locator{font-size:10px;line-height:1.8;color:#738070}.reference-card a{font-size:11px;color:#246454;display:inline-block;margin-top:14px}.reference-panel details{font-size:12px;line-height:1.8}.reference-panel summary{cursor:pointer;color:#395b46;overflow-wrap:anywhere}.matched-text{white-space:pre-wrap;overflow-wrap:anywhere;max-height:150px;overflow:auto;font-size:12px;line-height:1.9;color:#354d3d;border-left:2px solid #d7dfce;padding-left:10px}.reference-text{white-space:pre-wrap;overflow-wrap:anywhere;background:#fafbf7;padding:12px;max-height:360px;overflow:auto;font-size:12px;line-height:1.9}.confirmation{margin:20px 0}.choice-history{border-top:1px solid #e1e6d9;margin-top:24px;padding-top:20px}.choice-history ol{list-style:none;padding:0}.choice-history li{margin-top:16px;padding:12px;background:#f8faf4}.choice-history li>span{display:block;font-size:10px;color:#738070;overflow-wrap:anywhere;margin-top:6px}.notice{font-size:12px;line-height:1.8;overflow-wrap:anywhere}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:2px solid #246454;outline-offset:2px}@media(max-width:700px){.reference-panel{padding:0 14px 18px}.query-form,.reference-grid{grid-template-columns:1fr}.result-heading{flex-wrap:wrap}}
</style>
