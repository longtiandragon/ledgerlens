<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import Icon from './Icon.vue'

interface Row { method:string;answerable:number;unanswerable:number;failures:number;correct_hit_at_6?:number;returned_unanswerable?:number;threshold?:number;correct_hit_at_6_kept?:number;correct_hit_at_6_lost?:number;accepted_unanswerable?:number;rejected_unanswerable?:number;candidate_meets_gate?:boolean }
interface Summary { source:string;status:'available'|'invalid'|'unavailable';notice?:string;rows?:Row[];summary_sha256?:string;report_sha256?:string }
interface Results { baseline:Summary;abstention:Summary;notice:string }
const results=ref<Results>()
const loading=ref(false)
const error=ref('')
const names:Record<string,string>={keyword:'关键词',bge_pgvector:'BGE＋pgvector',bge_numpy:'BGE＋NumPy'}
async function refresh(){
  if(loading.value)return
  loading.value=true;error.value='';results.value=undefined
  try{results.value=await api<Results>('/evaluations/retrieval')}
  catch(e){error.value=e instanceof Error?e.message:'检索结果读取失败'}
  finally{loading.value=false}
}
onMounted(refresh)
</script>

<template>
  <section class="panel spaced retrieval-evaluation" aria-labelledby="retrieval-title">
    <div class="panel-heading">
      <div><span class="section-number">05</span><h2 id="retrieval-title">参考检索评测</h2></div>
      <button class="button" :disabled="loading" aria-label="刷新参考检索结果" @click="refresh"><Icon name="refresh" :size="14"/>{{ loading?'读取中':'刷新' }}</button>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <div v-else-if="results" class="retrieval-body">
      <p class="notice warning">当前默认仍为关键词检索。候选命中与分数阈值均不能证明条文适用于当前业务，引用须人工核实。</p>
      <div v-for="(summary,key) in {baseline:results.baseline,abstention:results.abstention}" :key="key" class="experiment">
        <h3>{{ key==='baseline'?'原始检索对比 · 20 条查询':'无答案拒答试验 · 开发 24 条 / 留出 24 条' }}</h3>
        <p v-if="summary.status!=='available'" class="notice warning" role="status">{{ summary.notice }}</p>
        <template v-else>
          <p class="scope">{{ key==='baseline'?'实际 PostgreSQL / pgvector 临时索引；下表为未设置拒答阈值的结果。':'本地 NumPy 精确余弦；本次没有运行 PostgreSQL。仅用开发集选择阈值，下表为留出结果。' }}</p>
          <div class="table-wrap" tabindex="0" :aria-label="key==='baseline'?'可横向滚动的原始检索对比':'可横向滚动的拒答试验结果'"><table>
            <thead v-if="key==='baseline'"><tr><th>方法</th><th>前 6 正确命中</th><th>无答案仍返回候选</th><th>查询失败</th></tr></thead>
            <thead v-else><tr><th>方法 / 阈值</th><th>保留正确命中</th><th>无答案误接受</th><th>正确拒答</th><th>丢失正确命中</th><th>查询失败</th><th>预设门槛</th></tr></thead>
            <tbody><tr v-for="row in summary.rows" :key="row.method">
              <td>{{ names[row.method]||row.method }}<small v-if="key==='abstention'">{{ row.threshold?.toFixed(6) }}</small></td>
              <template v-if="key==='baseline'"><td>{{ row.correct_hit_at_6 }}/{{ row.answerable }}</td><td>{{ row.returned_unanswerable }}/{{ row.unanswerable }}</td><td>{{ row.failures }}/{{ row.answerable+row.unanswerable }}</td></template>
              <template v-else><td>{{ row.correct_hit_at_6_kept }}/{{ row.answerable }}</td><td class="negative">{{ row.accepted_unanswerable }}/{{ row.unanswerable }}</td><td>{{ row.rejected_unanswerable }}/{{ row.unanswerable }}</td><td>{{ row.correct_hit_at_6_lost }}</td><td>{{ row.failures }}/{{ row.answerable+row.unanswerable }}</td><td :class="{negative:!row.candidate_meets_gate}">{{ row.candidate_meets_gate?'通过（仅此试验）':'未通过' }}</td></template>
            </tr></tbody>
          </table></div>
          <p v-if="key==='abstention'" class="scope">门槛：12 条有答案＋12 条无答案；查询失败 0，误接受 ≤1，保留正确命中 ≥9，丢失正确命中 ≤1。失败保留在分母内。</p>
          <details><summary>摘要来源与指纹</summary><p><code>{{ summary.source }}</code></p><p>摘要 SHA-256：<code>{{ summary.summary_sha256 }}</code></p><p>摘要记录的报告 SHA-256：<code>{{ summary.report_sha256 }}</code></p></details>
        </template>
      </div>
      <p class="scope">两批查询均由实现者编写，未经过独立专家盲测，不能外推真实业务。已使用的留出集不再用于调参；可靠拒答仍未完成。</p>
      <p class="caption">{{ results.notice }}</p>
    </div>
    <p v-else class="small-empty">正在读取参考检索结果…</p>
  </section>
</template>

<style scoped>
.retrieval-body{padding:0 22px 20px}.retrieval-evaluation>p{margin:0 22px 20px}
.experiment{margin-top:20px}.experiment h3{font-size:14px;margin:0 0 8px}.scope{font-size:12px;line-height:1.8;color:#5f6b5c}
td{font-size:12px;white-space:nowrap}small{display:block;color:#7a8475;margin-top:4px;font-variant-numeric:tabular-nums}.negative{color:#8a3b2a;font-weight:600}
details{margin-top:10px;font-size:11px;color:#5f6b5c}summary{cursor:pointer}details p{overflow-wrap:anywhere}code{font-size:10px}
.table-wrap:focus-visible{outline:2px solid #b69358;outline-offset:2px}
@media(max-width:760px){.retrieval-body{padding:0 16px 18px}.retrieval-evaluation>p{margin:0 16px 18px}}
</style>
