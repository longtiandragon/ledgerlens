<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NDrawer, NDrawerContent } from 'naive-ui'
import { api, download, post } from '../api'
import Icon from './Icon.vue'

interface Dataset {dataset_scope_id:string;snapshot_id:string;title:string;trace_count:number;event_count:number;observed_at:string;source_license:string}
interface Trace {source_trace_id:string;trace_alias:string;event_count:number;classification:string;policy_status:string;rule_conclusion:Status}
type Status='compatible'|'needs_review'|'insufficient'|'out_of_scope'
interface RuleResult {rule_id:string;title:string;status:Status;reason:string;evidence_ids:string[];cited_event_total:number;cited_events_truncated:boolean;observed:Record<string,unknown>}
interface Review {conclusion:Status;results:RuleResult[];unsupported_checks:string[];rule_pack:{version:string;source_sha256:string};trace_evidence_id:string}
interface Statement {text:string;evidence_ids:string[];rule_id:string}
interface ModelRun {id:string;status:string;created_at:string;completed_at?:string;result?:{proposal:{conclusion:Status;explanations:Statement[];counter_evidence:Statement[];missing_information:string[]}|null;usage?:Record<string,number>;error?:{code:string;message:string}}|null;error?:{code:string}}
interface Summary {trace_count:number;conclusion_counts:Record<Status,number>;rule_pack:{version:string}}
interface Event {evidence_id:string;source_event_ordinal:number;activity_raw:string;phase:string;occurred_at_raw:string|null;source_numeric_text:string|null;source_numeric_status:string;causal_order_status:string;quality_issues:string[]}
interface Page<T> {items:T[];total:number;offset:number;limit:number;next_offset:number|null}
interface Detail {trace:{trace_alias:string;source_trace_id:string;policy:{classification:string;status:string;issues:string[]};observations:Record<string,unknown>;trace_attributes:Record<string,string>};events:Page<Event>}
const datasets=ref<Dataset[]>([]),snapshotId=ref(''),traces=ref<Page<Trace>>(),detail=ref<Detail>(),focused=ref<Record<string,unknown>>()
const review=ref<Review>(),summary=ref<Summary>(),conclusion=ref<Status|''>(''),exporting=ref(false)
const runs=ref<ModelRun[]>([]),confirmingRun=ref(false),starting=ref(false)
let runGeneration=0,pollTimer:ReturnType<typeof setTimeout>|undefined
const search=ref(''),error=ref(''),loading=ref(false),traceLoading=ref(false),listLoading=ref(false),evidenceLoading=ref(false)
const dataset=computed(()=>datasets.value.find(item=>item.snapshot_id===snapshotId.value))
const scope=computed(()=>dataset.value?`/process-datasets/${encodeURIComponent(dataset.value.dataset_scope_id)}/snapshots/${encodeURIComponent(dataset.value.snapshot_id)}`:'')
let scopeGeneration=0,listGeneration=0,detailGeneration=0,evidenceGeneration=0
const labels:Record<string,string>={three_way_invoice_before_gr_allowed:'三方匹配 · 允许先收票',three_way_gr_based_invoice_verification:'三方匹配 · 基于收货验票',two_way_no_goods_receipt_required:'两方匹配',consignment_invoice_outside_item_process:'寄售流程',needs_review:'待核实',classified:'已识别来源策略',unsupported:'未支持'}
const statusText:Record<Status,string>={compatible:'源策略相容',needs_review:'待核实过程差异',insufficient:'证据不足',out_of_scope:'范围外 / 不适用'}
const statusTone:Record<Status,string>={compatible:'success',needs_review:'danger',insufficient:'warning',out_of_scope:'neutral'}
const checkText:Record<string,string>={goods_receipt_to_invoice_value_matching:'收货与发票金额匹配',stage_value_summation_and_rounding_tolerance:'阶段金额汇总与舍入容差',per_batch_receipt_invoice_clearing_pairing:'逐批收货／收票／清账配对',cancellation_target_identification_and_netting:'取消对象识别与冲销',payment_block_state_reconstruction:'付款冻结状态重建',clearing_as_bank_payment:'清账等同银行付款',duplicate_posting_diagnosis:'重复记账诊断',anomaly_root_cause:'异常根因',business_completion:'业务完成判断'}
const show=(value:unknown)=>JSON.stringify(value,null,2)
const message=(cause:unknown)=>cause instanceof Error?cause.message:'流程资料读取失败'
function stopPolling(){runGeneration++;if(pollTimer)clearTimeout(pollTimer);pollTimer=undefined}
function resetDetail(){stopPolling();runs.value=[];confirmingRun.value=false;detailGeneration++;evidenceGeneration++;detail.value=undefined;review.value=undefined;focused.value=undefined;traceLoading.value=false;evidenceLoading.value=false}
function closeEvidence(){evidenceGeneration++;focused.value=undefined;evidenceLoading.value=false}
async function load(){
 const generation=++scopeGeneration;loading.value=true;error.value='';resetDetail();traces.value=undefined
 try{const result=await api<{snapshots:Dataset[]}>('/process-datasets');if(generation!==scopeGeneration)return;datasets.value=result.snapshots;snapshotId.value=result.snapshots[0]?.snapshot_id||'';if(snapshotId.value){void loadSummary();await loadTraces()}}
 catch(cause){if(generation===scopeGeneration)error.value=message(cause)}finally{if(generation===scopeGeneration)loading.value=false}
}
async function loadSummary(){
 const generation=scopeGeneration,requestedScope=scope.value;summary.value=undefined;if(!requestedScope)return
 try{const result=await api<Summary>(`${requestedScope}/rule-summary`);if(generation===scopeGeneration&&requestedScope===scope.value)summary.value=result}
 catch(cause){if(generation===scopeGeneration)error.value=message(cause)}
}
async function changeSnapshot(){scopeGeneration++;listGeneration++;resetDetail();traces.value=undefined;summary.value=undefined;search.value='';conclusion.value='';void loadSummary();error.value='';await loadTraces()}
async function loadTraces(offset=0){
 if(!scope.value)return
 const generation=++listGeneration,requestedScope=scope.value;listLoading.value=true;error.value=''
 try{const result=await api<Page<Trace>>(`${requestedScope}/traces?${new URLSearchParams({q:search.value,offset:String(offset),limit:'30',...(conclusion.value?{conclusion:conclusion.value}:{})})}`);if(generation!==listGeneration||requestedScope!==scope.value)return;traces.value=result;if(result.items[0])await loadTrace(result.items[0].source_trace_id);else resetDetail()}
 catch(cause){if(generation===listGeneration)error.value=message(cause)}finally{if(generation===listGeneration)listLoading.value=false}
}
async function loadTrace(sourceTraceId:string,offset=0){
 const generation=++detailGeneration,requestedScope=scope.value;evidenceGeneration++;focused.value=undefined;detail.value=undefined;review.value=undefined;traceLoading.value=true;error.value=''
 try{const [result,rules]=await Promise.all([api<Detail>(`${requestedScope}/trace?${new URLSearchParams({source_trace_id:sourceTraceId,offset:String(offset),limit:'50'})}`),api<Review>(`${requestedScope}/trace-review?${new URLSearchParams({source_trace_id:sourceTraceId})}`)]);if(generation===detailGeneration&&requestedScope===scope.value){detail.value=result;review.value=rules;void loadRuns()}}
 catch(cause){if(generation===detailGeneration)error.value=message(cause)}finally{if(generation===detailGeneration)traceLoading.value=false}
}
async function inspect(event:Event|string){
 const generation=++evidenceGeneration,requestedScope=scope.value,evidenceId=typeof event==='string'?event:event.evidence_id;evidenceLoading.value=true;focused.value=undefined;error.value=''
 try{const result=await api<Record<string,unknown>>(`${requestedScope}/evidence/${encodeURIComponent(evidenceId)}`);if(generation===evidenceGeneration&&requestedScope===scope.value)focused.value=result}
 catch(cause){if(generation===evidenceGeneration)error.value=message(cause)}finally{if(generation===evidenceGeneration)evidenceLoading.value=false}
}
async function exportPaper(format:'json'|'markdown'){
 if(!detail.value||!scope.value)return
 const trace=detail.value.trace;exporting.value=true;error.value=''
 try{await download(`${scope.value}/trace-review/export?${new URLSearchParams({source_trace_id:trace.source_trace_id,format})}`,`process-review-${trace.trace_alias}.${format==='json'?'json':'md'}`)}
 catch(cause){error.value=message(cause)}finally{exporting.value=false}
}
async function loadRuns(){
 if(!detail.value||!scope.value)return
 const generation=++runGeneration,requestedScope=scope.value,traceId=detail.value.trace.source_trace_id
 try{const result=await api<{runs:ModelRun[]}>(`${requestedScope}/trace-investigations?${new URLSearchParams({source_trace_id:traceId})}`);if(generation!==runGeneration||requestedScope!==scope.value)return;runs.value=result.runs;if(result.runs.some(run=>run.status==='running'))pollTimer=setTimeout(()=>void loadRuns(),3000)}
 catch(cause){if(generation===runGeneration)error.value=message(cause)}
}
async function startRun(){
 if(!detail.value||!scope.value)return
 if(!confirmingRun.value){confirmingRun.value=true;return}
 confirmingRun.value=false;starting.value=true;error.value=''
 try{await post(`${scope.value}/trace-investigations?${new URLSearchParams({source_trace_id:detail.value.trace.source_trace_id})}`);await loadRuns()}
 catch(cause){error.value=message(cause)}finally{starting.value=false}
}
const runStatus:Record<string,string>={running:'运行中',complete:'已完成',failed:'失败',budget_exhausted:'预算耗尽',interrupted:'已中断'}
onMounted(load)
onBeforeUnmount(()=>{stopPolling();scopeGeneration++;listGeneration++;detailGeneration++;evidenceGeneration++})
</script>

<template>
 <div class="process-evidence">
  <div v-if="error" class="notice error" role="alert">{{ error }}</div>
  <section class="process-banner">
   <div><span class="process-kicker">BPI CHALLENGE 2019 · 采购流程日志</span><h2>沿着真实事件，<br/>查看业务如何发生。</h2><p>按采购行项目读取收货、收票、清账与变更记录。来源字段、原始时间与匿名数值均可回查。</p><span class="process-boundary">独立流程数据集 · 未关联会计账套</span></div>
   <dl v-if="dataset" class="process-counts"><div><dt>完整日志片段</dt><dd>{{ dataset.trace_count.toLocaleString() }}</dd></div><div><dt>可定位事件</dt><dd>{{ dataset.event_count.toLocaleString() }}</dd></div><div class="source-license"><dt>来源许可</dt><dd>{{ dataset.source_license||'CC BY 4.0' }}</dd></div></dl>
  </section>
  <section class="panel process-picker"><label for="process-snapshot">已导入的数据快照</label><select id="process-snapshot" v-model="snapshotId" :disabled="loading||!datasets.length" @change="changeSnapshot"><option v-if="!datasets.length" value="">{{ loading?'正在校验来源…':'尚无已导入快照' }}</option><option v-for="item in datasets" :key="item.snapshot_id" :value="item.snapshot_id">{{ item.title }} · {{ item.trace_count }} 个采购项</option></select><button class="button" :disabled="loading" @click="load"><Icon name="refresh" :size="15"/>刷新</button></section>
  <p class="process-note">完整日志片段表示读取了该片段的全部记录，不代表业务已完成。重复活动可能属于分批履行；清账记录不能单独证明银行付款。</p>
  <section v-if="summary&&dataset" class="rule-summary" aria-label="流程观察结论分布"><div><span class="process-kicker">PROCESS RULES · {{ summary.rule_pack.version }}</span><p>按数据集公开流程说明逐项观察，结果是计数，不是准确率或异常率。</p></div><dl><div v-for="(text,key) in statusText" :key="key"><dt>{{ text }}</dt><dd>{{ summary.conclusion_counts[key].toLocaleString() }}</dd></div></dl></section>
  <div v-if="loading" class="small-empty" role="status">正在校验并加载流程证据…</div>
  <section v-else-if="!dataset" class="panel process-empty"><h3>先导入已校验的流程数据</h3><p>本页读取本机采购事件快照。完成 BPI 数据适配后点击刷新，即可查看事件及其来源定位。</p></section>
  <div v-else class="process-grid">
   <section class="panel process-index" aria-label="采购项列表">
    <form class="process-search" @submit.prevent="loadTraces()"><label for="process-search">查找采购项</label><div><input id="process-search" v-model="search" placeholder="来源编号或匿名别名" maxlength="200"/><button class="button" :disabled="listLoading">查找</button></div><label for="process-conclusion" class="process-filter-label">流程观察结论</label><select id="process-conclusion" v-model="conclusion" class="process-filter" :disabled="listLoading" @change="loadTraces()"><option value="">全部</option><option v-for="(text,key) in statusText" :key="key" :value="key">{{ text }}</option></select></form>
    <p class="process-list-count">{{ traces?.total??0 }} 个匹配采购项 <span v-if="listLoading">· 读取中</span></p>
    <button v-for="item in traces?.items||[]" :key="item.source_trace_id" type="button" :class="['process-item',{selected:detail?.trace.source_trace_id===item.source_trace_id}]" @click="loadTrace(item.source_trace_id)"><strong>{{ item.trace_alias }}</strong><span>{{ labels[item.classification]||item.classification }}</span><small>{{ item.event_count }} 个事件 · {{ labels[item.policy_status]||item.policy_status }}</small><em :class="['rule-chip',item.rule_conclusion]">{{ statusText[item.rule_conclusion] }}</em></button>
    <p v-if="traces&&!traces.total" class="small-empty">没有匹配的采购项。</p>
    <div v-if="traces" class="process-pagination"><button class="button" :disabled="listLoading||traces.offset===0" @click="loadTraces(Math.max(0,traces.offset-traces.limit))">上一页</button><span>{{ traces.offset+Math.min(1,traces.total) }}–{{ Math.min(traces.offset+traces.limit,traces.total) }}</span><button class="button" :disabled="listLoading||traces.next_offset===null" @click="loadTraces(traces.next_offset!)">下一页</button></div>
   </section>
   <section class="panel process-detail" aria-label="采购事件时间线">
    <div v-if="traceLoading" class="small-empty" role="status">正在读取采购事件…</div>
    <template v-else-if="detail">
     <div class="process-detail-heading"><div><span class="process-kicker">PURCHASE ITEM</span><h3>{{ detail.trace.trace_alias }}</h3><p>{{ labels[detail.trace.policy.classification]||detail.trace.policy.classification }}</p></div><span class="badge neutral">{{ detail.events.total }} 个事件</span></div>
     <p v-if="detail.trace.policy.status!=='classified'" class="notice warning">来源策略需要核实：{{ detail.trace.policy.issues.join(' / ') }}</p>
     <section v-if="review" class="rule-review" aria-label="流程规则观察">
      <header><div><span class="process-kicker">流程规则观察</span><h4>{{ statusText[review.conclusion] }}</h4></div><span :class="['badge',statusTone[review.conclusion]]">BPI2019_PROCESS · 实验性</span></header>
      <ul><li v-for="item in review.results" :key="item.rule_id" :class="item.status"><div class="rule-line"><strong>{{ item.title }}</strong><span :class="['badge',statusTone[item.status]]">{{ statusText[item.status] }}</span></div><p>{{ item.reason }}</p><div class="rule-refs"><button v-for="(ref,index) in item.evidence_ids" :key="ref" type="button" class="text-button" :aria-label="`定位规则引用 ${index===0?'采购项':'事件'+index}`" @click="inspect(ref)">{{ index===0?'采购项':'事件 '+index }}</button><small v-if="item.cited_events_truncated">共 {{ item.cited_event_total }} 个事件，仅列前 {{ item.evidence_ids.length-1 }} 个</small></div></li></ul>
      <p class="process-note">未实现：<span v-for="(name,index) in review.unsupported_checks" :key="name" :title="name">{{ checkText[name]||name }}{{ index<review.unsupported_checks.length-1?'、':'' }}</span>。流程观察不是会计准则判断，未生成分录。</p>
      <div class="rule-export"><button class="button" :disabled="exporting" @click="exportPaper('markdown')"><Icon name="download" :size="14"/>导出 Markdown 底稿</button><button class="text-button" :disabled="exporting" @click="exportPaper('json')">导出 JSON</button></div>
      <section class="model-runs" aria-label="模型解释">
       <div class="model-runs-head"><div><span class="process-kicker">模型解释 · 未经核验的意见</span><p>由模型解释上述程序观察并寻找正常流程解释；程序观察仍是事实依据。</p></div><button class="button" :disabled="starting||runs.some(run=>run.status==='running')" @click="startRun">{{ confirmingRun?'确认调用付费模型':'启动模型解释' }}</button></div>
       <p v-if="confirmingRun" class="notice warning" role="status">将调用付费模型服务（单次约数分钱），结果不会改变程序观察。再次点击确认启动。<button class="text-button" @click="confirmingRun=false">取消</button></p>
       <article v-for="run in runs" :key="run.id" class="model-run"><header><span :class="['badge',run.status==='complete'?'neutral':run.status==='running'?'warning':'danger']">{{ runStatus[run.status]||run.status }}</span><small>{{ run.created_at }}</small></header>
        <template v-if="run.result?.proposal"><p class="model-conclusion">模型结论：{{ statusText[run.result.proposal.conclusion] }}</p>
         <div v-for="(item,index) in [...run.result.proposal.explanations.map(x=>({...x,kind:'解释'})),...run.result.proposal.counter_evidence.map(x=>({...x,kind:'反证'}))]" :key="index" class="model-statement"><strong>{{ item.kind }}</strong><p>{{ item.text }}</p><div class="rule-refs"><button v-for="ref in item.evidence_ids" :key="ref" type="button" class="text-button" :aria-label="`定位模型引用 ${ref}`" @click="inspect(ref)">定位引用</button></div></div>
         <p v-if="run.result.proposal.missing_information.length" class="process-note">缺少的信息：{{ run.result.proposal.missing_information.join('；') }}</p></template>
        <p v-else-if="run.status!=='running'" class="process-note">未形成模型意见：{{ run.result?.error?.message||run.error?.code||'运行未完成' }}</p>
       </article>
      </section>
     </section>
     <div class="process-table"><table><thead><tr><th>序号 / UTC 时间</th><th>来源活动</th><th>原数值</th><th>证据</th></tr></thead><tbody><tr v-for="event in detail.events.items" :key="event.evidence_id"><td><span class="event-ordinal">{{ String(event.source_event_ordinal).padStart(2,'0') }}</span><time>{{ event.occurred_at_raw||'时间未提供' }}</time><small v-if="event.causal_order_status==='same_timestamp_undetermined'" class="time-tie">同刻事件 · 因果先后未定</small></td><td><strong>{{ event.activity_raw||'活动未提供' }}</strong><small v-if="event.quality_issues.length" class="time-tie">{{ event.quality_issues.join(' / ') }}</small></td><td class="raw-number">{{ event.source_numeric_text??'未提供' }}<small v-if="event.source_numeric_status!=='valid'">{{ event.source_numeric_status==='missing'?'缺失':'待核实' }}</small></td><td><button class="text-button" @click="inspect(event)">定位 <Icon name="arrow" :size="14"/></button></td></tr></tbody></table></div>
     <div class="process-pagination"><span>事件 {{ detail.events.offset+1 }}–{{ Math.min(detail.events.offset+detail.events.limit,detail.events.total) }} / {{ detail.events.total }}</span><div><button class="button" :disabled="detail.events.offset===0" @click="loadTrace(detail.trace.source_trace_id,Math.max(0,detail.events.offset-detail.events.limit))">上一页</button><button class="button" :disabled="detail.events.next_offset===null" @click="loadTrace(detail.trace.source_trace_id,detail.events.next_offset!)">下一页</button></div></div>
     <p class="process-note">数值保留来源字面量；字段标签为 EUR，属于匿名事件价值。各阶段可能重复记录同一价值，本页不跨阶段求和。</p>
     <details class="process-observations"><summary>查看来源策略与过程观察</summary><h4>原始采购项属性</h4><pre>{{ show(detail.trace.trace_attributes) }}</pre><h4>程序观察</h4><pre>{{ show(detail.trace.observations) }}</pre></details>
    </template>
    <div v-else class="small-empty">选择一个采购项查看事件时间线。</div>
   </section>
  </div>
  <NDrawer :show="evidenceLoading||!!focused" width="min(480px, 100vw)" placement="right" @update:show="value=>{if(!value)closeEvidence()}"><NDrawerContent><template #header><div class="process-source-heading"><span>事件来源定位</span><button class="text-button" aria-label="关闭证据" @click="closeEvidence">关闭</button></div></template><section class="process-source" aria-label="流程证据定位"><p v-if="evidenceLoading" role="status">正在核对证据…</p><template v-else-if="focused"><p class="process-note">定位绑定当前数据集快照。JSONL 行号、事件序号和字段指针对应导入来源。</p><h4>来源位置</h4><pre>{{ show(focused.locator) }}</pre><h4>原始事件字段</h4><pre>{{ show(focused.event_attributes??focused.trace_attributes) }}</pre><p class="evidence-identity">{{ focused.evidence_id }}</p></template></section></NDrawerContent></NDrawer>
 </div>
</template>

<style scoped>
.rule-summary{display:flex;justify-content:space-between;gap:24px;align-items:center;padding:16px 22px;margin:0 0 18px;border:1px solid var(--process-border);border-radius:6px;background:#fbfcf9}.rule-summary p{font-size:12px;color:#6b756f;margin:6px 0 0}.rule-summary dl{display:grid;grid-template-columns:repeat(4,minmax(70px,1fr));gap:14px;margin:0}.rule-summary dt{font-size:10px;color:#6b756f}.rule-summary dd{margin:4px 0 0;font:22px Georgia,serif;font-variant-numeric:tabular-nums}.process-filter-label{margin-top:12px}.process-filter{width:100%;padding:7px;border:1px solid var(--process-border);border-radius:4px;background:white;font-size:12px}.rule-chip{display:inline-block;font-style:normal;font-size:10px;margin-top:7px;padding:2px 6px;border-radius:3px;background:#eef1ea;color:#566c5d}.rule-chip.needs_review{background:#f7e6e1;color:#8a3b2a}.rule-chip.insufficient{background:#f6eedb;color:#7a5a1d}.rule-review{border:1px solid var(--process-border);border-radius:6px;padding:16px 18px;margin:0 0 18px}.rule-review header{display:flex;justify-content:space-between;align-items:center;gap:12px}.rule-review h4{margin:6px 0 0;font-size:15px}.rule-review ul{list-style:none;padding:0;margin:12px 0 0}.rule-review li{border-top:1px solid #edf0eb;padding:11px 0}.rule-line{display:flex;justify-content:space-between;gap:12px;align-items:center}.rule-line strong{font-size:12px;font-weight:600}.rule-review li p{font-size:12px;line-height:1.8;color:#4f5f53;margin:6px 0}.rule-refs{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center}.rule-refs .text-button{font-size:11px;padding:2px 0}.rule-refs small{font-size:10px;color:#7d877b}.rule-export{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.model-runs{border-top:1px solid #edf0eb;margin-top:14px;padding-top:12px}.model-runs-head{display:flex;justify-content:space-between;gap:12px;align-items:center}.model-runs-head p{font-size:12px;color:#6b756f;margin:6px 0 0}.model-run{border:1px solid var(--process-border);border-radius:4px;padding:10px 12px;margin-top:10px}.model-run header{display:flex;gap:10px;align-items:center}.model-run small{font-size:10px;color:#7d877b}.model-conclusion{font-size:13px;font-weight:600;margin:8px 0}.model-statement{border-left:3px solid #c9d6c3;padding:2px 10px;margin:8px 0}.model-statement strong{font-size:11px}.model-statement p{font-size:12px;line-height:1.8;margin:4px 0}@media(max-width:760px){.model-runs-head{flex-direction:column;align-items:flex-start}}@media(max-width:760px){.rule-summary{flex-direction:column;align-items:flex-start}.rule-summary dl{grid-template-columns:repeat(2,minmax(0,1fr));width:100%}.rule-line{flex-direction:column;align-items:flex-start;gap:6px}}
.process-source-heading{display:flex;align-items:center;justify-content:space-between;gap:24px;width:100%}
.process-counts .source-license dd{font:12px monospace;line-height:1.6}
.process-evidence{--process-green:#235d4d;--process-border:#dfe6df}.process-banner{display:flex;justify-content:space-between;gap:32px;padding:30px 36px;background:#eaf0e7;border-left:4px solid var(--process-green);border-radius:6px;color:#244235}.process-kicker{font-size:10px;font-weight:700;letter-spacing:1.6px}.process-banner h2{font-size:29px;line-height:1.45;margin:12px 0}.process-banner p{font-size:13px;line-height:1.9;max-width:570px;color:#566c5d}.process-boundary{display:inline-block;font-size:11px;border-top:1px solid #c7d4c5;padding-top:12px;margin-top:8px}.process-counts{min-width:200px;display:grid;grid-template-columns:1fr 1fr;gap:26px;align-content:center;margin:0}.process-counts dt{font-size:11px;color:#647766}.process-counts dd{font-family:Georgia,serif;font-size:34px;margin:9px 0 0;font-variant-numeric:tabular-nums}.process-counts .source-license{grid-column:1/-1}.source-license dd{font:12px monospace}.process-picker{display:flex;align-items:center;gap:18px;padding:18px 22px;margin-top:20px}.process-picker label{white-space:nowrap;font-size:12px;font-weight:700}.process-picker select{flex:1;min-width:0;max-width:600px;padding:9px;border:1px solid var(--process-border);border-radius:4px;background:white;color:#244235}.process-note{font-size:12px;line-height:1.9;color:#6b756f;margin:16px 0}.process-grid{display:grid;grid-template-columns:260px minmax(0,1fr);gap:20px;align-items:start}.process-index{overflow:hidden;padding:0}.process-search{padding:18px}.process-search label{display:block;font-size:12px;font-weight:700;margin-bottom:10px}.process-search>div{display:flex;gap:6px}.process-search input{width:100%;min-width:0;border:1px solid var(--process-border);border-radius:4px;padding:8px;font-size:12px}.process-search .button{padding:7px 10px;white-space:nowrap}.process-list-count{font-size:11px;color:#758377;margin:0;padding:0 18px 12px;border-bottom:1px solid var(--process-border)}.process-item{display:block;text-align:left;width:100%;background:white;padding:16px 18px;border:0;border-bottom:1px solid #edf0eb;cursor:pointer;color:#314d3d}.process-item:hover,.process-item.selected{background:#eef3ec}.process-item.selected{box-shadow:inset 3px 0 var(--process-green)}.process-item strong{display:block;font:11px monospace;overflow-wrap:anywhere}.process-item span{display:block;font-size:12px;margin-top:7px;line-height:1.6}.process-item small{display:block;font-size:10px;color:#7d877b;margin-top:7px}.process-detail{padding:24px;min-width:0}.process-detail-heading{display:flex;justify-content:space-between;gap:15px;align-items:center}.process-detail-heading h3{font:14px monospace;overflow-wrap:anywhere;margin:10px 0}.process-detail-heading p{font-size:12px;color:#6a7d6c;margin:5px 0 20px}.process-table{overflow:auto}.process-table table{width:100%;border-collapse:collapse;min-width:610px}.process-table th{text-align:left;font-size:10px;color:#758073;padding:10px 8px;background:#f5f7f2;font-weight:600}.process-table td{padding:15px 8px;border-bottom:1px solid #e9ede6;font-size:12px;vertical-align:top}.process-table time{display:block;font:10px monospace;white-space:nowrap;color:#71826f;margin-top:6px}.event-ordinal{font-family:Georgia,serif;font-size:17px;color:#2f694e}.process-table td strong{display:block;max-width:210px;font-size:12px;font-weight:500;line-height:1.7}.process-table .raw-number{font:13px monospace;text-align:right;white-space:nowrap}.raw-number small,.time-tie{display:block;font:10px sans-serif;line-height:1.6;color:#906d32;margin-top:6px}.process-pagination{display:flex;justify-content:space-between;align-items:center;gap:6px;padding-top:17px;font-size:10px;color:#7d877b}.process-index .process-pagination{padding:14px 12px}.process-pagination .button{font-size:10px;padding:7px 10px;min-height:30px}.process-pagination>div{display:flex;gap:8px}.process-observations{font-size:12px;border-top:1px solid var(--process-border);padding-top:16px}.process-observations summary{cursor:pointer;color:var(--process-green)}.process-source{margin-top:20px;padding:24px}.process-source h3{margin:0;font-size:16px}.process-source h4,.process-observations h4{font-size:12px;margin:20px 0 8px}pre{background:#f4f6f1;padding:15px;border-radius:4px;font-size:11px;line-height:1.8;max-height:320px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}.evidence-identity{font:10px monospace;overflow-wrap:anywhere;color:#859080}.process-empty{padding:30px}.process-empty h3{font-size:16px}.process-empty p{font-size:13px;color:#7d877b}button:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid #246454;outline-offset:3px}@media(max-width:1100px){.process-grid{grid-template-columns:220px minmax(0,1fr)}.process-banner{padding:24px;gap:20px}.process-counts{min-width:150px;gap:20px}.process-counts dd{font-size:28px}}@media(max-width:760px){.process-banner{flex-direction:column}.process-counts{grid-template-columns:1fr 1fr 1fr}.process-counts .source-license{grid-column:auto}.process-banner h2{font-size:25px}.process-grid{grid-template-columns:1fr}.process-index{max-height:380px;overflow:auto}.process-picker{flex-wrap:wrap;padding:16px;gap:10px}.process-picker label{width:100%}.process-picker select{font-size:12px}.process-detail{padding:18px}.process-note{font-size:11px}.process-source{padding:18px}}
</style>
