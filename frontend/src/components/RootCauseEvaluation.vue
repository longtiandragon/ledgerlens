<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import Icon from './Icon.vue'

interface Metrics { samples:number;failures:number;category_macro_f1:number|null;conclusion_accuracy:number|null;decisive_evidence_obtained:string }
interface Run { id:string;summary_status:'verified'|'invalid';label?:string;split?:string;role?:string;validity?:string;report?:string;report_sha256?:string;repeat?:number;paid_requests?:number;offpeak_cost_cny_estimate?:string|null;status?:string;methods?:Record<string,Metrics> }
interface Results { status:string;conclusion?:string;limitations?:string[];notice:string;runs:Run[];dataset_version?:string }
const results=ref<Results>()
const loading=ref(false)
const error=ref('')
const methodNames:Record<string,string>={rule_only:'仅规则',rule_fixed_request:'规则＋固定补证',keyword_request:'关键词补证',single:'单Agent（模型）',hybrid:'确定性优先混合（模型兜底）',active:'主动调查（模型）',multi:'固定多Agent（模型）'}
const splitNames:Record<string,string>={development:'开发集',validation:'验证集',sealed_test:'封存测试集'}
const final=computed(()=>{
  // The newest verified final run, merged with the completed final batches of the same generation (v4: hybrid + single).
  const runs=results.value?.runs.filter(run=>run.role==='final'&&run.summary_status==='verified')||[]
  const first=runs[0]
  if(!first)return undefined
  const generation=first.id.split('-')[0]
  const batches=runs.filter(run=>run.id.split('-')[0]===generation&&run.status!=='interrupted')
  return {...first,methods:Object.assign({},...batches.map(run=>run.methods||{}).reverse())}
})
const rate=(value:number|null|undefined)=>value==null?'—':`${(value*100).toFixed(1)}%`
const score=(value:number|null|undefined)=>value==null?'—':value.toFixed(3)
async function refresh(){
  if(loading.value)return
  loading.value=true;error.value=''
  try{results.value=await api<Results>('/evaluations/root-cause')}
  catch(e){error.value=e instanceof Error?e.message:'原因调查结果读取失败'}
  finally{loading.value=false}
}
onMounted(refresh)
</script>

<template>
  <section class="panel spaced root-cause-evaluation" aria-labelledby="root-cause-title">
    <div class="panel-heading">
      <div><span class="section-number">04</span><h2 id="root-cause-title">原因调查评测</h2></div>
      <button class="button" :disabled="loading" aria-label="刷新原因调查结果" @click="refresh"><Icon name="refresh" :size="14"/>{{ loading?'读取中':'刷新' }}</button>
    </div>
    <div v-if="error" class="notice error" role="alert">{{ error }}</div>
    <p v-else-if="results&&results.status!=='available'" class="notice warning" role="status">{{ results.notice }}</p>
    <template v-else-if="results">
      <p class="rc-conclusion"><strong>结论</strong>{{ results.conclusion }}</p>
      <div v-if="final?.methods" class="rc-final" aria-label="封存测试集最终对比">
        <article v-for="(item,name) in final.methods" :key="name" :class="['rc-method',{model:String(name).length&&!String(name).startsWith('rule')&&name!=='keyword_request'}]">
          <span>{{ methodNames[name]||name }}</span>
          <dl><div><dt>原因F1</dt><dd>{{ score(item.category_macro_f1) }}</dd></div><div><dt>结论准确率</dt><dd>{{ rate(item.conclusion_accuracy) }}</dd></div><div><dt>失败</dt><dd>{{ item.failures }}/{{ item.samples }}</dd></div></dl>
        </article>
      </div>
      <div class="table-wrap"><table class="rc-runs">
        <thead><tr><th>运行</th><th>划分</th><th>有效性</th><th>付费请求 / 估算费用</th><th>来源报告</th></tr></thead>
        <tbody><tr v-for="run in results.runs" :key="run.id">
          <template v-if="run.summary_status==='verified'"><td>{{ run.label }}<small>重复{{ run.repeat }}次</small></td><td>{{ splitNames[run.split||'']||run.split }}</td><td :class="{invalidated:run.validity?.startsWith('作废')}">{{ run.validity }}</td><td>{{ run.paid_requests }} 次 / {{ run.offpeak_cost_cny_estimate?`约¥${run.offpeak_cost_cny_estimate}`:'—' }}</td><td><code>{{ run.report }}</code><small>{{ run.report_sha256?.slice(0,16) }}…</small></td></template>
          <template v-else><td>{{ run.id }}</td><td colspan="4" class="invalidated">摘要条目校验失败，已隔离显示，未推断其结果。</td></template>
        </tr></tbody>
      </table></div>
      <ul class="rc-limits"><li v-for="item in results.limitations" :key="item">{{ item }}</li></ul>
      <p class="caption">{{ results.notice }} 刷新只读取历史摘要，不会调用模型。</p>
    </template>
    <p v-else class="small-empty">正在读取原因调查结果…</p>
  </section>
</template>

<style scoped>
.rc-conclusion{font-size:13px;line-height:1.9;margin:0 0 16px;padding:12px 14px;background:#fbf6ec;border-left:3px solid #b98a3a}
.rc-conclusion strong{margin-right:10px}
.rc-final{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:18px}
.rc-method{border:1px solid #e3e8df;border-radius:6px;padding:12px 14px}
.rc-method.model{border-color:#d9b98a;background:#fffaf2}
.rc-method span{font-size:12px;font-weight:600}
.rc-method dl{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:10px 0 0}
.rc-method dt{font-size:10px;color:#7a8475}.rc-method dd{margin:4px 0 0;font:18px Georgia,serif;font-variant-numeric:tabular-nums}
.rc-runs td{font-size:12px;vertical-align:top}.rc-runs small{display:block;font-size:10px;color:#7a8475;margin-top:4px}
.rc-runs code{font-size:10px;overflow-wrap:anywhere}.invalidated{color:#8a3b2a}
.rc-limits{font-size:12px;color:#5f6b5c;line-height:1.8;padding-left:18px}
@media(max-width:760px){.rc-method dl{grid-template-columns:repeat(3,minmax(0,1fr))}}
</style>
