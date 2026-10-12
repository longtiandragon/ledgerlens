<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import Icon from './Icon.vue'

interface Replay { calculator_version:string;records:number;executed:number;exact_matches:number;model_requests:number;source_sha256:string;created_at:string }
interface Pilot { id:string;title:string;prompt_version:string;sample_group:string;samples:number;scorable:number;unscorable:number;answered:number;failed:number;abstained:number;exact_matches:number;exact_match_rate:number|null;paid_requests:number;tokens:number;created_at:string;source_sha256:string;cost_min_usd:string|null;cost_max_usd:string|null }
interface Results { arithmetic_replay:Replay|null;model_pilots:Pilot[];issues:{report_id:string;code:string}[];scope_notes:string[] }
const results=ref<Results>()
const loading=ref(false)
const error=ref('')
async function refresh(){
  if(loading.value)return
  loading.value=true;error.value=''
  try{results.value=await api<Results>('/evaluations/numeric')}
  catch(e){error.value=e instanceof Error?e.message:'历史摘要读取失败'}
  finally{loading.value=false}
}
const rate=(value:number|null)=>value===null?'—':`${(value*100).toFixed(1)}%`
const number=(value:number)=>value.toLocaleString('zh-CN')
onMounted(refresh)
</script>

<template>
  <section class="panel spaced numeric-evaluation" aria-labelledby="numeric-evaluation-title">
    <div class="panel-heading numeric-heading">
      <div><span class="section-number">03</span><h2 id="numeric-evaluation-title">数值推理组件</h2></div>
      <button class="button" :disabled="loading" aria-label="刷新数值组件历史结果" @click="refresh"><Icon name="refresh" :size="14"/>{{ loading?'读取中':'刷新历史摘要' }}</button>
    </div>
    <div v-if="error" class="notice error" role="alert">{{ error }}<span>刷新只读取结果，不会调用模型。</span></div>
    <p v-if="loading&&!results" class="numeric-empty" role="status">正在读取已经保存的实验结果…</p>
    <template v-if="results">
      <div class="proof-intro"><span class="tiny-label">FinQA · 公开 dev</span><p>把“计算正确”与“读题正确”分别检验。</p></div>
      <div v-if="results.arithmetic_replay" class="replay-band">
        <div class="replay-score"><span>给定参考程序</span><strong>{{ number(results.arithmetic_replay.exact_matches) }}<small> / {{ number(results.arithmetic_replay.records) }}</small></strong><p>与外部答案一致 · 5 位小数</p></div>
        <div class="replay-details"><div class="proof-path"><span>参考程序</span><Icon name="arrow" :size="14"/><span>Decimal 计算器</span><Icon name="arrow" :size="14"/><span>外部答案</span></div><p>这检验算术执行。正确程序由评测器提供，不能当作模型答题准确率。</p><div class="replay-facts"><span>执行 <b>{{ results.arithmetic_replay.executed }}</b> 条</span><span>模型请求 <b>{{ results.arithmetic_replay.model_requests }}</b> 次</span><code>{{ results.arithmetic_replay.calculator_version }}</code></div></div>
      </div>
      <p v-else class="numeric-empty">尚无有效的参考程序重放摘要。</p>
      <div class="numeric-model-heading"><h3>模型自己读题与选择公式</h3><span>所有已评分失败保留在分母中</span></div>
      <div v-if="results.model_pilots.length" class="numeric-table-wrap" tabindex="0" aria-label="可横向滚动的模型历史结果">
        <table class="numeric-table"><caption class="sr-only">FinQA 模型历史试跑；公开开发集，小样本，不能推导会计准确率</caption><thead><tr><th scope="col">试跑 / 数据分组</th><th scope="col">正确 / 已评分</th><th scope="col">输出校验通过</th><th scope="col">失败 / 保留</th><th scope="col">请求 / Token</th></tr></thead><tbody>
          <tr v-for="item in results.model_pilots" :key="item.id"><th scope="row"><strong>{{ item.title }}</strong><span>{{ item.sample_group }}</span><code>{{ item.prompt_version }}</code></th><td><strong class="score-fraction">{{ item.exact_matches }} / {{ item.scorable }}</strong><span>{{ rate(item.exact_match_rate) }}</span><small v-if="item.unscorable">{{ item.unscorable }} 题无参考答案</small></td><td><strong>{{ item.answered }} / {{ item.samples }}</strong><span>来源与算术校验</span></td><td><strong>{{ item.failed }} / {{ item.abstained }}</strong><span>失败 / 证据不足</span></td><td><strong>{{ item.paid_requests }} 次 / {{ number(item.tokens) }}</strong><span>实际累计 Token</span></td></tr>
        </tbody></table>
      </div>
      <p v-else class="numeric-empty">尚无有效的模型试跑摘要，未生成模型成绩。</p>
      <details v-if="results.model_pilots.length" class="numeric-provenance"><summary>查看版本、来源与费用口径</summary><div v-for="item in results.model_pilots" :key="item.id"><strong>{{ item.title }}</strong><span>{{ item.created_at }}</span><code>数据 SHA256 {{ item.source_sha256 }}</code><p v-if="item.cost_min_usd!==null&&item.cost_max_usd!==null">已知用量列表价格：USD {{ item.cost_min_usd }}–{{ item.cost_max_usd }}；不是实际账单。</p><p v-else>费用用量不完整或无法核验，保留未知。</p></div></details>
      <div v-if="results.issues.length" class="numeric-issues" role="status"><strong>部分摘要不可用</strong><span v-for="item in results.issues" :key="item.report_id">{{ item.report_id }}：{{ item.code==='not_available'?'尚未提供':'校验失败或无法读取' }}</span></div>
      <ul class="numeric-boundaries"><li v-for="note in results.scope_notes" :key="note">{{ note }}</li></ul>
    </template>
  </section>
</template>

<style scoped>
.numeric-heading{flex-wrap:wrap;gap:12px}.numeric-heading .button{font-size:11px}.proof-intro{display:flex;align-items:center;gap:14px;padding:0 24px 18px}.proof-intro p{font-family:'Noto Serif SC',SimSun,serif;font-size:18px;color:var(--ink)}.replay-band{display:grid;grid-template-columns:210px 1fr;margin:0 24px;border:1px solid #d8e4d7;background:#f0f5ed}.replay-score{padding:24px;border-right:1px solid #d8e4d7}.replay-score>span{font-size:11px;letter-spacing:1px;color:#546e5d}.replay-score>strong{display:block;margin:6px 0;font-family:Consolas,monospace;font-size:35px;font-weight:500;line-height:1.3;color:#285740}.replay-score small{font-size:18px;color:#76927c}.replay-score p{font-size:10px;color:#6d8272}.replay-details{padding:24px;align-self:center}.proof-path{display:flex;align-items:center;gap:10px;flex-wrap:wrap;color:#32644c;font-size:11px;margin-bottom:12px}.replay-details>p{font-size:12px;max-width:540px;color:#5c7062}.replay-facts{display:flex;flex-wrap:wrap;gap:16px;margin-top:15px;font-size:10px;color:#6b7f70}.replay-facts b{font-weight:500;color:#2d5b45}.replay-facts code{font-size:10px}.numeric-model-heading{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin:25px 24px 12px}.numeric-model-heading span{font-size:10px;color:var(--muted)}.numeric-table-wrap{overflow-x:auto;margin:0 24px}.numeric-table{width:100%;border-collapse:collapse;text-align:left;min-width:650px}.numeric-table thead{background:#f5f6ef}.numeric-table th,.numeric-table td{padding:15px 14px;border-bottom:1px solid var(--line);vertical-align:top;font-weight:400}.numeric-table thead th{font-size:10px;color:#718073}.numeric-table th>strong,.numeric-table td>strong{display:block;font-size:12px;font-weight:500;color:var(--ink);font-variant-numeric:tabular-nums}.numeric-table th>span,.numeric-table td>span{display:block;font-size:10px;color:var(--muted);margin-top:4px}.numeric-table th>code{display:block;font-size:9px;margin-top:5px;color:#899286}.numeric-table .score-fraction{font-family:Consolas,monospace;font-size:17px;color:#285c44}.numeric-table td small{display:block;color:#9b7438;font-size:10px}.numeric-provenance{margin:18px 24px;color:#6c7d6d;font-size:11px}.numeric-provenance summary{cursor:pointer;width:fit-content;padding:4px 0}.numeric-provenance>div{border-top:1px solid var(--line);margin-top:10px;padding-top:12px}.numeric-provenance strong{color:var(--ink);margin-right:12px}.numeric-provenance code{display:block;overflow-wrap:anywhere;margin:6px 0;font-size:10px}.numeric-issues{display:flex;flex-direction:column;gap:4px;margin:18px 24px;padding:12px 14px;border-left:2px solid #b99252;background:#fbf7eb;font-size:11px;color:#8b713f}.numeric-boundaries{margin:20px 24px;padding:14px 18px 14px 29px;background:#f8f8f2;border-top:1px solid var(--line);color:#7d887c;font-size:11px}.numeric-boundaries li+li{margin-top:5px}.numeric-empty{padding:18px 24px;color:var(--muted);font-size:12px}.numeric-evaluation>.notice{margin:0 24px 18px}.notice span{display:block;font-size:11px;margin-top:5px}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
@media(max-width:640px){.proof-intro{display:block;padding:0 16px 16px}.proof-intro p{font-size:16px;margin-top:8px}.replay-band{grid-template-columns:1fr;margin:0 16px}.replay-score{border-right:0;border-bottom:1px solid #d8e4d7;padding:18px}.replay-details{padding:18px}.numeric-model-heading{margin:20px 16px 12px}.numeric-table-wrap{margin:0 16px}.numeric-provenance,.numeric-issues,.numeric-boundaries{margin-left:16px;margin-right:16px}.numeric-heading{padding-left:16px;padding-right:16px}.numeric-heading .button{font-size:11px}.numeric-evaluation>.notice{margin-left:16px;margin-right:16px}}
</style>
