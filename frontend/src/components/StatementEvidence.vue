<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref } from 'vue'
import { NDrawer, NDrawerContent } from 'naive-ui'
import { api } from '../api'
import type {
  StatementDataset as Snapshot,
  StatementDatasetList,
  StatementObservation as Observation,
  StatementReportsPage as ReportPage,
  StatementEvidenceDetail as EvidenceDetail,
} from '../generated/contracts'
import Icon from './Icon.vue'

const StatementPdfPreview = defineAsyncComponent(() => import('./StatementPdfPreview.vue'))
type Filter = 'all' | 'observed' | 'rejected'
const snapshots = ref<Snapshot[]>()
const selectedKey = ref('')
const reports = ref<ReportPage>()
const query = ref('')
const filter = ref<Filter>('all')
const loading = ref(false)
const listLoading = ref(false)
const error = ref('')
const listError = ref('')
const drawerOpen = ref(false)
const evidenceLoading = ref(false)
const evidenceError = ref('')
const evidence = ref<EvidenceDetail>()
const notice = ref('')
const keyFor = (item: Snapshot) => JSON.stringify([item.dataset_scope_id, item.snapshot_id])
const snapshot = computed(() => snapshots.value?.find(item => keyFor(item) === selectedKey.value))
const scope = computed(() => snapshot.value ? `/statement-datasets/${encodeURIComponent(snapshot.value.dataset_scope_id)}/snapshots/${encodeURIComponent(snapshot.value.snapshot_id)}` : '')
const metrics: Record<Observation['metric'], string> = { total_assets: '资产总计', total_liabilities: '负债合计', total_equity: '所有者权益合计' }
const pageRange = computed(() => reports.value ? reports.value.total ? `${reports.value.offset + 1}–${reports.value.offset + reports.value.items.length}` : '0' : '—')
let snapshotGeneration = 0
let listGeneration = 0
let evidenceGeneration = 0
let disposed = false
let snapshotController: AbortController | undefined
let listController: AbortController | undefined
let evidenceController: AbortController | undefined
let opener: HTMLElement | undefined

function message(cause: unknown) { return cause instanceof Error ? cause.message : '年报证据读取失败' }
function safeSource(raw: string): string | undefined {
  try {
    const url = new URL(raw)
    if (url.protocol === 'https:' && !url.username && !url.password) return url.href
  } catch { /* A source URL is displayed only when it is safe to navigate. */ }
  return undefined
}
function timestamp(value: string) { return value.replace('T', ' ').replace(/(?:\.\d+)?(?:Z|\+00:00)$/, ' UTC') }
function closeEvidence(restoreFocus = true) {
  evidenceGeneration++
  evidenceController?.abort()
  drawerOpen.value = false
  evidence.value = undefined
  evidenceError.value = ''
  evidenceLoading.value = false
  if (restoreFocus && opener?.isConnected) opener.focus()
}
function clearList() {
  listGeneration++
  listController?.abort()
  reports.value = undefined
  listError.value = ''
  listLoading.value = false
  closeEvidence(false)
}
async function loadSnapshots() {
  const requested = ++snapshotGeneration
  snapshotController?.abort()
  snapshotController = new AbortController()
  loading.value = true
  error.value = ''
  clearList()
  const previous = selectedKey.value
  try {
    const result = await api<StatementDatasetList>('/statement-datasets', { signal: snapshotController.signal })
    if (disposed || requested !== snapshotGeneration) return
    snapshots.value = result.snapshots
    notice.value = result.notice
    selectedKey.value = result.snapshots.some(item => keyFor(item) === previous) ? previous : result.snapshots[0] ? keyFor(result.snapshots[0]) : ''
    if (selectedKey.value) await loadReports()
  } catch (cause) {
    if (!disposed && requested === snapshotGeneration) error.value = message(cause)
  } finally {
    if (!disposed && requested === snapshotGeneration) loading.value = false
  }
}
async function changeSnapshot() {
  snapshotGeneration++
  snapshotController?.abort()
  loading.value = false
  query.value = ''
  filter.value = 'all'
  error.value = ''
  clearList()
  await loadReports()
}
async function loadReports(offset = 0) {
  const requestedScope = scope.value
  if (!requestedScope) return
  const requested = ++listGeneration
  listController?.abort()
  listController = new AbortController()
  listLoading.value = true
  listError.value = ''
  reports.value = undefined
  closeEvidence(false)
  try {
    const params = new URLSearchParams({ q: query.value.trim(), status: filter.value, offset: String(offset), limit: '30' })
    const result = await api<ReportPage>(`${requestedScope}/reports?${params}`, { signal: listController.signal })
    if (!disposed && requested === listGeneration && requestedScope === scope.value) reports.value = result
  } catch (cause) {
    if (!disposed && requested === listGeneration && requestedScope === scope.value) listError.value = message(cause)
  } finally {
    if (!disposed && requested === listGeneration && requestedScope === scope.value) listLoading.value = false
  }
}
async function inspect(observation: Observation, event: MouseEvent) {
  const requestedScope = scope.value
  if (!requestedScope) return
  opener = event.currentTarget instanceof HTMLElement ? event.currentTarget : undefined
  const requested = ++evidenceGeneration
  evidenceController?.abort()
  evidenceController = new AbortController()
  evidence.value = undefined
  evidenceError.value = ''
  evidenceLoading.value = true
  drawerOpen.value = true
  try {
    const result = await api<EvidenceDetail>(`${requestedScope}/evidence/${encodeURIComponent(observation.evidence_id)}`, { signal: evidenceController.signal })
    if (!disposed && requested === evidenceGeneration && requestedScope === scope.value && drawerOpen.value) evidence.value = result
  } catch (cause) {
    if (!disposed && requested === evidenceGeneration && requestedScope === scope.value && drawerOpen.value) evidenceError.value = message(cause)
  } finally {
    if (!disposed && requested === evidenceGeneration && requestedScope === scope.value) evidenceLoading.value = false
  }
}
onMounted(loadSnapshots)
onBeforeUnmount(() => {
  disposed = true
  snapshotGeneration++
  listGeneration++
  evidenceGeneration++
  snapshotController?.abort()
  listController?.abort()
  evidenceController?.abort()
})
</script>

<template>
  <div class="statement-evidence">
    <section class="statement-banner">
      <div class="statement-banner-copy"><span class="statement-kicker">PUBLIC ANNUAL REPORTS / 来源证据</span><h2>让报表数字，<br/>回到它的原始页面。</h2><p>读取公开年报合并资产负债表中的资产、负债与权益总额。每一条提取记录都绑定快照、原 PDF 和单元格位置。</p><span class="statement-boundary"><Icon name="shield" :size="14"/>独立公开资料 · 原文件只读 · 提取语义未复核</span></div>
      <dl class="statement-counts" aria-label="年报快照统计">
        <div><dt>快照年报</dt><dd>{{ snapshot?.report_count.toLocaleString() ?? '—' }}</dd></div>
        <div><dt>已提取年报</dt><dd>{{ snapshot?.observed_count.toLocaleString() ?? '—' }}</dd></div>
        <div class="statement-rejected-count"><dt>拒绝解析</dt><dd>{{ snapshot?.rejected_count.toLocaleString() ?? '—' }}</dd></div>
      </dl>
    </section>

    <div v-if="error" class="notice error statement-alert" role="alert"><strong>快照读取失败</strong><span>{{ error }}</span><button type="button" class="text-button" @click="loadSnapshots">重试</button></div>
    <section class="panel statement-picker">
      <label for="statement-snapshot">年报数据快照</label>
      <select id="statement-snapshot" v-model="selectedKey" :disabled="loading || !snapshots?.length" @change="changeSnapshot">
        <option v-if="!snapshots?.length" value="">{{ loading ? '正在读取快照…' : error ? '快照未取得' : '尚无已导入快照' }}</option>
        <option v-for="item in snapshots" :key="keyFor(item)" :value="keyFor(item)">{{ item.title }} · {{ timestamp(item.observed_at) }} · 已提取 {{ item.observed_count }} / 拒绝 {{ item.rejected_count }} · {{ item.snapshot_id.slice(-8) }}</option>
      </select>
      <button type="button" class="button" :disabled="loading" @click="loadSnapshots"><Icon name="refresh" :size="14"/>刷新快照</button>
    </section>
    <p class="statement-note">公开来源的可下载性不等于再分发许可。来源许可未明确；原金额保留字符串与单位，提取结果不会自动成为账务事实。</p>
    <p v-if="notice" class="statement-api-notice">{{ notice }}</p>
    <div v-if="loading" class="panel statement-empty" role="status">正在读取并校验年报快照…</div>
    <section v-else-if="!snapshot && !error" class="panel statement-empty"><Icon name="file" :size="32"/><h3>尚无年报证据快照</h3><p>完成公开年报来源校验与报表提取后，在这里查看原数字、拒绝原因和原文定位。此页无需选择会计账套。</p></section>

    <section v-if="snapshot" class="panel statement-index" aria-label="年报报告列表" :aria-busy="listLoading">
      <div class="statement-index-heading"><div><span class="statement-kicker">CONSOLIDATED BALANCE SHEET</span><h3>合并资产负债表</h3></div><span class="badge neutral">只读快照</span></div>
      <div class="statement-scope"><span>取得时间 {{ timestamp(snapshot.observed_at) }}</span><code>快照 {{ snapshot.snapshot_id }}</code><details><summary>解析器版本</summary><code>{{ snapshot.parser_source_sha256 }}</code></details></div>
      <form class="statement-filters" @submit.prevent="loadReports()">
        <label for="statement-search">查找公司或报告<div class="statement-search-field"><Icon name="search" :size="15"/><input id="statement-search" v-model="query" placeholder="公司名称、代码或报告编号" maxlength="200"/></div></label>
        <label for="statement-status">解析状态<select id="statement-status" v-model="filter" @change="loadReports()"><option value="all">全部报告</option><option value="observed">已提取</option><option value="rejected">拒绝解析</option></select></label>
        <button type="submit" class="button primary" :disabled="listLoading">查找</button>
      </form>
      <div class="statement-list-summary" aria-live="polite"><span v-if="reports">{{ reports.total }} 份匹配年报</span><span v-else>{{ listLoading ? '正在读取报告…' : '报告列表未取得' }}</span><span>本期 / 前期金额按原表单位展示</span></div>
      <div v-if="listError" class="notice error statement-list-error" role="alert"><strong>报告列表读取失败</strong><span>{{ listError }}</span><button type="button" class="text-button" @click="loadReports()">重试读取</button></div>
      <div v-if="listLoading" class="statement-loading" role="status">正在读取当前快照的报告记录…</div>
      <div v-else-if="reports && !reports.items.length" class="statement-empty">当前筛选没有匹配报告。</div>
      <div v-else class="statement-reports">
        <article v-for="report in reports?.items ?? []" :key="report.report_id" class="statement-report" :class="{ rejected: report.status === 'rejected' }" :aria-label="`${report.company_name} ${report.report_year} 年报`">
          <header><div><span class="statement-company-code">{{ report.company_code }}</span><h4>{{ report.company_name }}</h4><span class="statement-report-year">{{ report.report_year }} 年报</span></div><span :class="['badge', report.status === 'rejected' ? 'danger' : 'warning']">{{ report.status === 'rejected' ? '拒绝解析' : '提取未复核' }}</span></header>
          <p v-if="report.status === 'rejected'" class="statement-rejection"><Icon name="info" :size="15"/><span>此报告未形成可用提取证据。原因：<code>{{ report.error_code ?? '未提供拒绝代码' }}</code></span></p>
          <div v-else-if="report.observations.length" class="statement-observations"><table><caption class="statement-visually-hidden">{{ report.company_name }} 本期与前期报表提取金额</caption><thead><tr><th>报表项目</th><th>本期金额</th><th>前期金额</th><th>来源证据</th></tr></thead><tbody><tr v-for="observation in report.observations" :key="observation.evidence_id"><th scope="row">{{ metrics[observation.metric] }}<small>{{ observation.currency }} · {{ observation.unit }}</small></th><td class="statement-amount">{{ observation.amount_reported }}</td><td class="statement-amount previous">{{ observation.previous_amount_reported }}</td><td><button type="button" class="text-button" :aria-label="`${report.company_name} ${metrics[observation.metric]} 原文定位`" @click="inspect(observation, $event)">第 {{ observation.pdf_page }} 页 <Icon name="arrow" :size="13"/></button></td></tr></tbody></table></div>
          <p v-else class="statement-rejection">接口未提供可用观察记录，金额未取得。</p>
          <footer><div v-if="report.balance_check" class="statement-balance"><span>{{ report.balance_check.equal_as_reported ? '原表金额等式相等' : '原表金额等式存在差值' }}</span><code>差值 {{ report.balance_check.difference_reported_unit }}（原表单位）</code><small>程序观察，不等于会计结论已复核</small></div><div class="statement-report-source"><a v-if="safeSource(report.url)" class="text-button" :href="safeSource(report.url)" target="_blank" rel="noopener noreferrer">公开来源 <Icon name="link" :size="12"/></a><span v-else>公开来源链接不可用</span><span>许可未明确</span><details><summary>内容指纹</summary><code>{{ report.raw_sha256 }}</code></details></div></footer>
        </article>
      </div>
      <div v-if="reports" class="statement-pagination" aria-label="年报分页"><button type="button" class="button" :disabled="listLoading || reports.offset === 0" @click="loadReports(Math.max(0, reports.offset - reports.limit))">上一页</button><span>{{ pageRange }} / {{ reports.total }}</span><button type="button" class="button" :disabled="listLoading || reports.next_offset === null" @click="loadReports(reports.next_offset!)">下一页</button></div>
    </section>

    <NDrawer :show="drawerOpen" width="min(1120px, 100vw)" placement="right" @update:show="value => { if (!value) closeEvidence() }">
      <NDrawerContent><template #header><div class="statement-drawer-heading"><div><span class="statement-kicker">SOURCE LOCATOR</span><strong>年报证据原文定位</strong></div><button type="button" class="text-button" aria-label="关闭年报证据" @click="closeEvidence()"><Icon name="close" :size="17"/>关闭</button></div></template>
        <section class="statement-evidence-detail" aria-label="年报证据定位">
          <p v-if="evidenceLoading" class="statement-loading" role="status">正在读取当前快照的来源定位…</p>
          <div v-if="evidenceError" class="notice error" role="alert"><strong>证据读取失败</strong><span>{{ evidenceError }}</span></div>
          <template v-if="evidence && snapshot && drawerOpen">
            <div class="statement-detail-title"><div><h3>{{ evidence.report.company_name }} · {{ evidence.report.report_year }} 年报</h3><p>{{ metrics[evidence.observation.metric] }} / {{ evidence.observation.unit }} / {{ evidence.observation.currency }}</p></div><span class="badge warning">提取语义未复核</span></div>
            <div class="statement-detail-grid">
              <div class="statement-detail-document"><StatementPdfPreview :key="`${scope}/${evidence.observation.evidence_id}`" :dataset-scope-id="snapshot.dataset_scope_id" :snapshot-id="snapshot.snapshot_id" :report-id="evidence.report.report_id" :name="`${evidence.report.company_name} ${evidence.report.report_year} 年报`" :initial-page="evidence.observation.pdf_page" :highlight="evidence.observation.current_cell_bbox" :page-info="evidence.page"/></div>
              <div class="statement-detail-record">
                <dl class="statement-value-pair"><div><dt>本期原金额（{{ evidence.observation.unit }}）</dt><dd>{{ evidence.observation.amount_reported }}</dd></div><div><dt>前期原金额（{{ evidence.observation.unit }}）</dt><dd>{{ evidence.observation.previous_amount_reported }}</dd></div><div><dt>接口标准化金额（CNY 元）</dt><dd>{{ evidence.observation.normalized_cny }}</dd></div></dl>
                <h4>单元格定位</h4><dl class="statement-locator"><dt>原 PDF 页</dt><dd>{{ evidence.observation.pdf_page }}</dd><dt>表 / 行</dt><dd>{{ evidence.observation.table }} / {{ evidence.observation.table_row }}</dd><dt>本期列（从 0 起）</dt><dd>{{ evidence.observation.current_table_column_zero_based }}</dd><dt>前期列（从 0 起）</dt><dd>{{ evidence.observation.previous_table_column_zero_based }}</dd><dt>项目原文</dt><dd>{{ evidence.observation.label_cell }}</dd><dt>本期原单元格</dt><dd>{{ evidence.observation.current_cell }}</dd><dt>前期原单元格</dt><dd>{{ evidence.observation.previous_cell }}</dd></dl>
                <p class="statement-note">页码和坐标只证明提取位置可回查；年份、口径、单位及业务含义仍需复核。</p>
                <details open class="statement-detail-lines"><summary>来源文字行</summary><p v-if="!evidence.observation.text_lines.length">未提供来源文字行。</p><div v-for="line in evidence.observation.text_lines" :key="line.line"><span>行 {{ line.line }}</span><p>{{ line.text }}</p></div></details>
                <details v-if="evidence.observation.table_rows?.length || evidence.observation.label_fragments?.length" open class="statement-label-fragments"><summary>项目标签原单元格</summary><p v-if="evidence.observation.table_rows?.length" class="statement-note">关联原表行索引：{{ evidence.observation.table_rows.join(' / ') }}</p><div v-if="evidence.observation.label_fragments?.length" class="statement-fragment-table" tabindex="0" aria-label="项目标签原单元格与坐标"><table><thead><tr><th>原行索引</th><th>列（从 0 起）</th><th>标签原文</th><th>原始坐标</th></tr></thead><tbody><tr v-for="(fragment, index) in evidence.observation.label_fragments" :key="index"><td>{{ fragment.table_row }}</td><td>{{ fragment.table_column_zero_based }}</td><td>{{ fragment.text }}</td><td><code>{{ fragment.bbox.join(', ') }}</code></td></tr></tbody></table></div></details>
                <details><summary>列标题与提取上下文</summary><pre>{{ JSON.stringify({ column_header: evidence.column_header, context: evidence.context }, null, 2) }}</pre></details>
                <details><summary>单元格坐标与原文指纹</summary><pre>{{ JSON.stringify({ current_cell_bbox: evidence.observation.current_cell_bbox, previous_cell_bbox: evidence.observation.previous_cell_bbox, page: evidence.page, raw_sha256: evidence.report.raw_sha256 }, null, 2) }}</pre></details>
                <p class="statement-evidence-id">{{ evidence.observation.evidence_id }}</p><a v-if="safeSource(evidence.report.url)" class="text-button statement-detail-source" :href="safeSource(evidence.report.url)" target="_blank" rel="noopener noreferrer">查看公开来源 <Icon name="link" :size="13"/></a>
              </div>
            </div>
          </template>
        </section>
      </NDrawerContent>
    </NDrawer>
  </div>
</template>

<style scoped>
.statement-fragment-table{overflow-x:auto;max-width:100%;margin-top:10px}.statement-fragment-table table{min-width:440px}.statement-fragment-table th,.statement-fragment-table td{padding:8px;font-size:9px;vertical-align:top}.statement-fragment-table td{white-space:pre-wrap;overflow-wrap:anywhere}.statement-fragment-table code{font-size:9px;white-space:nowrap}
.statement-evidence{--statement-green:#315d43;--statement-line:#dfe5d4;min-width:0}.statement-banner{display:flex;justify-content:space-between;align-items:center;gap:35px;padding:30px 34px;background:#eaf0e2;border:1px solid #d8e3cd;border-left:4px solid var(--statement-green);border-radius:7px}.statement-banner-copy{max-width:640px}.statement-kicker{font-size:9px;letter-spacing:1.8px;font-weight:700;color:#69845c}.statement-banner h2{font-family:'Noto Serif SC','SimSun',serif;font-size:29px;font-weight:500;line-height:1.5;letter-spacing:.8px;color:#365a3a;margin:12px 0}.statement-banner p{font-size:12px;line-height:1.95;color:#6e8260;max-width:560px;margin:0 0 18px}.statement-boundary{display:inline-flex;align-items:center;gap:7px;font-size:10px;color:#6e825c;padding-top:11px;border-top:1px solid #ccd8c1}.statement-counts{display:grid;grid-template-columns:repeat(3,minmax(64px,1fr));gap:25px;flex-shrink:0;margin:0}.statement-counts dt{font-size:10px;color:#7d8d6e;white-space:nowrap}.statement-counts dd{font:34px Georgia,'SimSun',serif;font-variant-numeric:tabular-nums;color:#47683e;margin:10px 0 0}.statement-rejected-count dd{color:#9d7952}.statement-picker{display:flex;align-items:center;gap:16px;padding:17px 22px;margin-top:21px}.statement-picker label{white-space:nowrap;font-size:11px;font-weight:600;color:#637755}.statement-picker select{min-width:0;flex:1}.statement-picker .button{white-space:nowrap}.statement-note{font-size:11px;line-height:1.9;color:#7c8b70;margin:14px 0}.statement-api-notice{font-size:10px;line-height:1.8;color:#829077;overflow-wrap:anywhere;margin-bottom:18px}.statement-index{min-width:0;overflow:hidden;margin-top:20px}.statement-index-heading{display:flex;justify-content:space-between;align-items:center;padding:23px 24px 13px;gap:16px}.statement-index-heading h3{font-family:'Noto Serif SC','SimSun',serif;font-size:18px;font-weight:500;margin:8px 0 0;color:#486440}.statement-scope{display:flex;flex-wrap:wrap;align-items:start;gap:8px 20px;padding:0 24px 18px;border-bottom:1px solid var(--statement-line);font-size:9px;color:#849277}.statement-scope code{overflow-wrap:anywhere;white-space:normal}.statement-scope details{margin:0;font-size:9px}.statement-scope summary{padding:0}.statement-scope details code{display:block;max-width:500px;margin-top:6px}.statement-filters{display:grid;grid-template-columns:minmax(0,1fr) 150px auto;gap:15px;align-items:end;padding:20px 24px 16px}.statement-filters label{font-size:10px;color:#74856a}.statement-filters select{margin-top:7px;font-size:11px}.statement-search-field{display:flex;align-items:center;gap:5px;border:1px solid #dce4d1;background:#fffefa;border-radius:5px;padding-left:10px;margin-top:7px}.statement-search-field input{border:0;background:transparent;min-width:0;font-size:11px}.statement-filters .button{height:34px;font-size:11px;padding:8px 16px}.statement-list-summary{display:flex;justify-content:space-between;gap:15px;padding:0 24px 16px;color:#87947c;font-size:10px;line-height:1.7}.statement-report{border-top:1px solid var(--statement-line);padding:23px 24px}.statement-report.rejected{background:#fcf9f2}.statement-report header{display:flex;justify-content:space-between;align-items:center;gap:15px;margin-bottom:15px}.statement-report header>div{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}.statement-company-code{font:10px Consolas,monospace;color:#8b997d}.statement-report h4{font-size:14px;color:#48653c;font-weight:600;margin:0}.statement-report-year{font-size:10px;color:#87967a}.statement-observations{overflow-x:auto}.statement-observations table{min-width:570px}.statement-observations th,.statement-observations td{padding:12px 14px;white-space:nowrap}.statement-observations thead th{font-size:9px}.statement-observations th:first-child{padding-left:12px}.statement-observations tbody th{background:transparent;font-size:11px;font-weight:500;color:#657b56;text-align:left;border-top:0;border-bottom:1px solid #edf0e6}.statement-observations th small{display:block;font:9px Consolas,'Microsoft YaHei',sans-serif;color:#94a185;margin-top:5px}.statement-observations thead th:nth-child(2),.statement-observations thead th:nth-child(3){text-align:right}.statement-amount{text-align:right;font:12px Consolas,monospace!important;color:#4c6740;font-variant-numeric:tabular-nums;letter-spacing:.1px}.statement-amount.previous{color:#819474}.statement-observations .text-button{display:inline-flex;align-items:center;gap:6px;font-size:10px}.statement-report footer{display:flex;justify-content:space-between;align-items:start;gap:16px;margin-top:14px}.statement-balance{display:flex;flex-wrap:wrap;align-items:center;gap:5px 12px;font-size:9px;color:#829078}.statement-balance code{color:#6e835c}.statement-balance small{width:100%;font-size:9px;color:#9ba88f}.statement-report-source{display:flex;align-items:start;flex-wrap:wrap;justify-content:flex-end;gap:8px 12px;font-size:9px;color:#99a58e}.statement-report-source .text-button{display:inline-flex;align-items:center;gap:5px;font-size:9px;white-space:nowrap}.statement-report-source details{margin:0;font-size:9px}.statement-report-source summary{padding:0;white-space:nowrap}.statement-report-source code{display:block;max-width:260px;overflow-wrap:anywhere;margin-top:7px}.statement-rejection{display:flex;align-items:start;gap:9px;font-size:11px;line-height:1.9;color:#9b7959;padding:8px 0;white-space:normal;overflow-wrap:anywhere}.statement-rejection svg{flex-shrink:0;margin-top:3px}.statement-pagination{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:17px 24px;border-top:1px solid var(--statement-line);font:10px Consolas,monospace;color:#849376}.statement-pagination .button{font-size:10px;padding:7px 14px;min-height:31px}.statement-empty{padding:40px 25px;text-align:center;font-size:12px;color:#8b9c7a;line-height:1.9}.statement-empty>svg{margin:0 auto 14px}.statement-empty h3{font-size:16px;font-weight:500;color:#6d8456;margin:5px 0 10px}.statement-empty p{max-width:570px;margin:auto;font-size:11px}.statement-loading{font-size:12px;line-height:1.9;color:#7b8d6b;padding:32px 24px;text-align:center}.statement-alert,.statement-list-error{display:flex;flex-wrap:wrap;align-items:center;gap:10px;line-height:1.8;font-size:11px;overflow-wrap:anywhere}.statement-alert{margin-top:18px}.statement-list-error{margin:0 24px 20px}.statement-alert .text-button,.statement-list-error .text-button{margin-left:auto;font-size:11px}.statement-drawer-heading{display:flex;align-items:center;justify-content:space-between;width:100%;gap:25px}.statement-drawer-heading>div{display:grid;gap:7px}.statement-drawer-heading strong{font-size:15px;font-weight:500;color:#415c39}.statement-drawer-heading .text-button{display:inline-flex;align-items:center;gap:5px;font-size:11px;white-space:nowrap}.statement-evidence-detail{padding-top:4px;min-width:0}.statement-detail-title{display:flex;justify-content:space-between;align-items:start;gap:16px;border-bottom:1px solid #e2e8d8;margin-bottom:21px;padding:0 0 18px}.statement-detail-title h3{font:20px 'Noto Serif SC','SimSun',serif;font-weight:500;color:#43633a;line-height:1.6;margin:0}.statement-detail-title p{font-size:11px;color:#829472;margin:8px 0 0}.statement-detail-grid{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(240px,1fr);gap:24px;align-items:start}.statement-detail-document,.statement-detail-record{min-width:0}.statement-value-pair{display:grid;gap:14px;padding:18px;margin:0;background:#f2f5e9;border:1px solid #e0e7d5;border-radius:5px}.statement-value-pair dt{font-size:10px;color:#839175;line-height:1.7}.statement-value-pair dd{font:17px Consolas,monospace;color:#577047;margin:5px 0 0;overflow-wrap:anywhere}.statement-value-pair>div:last-child{border-top:1px solid #dfe6d0;padding-top:12px}.statement-value-pair>div:last-child dd{font-size:13px}.statement-detail-record h4{font-size:12px;font-weight:600;color:#637c50;margin:23px 0 10px}.statement-locator{display:grid;grid-template-columns:116px minmax(0,1fr);margin:0;border:1px solid #e5e9db;border-radius:4px;overflow:hidden;font-size:10px;line-height:1.8}.statement-locator dt,.statement-locator dd{padding:7px 10px;border-bottom:1px solid #e9edde;overflow-wrap:anywhere;white-space:pre-wrap}.statement-locator dt{background:#f8f9f2;color:#8a9b79}.statement-locator dd{margin:0;color:#667f53}.statement-locator dt:last-of-type,.statement-locator dd:last-of-type{border-bottom:0}.statement-detail-record .statement-note{font-size:10px}.statement-detail-record details{font-size:11px;border-top:1px solid #e8eddc;padding-top:7px;margin-top:12px}.statement-detail-record pre{max-height:260px;overflow:auto;padding:12px;background:#f7f9f2;border-radius:4px;font-size:10px;line-height:1.8}.statement-detail-lines>div{display:flex;align-items:start;gap:10px;border-top:1px solid #edf0e4;padding:9px 0}.statement-detail-lines>div>span{white-space:nowrap;font:9px Consolas,monospace;color:#9ba98b;margin-top:3px}.statement-detail-lines p{font-size:10px;line-height:1.9;color:#738b61;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}.statement-evidence-id{font:9px Consolas,monospace;color:#a0ad90;overflow-wrap:anywhere;line-height:1.7;margin:17px 0 10px}.statement-detail-source{display:inline-flex;align-items:center;gap:5px;font-size:11px}.statement-visually-hidden{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}button:focus-visible,a:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid #246454;outline-offset:3px}@media(max-width:1100px){.statement-banner{padding:24px;gap:22px}.statement-counts{gap:17px}.statement-counts dd{font-size:29px}.statement-banner h2{font-size:26px}.statement-detail-grid{gap:18px;grid-template-columns:minmax(0,1.15fr) minmax(250px,1fr)}}@media(max-width:760px){.statement-banner{flex-direction:column;align-items:stretch;gap:23px;padding:22px}.statement-banner h2{font-size:25px}.statement-banner p{font-size:11px}.statement-counts{border-top:1px solid #d4dfc9;padding-top:18px;gap:14px}.statement-counts dd{font-size:30px}.statement-boundary{font-size:9px;line-height:1.8}.statement-picker{flex-wrap:wrap;gap:10px;padding:16px}.statement-picker label{width:100%}.statement-picker select{font-size:11px}.statement-picker .button{font-size:10px;padding:8px 10px}.statement-note{font-size:10px}.statement-index-heading{padding:19px 16px 12px}.statement-scope{padding:0 16px 16px;gap:7px}.statement-scope code{width:100%;font-size:8px}.statement-filters{grid-template-columns:minmax(0,1fr) 125px;gap:12px;padding:17px 16px 13px}.statement-filters>label:first-child{grid-column:1/-1}.statement-filters .button{justify-self:end;width:100%}.statement-list-summary{padding:0 16px 14px;flex-wrap:wrap;gap:5px;font-size:9px}.statement-report{padding:19px 16px}.statement-report header>div{gap:5px 8px}.statement-report h4{font-size:13px}.statement-report .badge{font-size:8px;white-space:nowrap}.statement-report footer{flex-direction:column;gap:12px}.statement-report-source{justify-content:flex-start}.statement-pagination{padding:15px 16px}.statement-detail-title h3{font-size:18px}.statement-detail-grid{grid-template-columns:1fr;gap:22px}.statement-detail-record{border-top:1px solid #e1e8d6;padding-top:20px}.statement-value-pair{grid-template-columns:1fr 1fr;gap:15px}.statement-value-pair dd{font-size:14px}.statement-value-pair>div:last-child{grid-column:1/-1}.statement-list-error{margin:0 16px 18px}.statement-evidence-detail{max-width:100%}}
</style>
