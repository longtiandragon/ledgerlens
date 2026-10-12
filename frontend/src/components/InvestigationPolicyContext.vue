<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, type Evidence, type PolicyInterpretation } from '../api'
import type { InvestigationPolicyContextResponse } from '../generated/contracts'

const props = defineProps<{ runId: string; evidence: Evidence[]; interpretations?: PolicyInterpretation[] }>()
const emit = defineEmits<{ inspect: [evidence: Evidence] }>()
const context = ref<InvestigationPolicyContextResponse>()
const loading = ref(false), error = ref('')
let epoch = 0, controller: AbortController | undefined
const checks = computed(() => context.value?.payload?.checks ?? [])
const byId = computed(() => new Map(props.evidence.map(row => [row.id, row])))
const knownCheckIds = computed(() => new Set(checks.value.map(check => check.id)))
const statuses: Record<string, string> = { supported: '处理存在差异', normal: '有限条件检查正常', insufficient: '条件不足', unsupported: '超出范围' }
const treatments: Record<string, string> = { unknown: '未知', inventory: '存货', fixed_asset: '固定资产', expense: '费用', inventory_cost: '存货成本', administrative_expense: '管理费用', selling_expense: '销售费用', retain_prior_expense: '保留原费用', tax_refunds_received: '收到的税费返还', taxes_paid: '支付的各项税费', other_operating_cash_received: '收到其他经营活动现金', other_income: '其他收益' }
const conditions: Record<string, string> = { ordinary_course: '属于日常经营活动', held_for_sale: '持有目的为出售', economic_benefits_probable: '经济利益很可能流入', cost_reliably_measurable: '成本能够可靠计量', routine_maintenance: '属于日常维修', capitalization_conditions_met: '符合资本化条件' }
const staleReasons: Record<string, string> = { book_revision_changed: '账套资料已更新', policy_observations_changed: '条件版本已更新', policy_checks_changed: '保存的检查结果已更新', policy_rule_identity_changed: '政策规则版本已更新', current_policy_state_invalid: '当前政策记录未通过校验' }
const label = (value: string | null | undefined) => value == null ? '尚不能确定' : treatments[value] ?? value
const sourceUrl = (value: string) => {
  try { const url = new URL(value); return url.protocol === 'https:' && ['kjs.mof.gov.cn', 'www.mof.gov.cn', 'mof.gov.cn'].includes(url.hostname) && !url.username && !url.password ? url.href : undefined }
  catch { return undefined }
}
const time = (value: string) => value.replace('T', ' ').replace('+00:00', ' UTC')
async function load() {
  const current = ++epoch, identity = props.runId
  controller?.abort(); controller = new AbortController()
  context.value = undefined; error.value = ''; loading.value = true
  try {
    const result = await api<InvestigationPolicyContextResponse>(`/runs/${encodeURIComponent(identity)}/policy-context`, { signal: controller.signal })
    if (current === epoch && identity === props.runId) context.value = result
  } catch (failure) { if (current === epoch && identity === props.runId) error.value = failure instanceof Error ? failure.message : String(failure) }
  finally { if (current === epoch) loading.value = false }
}
watch(() => props.runId, load, { immediate: true })
onBeforeUnmount(() => { epoch++; controller?.abort() })
</script>

<template>
  <section class="run-policy-context" aria-label="本次调查的政策依据">
    <div class="policy-context-heading"><div><span class="caption">固定在调查启动时</span><h3>本次调查的政策依据</h3></div><button type="button" class="button" :disabled="loading" @click="load">{{ loading ? '正在读取…' : '刷新版本状态' }}</button></div>
    <p class="caption">条件由人工确认，检查由程序执行；模型解释仍待复核。后续补证或修订不会改写本次运行。</p>
    <p v-if="loading" role="status">正在读取保存的调查上下文。</p>
    <p v-else-if="error" role="alert" class="notice error">{{ error }}</p>
    <template v-else-if="context">
      <p v-if="context.status === 'legacy_uncaptured'" class="notice warning">此历史运行未保存政策检查上下文，不能把今天的检查补作当时的依据。</p>
      <template v-else-if="context.payload">
        <dl class="context-scope"><dt>账务资料截止</dt><dd>{{ checks[0]?.binding.source_cutoff ? time(checks[0].binding.source_cutoff) : '见固定数据快照' }}</dd><dt>条件与结果固定于</dt><dd>{{ time(context.payload.captured_at) }}</dd><dt>数据快照</dt><dd><code>{{ context.payload.snapshot_id }}</code></dd><dt>规则版本</dt><dd>{{ context.payload.policy_rule_identity.version }}</dd><dt>已保存检查</dt><dd>{{ checks.length }} 项 · {{ context.payload.observation_manifest.length }} 项条件版本</dd></dl>
        <p v-if="context.stale" class="notice warning">已有后续变更，此上下文供历史回查。{{ context.stale_reasons.map(reason => staleReasons[reason] ?? reason).join('；') }}</p>
        <p v-if="!checks.length" class="policy-empty">本次没有可提供的已保存政策检查。未确认、未检查或其他规则版本的结果不会自动补入。</p>
        <p class="caption">{{ context.payload.notice }}</p>
        <details v-for="check in checks" :key="check.id" class="captured-check" :data-policy-check="check.id">
          <summary><strong>{{ check.result.title }}</strong><span class="badge warning">{{ statuses[check.result.status] ?? check.result.status }}</span><span>条件版本 {{ check.binding.observation_version }}</span></summary>
          <p>{{ check.result.reason }}</p>
          <dl class="context-scope"><dt>预期处理</dt><dd>{{ label(check.result.expected_treatment) }}</dd><dt>已采用的处理</dt><dd>{{ label(check.result.reported_treatment) }}</dd><dt>检查编号</dt><dd><code>{{ check.id }}</code></dd></dl>
          <ul class="condition-list"><li v-for="(condition, name) in check.binding.observation.conditions" :key="name"><strong>{{ conditions[name] ?? name }}</strong><span>{{ condition.value === 'yes' ? '是' : condition.value === 'no' ? '否' : '未知' }} · {{ condition.confirmation === 'human_confirmed' ? '人工确认' : '未获人工确认' }}</span></li></ul>
          <p v-if="check.result.missing_conditions.length" class="notice warning">待确认：{{ check.result.missing_conditions.map(name => conditions[name] ?? name).join('、') }}</p>
          <div class="policy-sources"><strong>固定来源位置</strong><div v-for="locator in check.binding.evidence_locators" :key="locator.evidence_id"><button v-if="byId.has(locator.evidence_id)" type="button" class="evidence-chip" @click="emit('inspect', byId.get(locator.evidence_id)!)">{{ locator.source_id }}</button><code v-else>{{ locator.evidence_id }} · 尚未载入证据浏览器</code><span>{{ locator.document_name }} · {{ JSON.stringify(locator.locator) }}</span></div></div>
          <p class="caption"><a v-if="sourceUrl(check.result.source.url)" :href="sourceUrl(check.result.source.url)" target="_blank" rel="noopener noreferrer">财政部依据 · {{ check.result.source.source_id }}</a><span v-else>来源网址未通过校验</span> · {{ check.result.source.locator }}</p>
          <p class="caption">{{ check.result.scope_note }}</p>
        </details>
        <div v-if="interpretations?.length" class="policy-interpretations"><h4>模型政策解释 · 待复核</h4><p class="caption">引用存在且已读取不等于解释语义正确，也不构成记账或审批。</p><article v-for="(interpretation, i) in interpretations" :key="i"><p>{{ interpretation.text }}</p><div><code v-for="id in interpretation.policy_check_ids" :key="id">{{ id }}{{ knownCheckIds.has(id) ? '' : ' · 未在固定上下文中找到' }}</code></div></article></div>
      </template>
    </template>
  </section>
</template>

<style scoped>
.run-policy-context{margin:20px 0;padding:18px;border:1px solid #dce6d2;border-radius:6px;background:#fffefa;min-width:0}.policy-context-heading{display:flex;justify-content:space-between;gap:12px;align-items:center}.policy-context-heading h3{margin:4px 0}.context-scope{display:grid;grid-template-columns:130px minmax(0,1fr);gap:9px 14px;font-size:12px;line-height:1.6}.context-scope dt{color:#778474}.context-scope dd{margin:0;overflow-wrap:anywhere}.context-scope code{font-size:11px}.captured-check{margin-top:14px;border-top:1px solid #dce6d2;padding-top:12px}.captured-check summary{display:flex;flex-wrap:wrap;gap:10px;align-items:center;cursor:pointer;font-size:12px}.captured-check p{font-size:13px;line-height:1.8}.condition-list{padding:0;list-style:none;display:grid;gap:8px;font-size:12px}.condition-list li{display:flex;justify-content:space-between;gap:14px}.condition-list span{color:#6f7869}.policy-sources{display:grid;gap:10px;font-size:12px}.policy-sources>div{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.policy-sources code,.policy-sources span{overflow-wrap:anywhere;max-width:100%}.policy-empty{padding:12px;background:#f4f5ed;font-size:13px}.policy-interpretations article{border-left:3px solid #b9cbaa;padding:3px 12px;margin:12px 0;font-size:13px}.policy-interpretations code{display:block;overflow-wrap:anywhere;font-size:11px;color:#778474}.caption{line-height:1.6;overflow-wrap:anywhere}@media(max-width:600px){.run-policy-context{padding:12px}.policy-context-heading{align-items:flex-start;flex-direction:column}.context-scope{grid-template-columns:minmax(0,1fr);gap:3px}.context-scope dd{margin-bottom:8px}.condition-list li{flex-direction:column;gap:3px}.captured-check summary{gap:8px}.policy-context-heading .button{min-height:40px}}
</style>
