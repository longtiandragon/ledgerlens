<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NSelect } from 'naive-ui'
import { api } from '../api'
import type { PolicyCatalogResponse, PolicyCheckResponse, PolicyObservationResponse, PolicyObservationInput, PolicyObservation } from '../generated/contracts'
import Icon from './Icon.vue'

const props = defineProps<{ bookId: string; snapshotId: string | null; framework: 'CAS' | 'SCAS'; records: Record<string, unknown>[] }>()
type Topic = PolicyObservation['topic']
type Treatment = PolicyObservation['reported_treatment']
type Confirmation = NonNullable<PolicyObservation['conditions'][string]['confirmation']>
type ConditionDraft = { name: string; value: 'yes' | 'no' | 'unknown'; confirmed: boolean; origin: Confirmation; evidenceIds: string[] }
type Snapshot = { id: string; book_id: string; created_at: string; record_ids: string[]; policy: { framework: string; period: string } }
const catalog = ref<PolicyCatalogResponse>()
const snapshot = ref<Snapshot>()
const observations = ref<PolicyObservationResponse[]>([])
const checks = ref<PolicyCheckResponse[]>([])
const selected = ref<PolicyObservationResponse>()
const result = ref<PolicyCheckResponse>()
const loading = ref(false)
const busy = ref('')
const error = ref('')
const notice = ref('')
const editing = ref(true)
const topic = ref<Topic>('resale_inventory')
const conditions = ref<ConditionDraft[]>([])
const logicalKey = ref('')
const parentId = ref<string | null>(null)
const treatment = ref<Treatment>('unknown')
const treatmentEvidence = ref<string[]>([])
const beneficiary = ref<NonNullable<PolicyObservation['beneficiary']>>('unknown')
const beneficiaryEvidence = ref<string[]>([])
const beneficiaryConfirmed = ref(false)
const comment = ref('')
const humanConfirmed = ref(false)
const inspectedEvidence = ref('')
const moreObservations = ref(false)
const moreChecks = ref(false)
const PAGE_SIZE = 200
let observationCursor = 0
let checkCursor = 0
let generation = 0
let actionGeneration = 0
let controller: AbortController | undefined
let actionController: AbortController | undefined
let disposed = false

const conditionLabels: Record<string, string> = {
  ordinary_course: '属于日常经营活动', held_for_sale: '持有目的为出售',
  economic_benefits_probable: '经济利益很可能流入', cost_reliably_measurable: '成本能够可靠计量',
  routine_repair: '属于固定资产日常修理', capitalization_criteria_met: '满足资本化条件',
  previous_expensing_was_correct: '原费用化处理正确', later_sale_contract_or_sale: '已有后续销售合同或销售',
  vat_credit_refund_under_2022_policy: '适用所述2022留抵退税政策', received: '该事件为收到退税款',
  repaid_and_credit_continues: '该事件为缴回并继续按规定抵扣', enterprise_is_withholding_agent: '企业为个税扣缴义务人',
  personal_tax_fee_received: '收到法定个税扣缴手续费', beneficiary: '已确认的受益对象', reported_treatment: '当前处理的来源证据',
  lease_term_not_over_12_months: '已合理确定的租赁期不超过12个月', contains_purchase_option: '租赁包含购买选择权',
  low_value_asset_lease_eligible: '符合低价值资产租赁资格（按全新资产绝对价值、独立使用条件及不转租等综合确认；未知不得填否）',
  lease_liability_principal_or_interest: '偿还租赁负债本金或利息', prepaid_rent_or_lease_deposit: '支付预付租金或租赁保证金',
  simplified_short_term_or_low_value_lease: '属于按简化处理的短期或低价值资产租赁', variable_payment_outside_lease_liability: '未纳入租赁负债的可变租赁付款',
  transport_before_control_transfer: '运输发生在控制权转移之前', transport_not_separate_performance_obligation: '运输不构成单项履约义务',
  transport_cost_amortization_in_current_revenue_period: '本次仅核对随相关收入确认摊销进入本期损益的履约运输成本（不含收入确认前暂存成本或未拆分跨期金额）',
  new_revenue_standard_applied: '执行新收入准则', received_before_transfer: '转让商品前已收客户对价', vat_portion_excluded: '所检查金额不含增值税部分',
  contract_asset_impairment: '合同资产发生减值或转回', lessee_improvement_expenditure: '承租人租赁资产改良支出',
  foreign_currency_advance: '外币预收或预付款', measured_at_historical_cost: '以历史成本计量',
  debtor_settles_debt_with_inventory: '债务人以存货清偿债务', restructuring_outside_ordinary_activities: '该债务重组不属于日常活动',
  cash_discount_granted_on_sale: '销售商品时给予客户现金折扣',
  intragroup_share_payment: '适用股份支付准则的集团内交易', entity_receives_employee_services: '本企业是接受员工服务方',
  no_settlement_obligation: '本企业没有结算义务', debt_restructuring_agreement_signed: '已签署债务重组合同',
  present_obligation_not_released: '债务现时义务尚未解除', financial_instruments_2017_applied: '已执行2017年金融工具准则',
  first_application_new_revenue_standard: '属于首次执行新收入准则',
  new_receivable_or_contract_asset_from_transition: '应收款或合同资产因该首次执行而产生',
  government_grant: '属于政府补助', compensates_property_rental_costs: '补偿物业租赁成本',
  not_for_acquiring_long_term_assets: '不用于取得或形成长期资产', equity_award_cancelled_during_vesting: '等待期内取消授予的权益工具',
  employee_voluntary_exit: '职工自愿退出计划', not_cancelled_for_failure_of_vesting_conditions: '并非因未满足可行权条件而取消',
  asset_renovated: '固定资产发生更新改造', conclusive_evidence_life_extended: '有确凿证据表明主体确因改造延长使用寿命且符合法规',
  renovation_capitalization_criteria_met: '改造支出满足后续支出资本化条件',
  development_phase: '支出属于开发阶段（研究阶段为否）', technical_feasibility: '完成该数据资源无形资产在技术上可行',
  intention_to_complete: '具有完成并使用或出售的意图', benefit_generation_demonstrated: '能证明产生经济利益的方式（存在市场或内部有用性）',
  adequate_resources: '有足够的技术、财务和其他资源支持完成开发', expenditure_reliably_measured: '开发阶段支出能够可靠计量',
  equity_method_investment: '权益法核算的长期股权投资', market_price_significant_decline: '市价当期大幅下跌且明显高于正常预期',
  goodwill_from_business_combination: '企业合并形成的商誉', cgu_benefits_from_synergies: '资产组预计从合并协同效应中受益',
  lowest_internal_monitoring_level: '代表内部管理监控商誉的最低水平', not_larger_than_reporting_segment: '不大于报告分部（经营分部）',
  creditor_receives_non_financial_asset: '债权人受让非金融资产', arms_length_market_transaction: '在公平交易的市场环境中达成',
  waived_claim_fair_value_exceeds_carrying_balance: '放弃债权的公允价值高于其账面余额',
  debtor_repurchase_obligation: '约定债务人未来回购股权', mandatory_dividend_right: '债权人所持股份享有强制分红权',
  compound_instrument_with_liability_component: '替换工具包含金融负债成分（复合工具）',
  classified_as_equity_under_cas37: '按金融工具列报准则分类为权益工具',
  grant_conditioned_on_constructing_long_term_assets: '综合文件判断补助以购建或形成长期资产为前提',
  qualifying_asset_long_construction: '需相当长时间购建的符合资本化条件的资产', asset_expenditure_incurred: '资产支出已经发生',
  borrowing_cost_incurred: '借款费用已经发生', manufacturer_production_started: '确有证据表明生产商已开始必要的购建或生产活动',
  asset_ready_for_intended_use: '设备已达到预定可使用状态',
  specific_borrowing_interest_only: '本观察仅含直接归属于该设备的专门借款利息（不含辅助费用、一般借款、汇兑差额或混合费用）',
  capitalization_suspension_excluded: '已核对中断性质和连续时间，排除本次费用应暂停资本化的情形',
  uniform_capitalization_phase_for_observation: '本次观察费用全部处于同一阶段（未跨开始、暂停、恢复或停止时点）',
  developer_builds_for_sale: '房地产开发企业取得土地用于建造对外出售的房屋', self_constructed_own_use_building: '自行开发建造厂房等自用建筑物',
  currency_not_exchangeable: '货币不可兑换为另一种货币', estimate_reflects_orderly_exchange: '估计汇率如实反映有序兑换交易的汇率',
  estimation_disclosed: '已在附注披露所用汇率及估计过程',
  contract_term_not_over_12_months: '合同约定期限不超过12个月', renewal_or_non_termination_reasonably_certain: '合理确定将续租或不行使终止选择权',
  improvements_amortized_beyond_lease_term: '改良支出等的摊销期超过所确定的租赁期',
  prior_period_error_confirmed: '已认定构成前期差错', material_to_discovery_period: '对差错发现当期报表具有重要性',
  material_to_comparative_period: '对比较期间报表具有重要性',
}
const treatmentLabels: Record<string, string> = {
  unknown: '尚未确认当前处理', inventory: '存货', fixed_asset: '固定资产', period_expense: '当期费用',
  inventory_cost: '存货成本', management_expense: '管理费用', selling_expense: '销售费用',
  reverse_prior_expense: '冲回原费用并转入存货或成本', keep_prior_expense: '保留原正确费用化处理',
  tax_refunds_received: '收到的税费返还', other_operating_cash_received: '收到其他与经营活动有关的现金',
  taxes_paid: '支付税费（按适用制度列示）', other_income_and_operating_revenue: '其他收益科目 / 营业收入项目',
  nonoperating_income: '营业外收入', right_of_use_asset_and_lease_liability: '确认使用权资产和租赁负债',
  short_term_lease_simplified: '按短期租赁简化处理', financing_cash_outflow: '筹资活动现金流出', operating_cash_outflow: '经营活动现金流出',
  operating_cost: '营业成本', contract_liability: '合同负债', advance_receipts: '预收账款', deferred_income: '递延收益',
  asset_impairment_loss: '资产减值损失', credit_impairment_loss: '信用减值损失', long_term_prepaid_expense: '长期待摊费用',
  right_of_use_asset_cost: '计入使用权资产成本', historical_rate_no_exchange_difference: '按交易日汇率折算、不产生汇兑损益',
  exchange_difference_recognized: '按期末汇率折算并确认汇兑损益', other_income_not_revenue: '差额记入其他收益、不作销售',
  revenue_and_cost_of_sales: '确认收入并结转销售成本', variable_consideration: '按可变对价处理', finance_expense: '财务费用', other: '其他处理',
  equity_settled_share_payment: '权益结算的股份支付', cash_settled_share_payment: '现金结算的股份支付',
  defer_debt_restructuring_gain: '不终止确认债务，不确认重组损益', recognize_debt_restructuring_gain: '签约时确认重组损益',
  opening_retained_earnings_no_comparative_restatement: '调整年初留存收益，不追溯可比信息', restate_comparative_ecl: '追溯可比期间信用损失',
  income_related_grant: '与收益相关的政府补助', asset_related_grant: '与资产相关的政府补助',
  accelerate_vesting_keep_prior_expense: '加速行权并保留以前费用', reverse_share_payment_expense: '冲回以前股份支付费用',
  revise_remaining_useful_life: '调整剩余折旧年限', keep_useful_life_estimate: '维持原使用寿命估计',
  capitalize_and_derecognize_replaced_part: '资本化并终止确认被替换部分', capitalize_without_derecognition: '资本化但未终止确认被替换部分',
  expense_renovation: '改造支出费用化', data_resource_intangible_asset: '确认为数据资源无形资产',
  estimate_recoverable_amount_higher_of: '按两者较高者估计可收回金额', share_price_as_recoverable_amount: '直接以股价计算可收回金额',
  no_impairment_test: '未进行减值测试', goodwill_allocated_to_qualifying_cgu: '分摊至满足条件的资产组',
  reallocate_goodwill_to_qualifying_cgu: '须重新分摊商誉', waived_claim_fair_value_basis: '以放弃债权公允价值计量',
  received_asset_fair_value_basis: '以受让资产公允价值计量', claim_carrying_amount_basis: '以债权账面余额计量',
  debt_to_equity_restructuring: '按债转股方式处理', not_debt_to_equity_restructuring: '不属于债转股方式',
  capitalize_borrowing_costs: '借款费用资本化', expense_borrowing_costs: '借款费用费用化',
  capitalization_base_includes_land_cost: '资本化基础包含土地使用权支出', capitalization_base_excludes_land_cost: '资本化基础不含土地使用权支出',
  observable_rate_unadjusted: '未经调整的可观察汇率', first_rate_after_restoration: '可兑换性恢复后的首个汇率',
  other_estimation_technique: '其他估计方法', retrospective_restatement_of_comparatives: '追溯调整前期比较数据',
  current_period_adjustment: '调整发现当期相关项目',
}
const beneficiaryLabels = { unknown: '尚未确认', inventory_production: '存货生产或加工', administration: '行政管理部门', dedicated_sales: '专设销售机构', other: '其他受益对象' }
const statusLabels = { normal: '处理一致', supported: '存在有依据的差异', insufficient: '证据不足', unsupported: '超出支持范围' }
const rules = computed(() => catalog.value?.rules.filter(rule => rule.framework === props.framework) ?? [])
const rule = computed(() => rules.value.find(item => item.topic === topic.value))
const scopeKey = computed(() => JSON.stringify([props.bookId, props.snapshotId, props.framework]))
const scopedRecords = computed(() => {
  const members = new Set(snapshot.value?.record_ids ?? [])
  return props.records.filter(item => typeof item.id === 'string' && members.has(item.id) && typeof item.evidence_id === 'string')
})
const evidenceOptions = computed(() => scopedRecords.value.map(record => ({ value: record.evidence_id as string,
  label: [record.source_id, record.counterparty, record.account, typeof record.amount === 'string' ? record.amount : typeof record.debit === 'string' ? `借 ${record.debit} / 贷 ${record.credit ?? ''}` : '', typeof record.description === 'string' ? record.description.slice(0, 45) : ''].filter(Boolean).join(' · '),
})))
const inspectedRecord = computed(() => scopedRecords.value.find(item => item.evidence_id === inspectedEvidence.value))
const selectedChecks = computed(() => checks.value.filter(item => item.observation_id === selected.value?.id && item.snapshot_id === selected.value?.snapshot_id))
const confirmedCount = computed(() => conditions.value.filter(item => item.confirmed && item.value !== 'unknown' && item.evidenceIds.length).length)
const canSave = computed(() => editing.value && humanConfirmed.value && !busy.value && !loading.value && !!snapshot.value && !!rule.value && !!logicalKey.value.trim())
const canCheck = computed(() => selected.value?.latest && !selected.value.stale && selected.value.snapshot_id === props.snapshotId && !busy.value && !loading.value)
function label(value?: string | null) { return value ? treatmentLabels[value] ?? value : '尚无确定处理建议' }
function sourceUrl(raw?: string) {
  try { const url = new URL(raw ?? ''); return url.protocol === 'https:' && ['kjs.mof.gov.cn', 'www.mof.gov.cn'].includes(url.hostname) && !url.username && !url.password && !url.port && !url.hash ? url.href : undefined } catch { return undefined }
}
function fail(cause: unknown) { return cause instanceof Error ? cause.message : '政策检查请求失败' }
function time(value: string) { return value.replace('T', ' ').replace(/(?:\.\d+)?(?:Z|\+00:00)$/, ' UTC') }
function stringify(value: unknown) { return typeof value === 'string' ? value : JSON.stringify(value) }
function buildConditions() {
  conditions.value = (rule.value?.required_conditions ?? []).filter(name => name !== 'beneficiary').map(name => ({ name, value: 'unknown', confirmed: false, origin: 'unconfirmed', evidenceIds: [] }))
}
function blankForm() {
  logicalKey.value = `复核-${crypto.randomUUID().slice(0, 8)}`
  parentId.value = null; treatment.value = 'unknown'; treatmentEvidence.value = []
  beneficiary.value = 'unknown'; beneficiaryEvidence.value = []; beneficiaryConfirmed.value = false
  comment.value = ''; humanConfirmed.value = false; buildConditions()
}
function newObservation() { selected.value = undefined; result.value = undefined; editing.value = true; notice.value = ''; blankForm() }
function changeTopic() { blankForm(); notice.value = '已切换检查主题；业务条件需要重新填写和确认。' }
function changeCondition(item: ConditionDraft) { item.confirmed = false; item.origin = 'unconfirmed'; humanConfirmed.value = false }
function selectObservation(item: PolicyObservationResponse) {
  actionGeneration++; actionController?.abort(); busy.value = ''; error.value = ''
  selected.value = item; result.value = checks.value.filter(check => check.observation_id === item.id && check.snapshot_id === item.snapshot_id).at(-1)
  editing.value = false; humanConfirmed.value = false; inspectedEvidence.value = ''
}
function revise() {
  if (!selected.value || selected.value.stale || !selected.value.latest || selected.value.snapshot_id !== props.snapshotId) return
  const saved = selected.value.payload
  topic.value = saved.topic; logicalKey.value = selected.value.logical_key; parentId.value = selected.value.id
  buildConditions()
  conditions.value.forEach(item => {
    const original = saved.conditions[item.name]
    if (original) { item.value = original.value ?? 'unknown'; item.evidenceIds = [...(original.evidence_ids ?? [])]; item.origin = original.confirmation === 'human_confirmed' ? 'unconfirmed' : original.confirmation ?? 'unconfirmed'; item.confirmed = false }
  })
  treatment.value = saved.reported_treatment ?? 'unknown'; treatmentEvidence.value = [...(saved.treatment_evidence_ids ?? [])]
  beneficiary.value = saved.beneficiary ?? 'unknown'; beneficiaryEvidence.value = [...(saved.beneficiary_evidence_ids ?? [])]
  beneficiaryConfirmed.value = false; humanConfirmed.value = false; comment.value = selected.value.comment
  result.value = undefined; editing.value = true; notice.value = '历史值已填入草稿。请重新逐项确认，再保存为新版本。'
}
async function loadScope(reset = true) {
  const scope = scopeKey.value, requested = ++generation
  const previousSelected = selected.value?.id, previousResult = result.value?.id, previousEditing = editing.value
  controller?.abort(); actionController?.abort(); actionGeneration++; controller = new AbortController()
  loading.value = true; busy.value = ''; error.value = ''; notice.value = ''; snapshot.value = undefined
  observations.value = []; checks.value = []; selected.value = undefined; result.value = undefined; inspectedEvidence.value = ''
  if (reset) { editing.value = true; conditions.value = []; humanConfirmed.value = false; parentId.value = null }
  if (!props.snapshotId) { loading.value = false; return }
  const bookPath = `/books/${encodeURIComponent(props.bookId)}`
  const params = new URLSearchParams({ offset: '0', limit: String(PAGE_SIZE) })
  try {
    const [available, frozen, saved, savedChecks] = await Promise.all([
      api<PolicyCatalogResponse>('/policy-checks/catalog', { signal: controller.signal }),
      api<Snapshot>(`/snapshots/${encodeURIComponent(props.snapshotId)}`, { signal: controller.signal }),
      api<PolicyObservationResponse[]>(`${bookPath}/policy-observations?${params}`, { signal: controller.signal }),
      api<PolicyCheckResponse[]>(`${bookPath}/policy-checks?${params}`, { signal: controller.signal }),
    ])
    if (disposed || requested !== generation || scope !== scopeKey.value) return
    if (frozen.id !== props.snapshotId || frozen.book_id !== props.bookId || frozen.policy.framework !== props.framework || !Array.isArray(frozen.record_ids) || frozen.record_ids.some(id => typeof id !== 'string') || new Set(frozen.record_ids).size !== frozen.record_ids.length) throw new Error('账套、制度与快照范围不一致，请刷新当前资料。')
    if (saved.some(item => item.book_id !== props.bookId || item.payload.book_id !== item.book_id || item.payload.snapshot_id !== item.snapshot_id) || savedChecks.some(item => item.book_id !== props.bookId || item.binding.ledger_snapshot_id !== item.snapshot_id)) throw new Error('返回的政策记录不属于当前账套或其冻结快照。')
    catalog.value = available; snapshot.value = frozen; observations.value = saved; checks.value = savedChecks
    moreObservations.value = saved.length === PAGE_SIZE; moreChecks.value = savedChecks.length === PAGE_SIZE
    observationCursor = saved.length; checkCursor = savedChecks.length
    if (!rules.value.some(item => item.topic === topic.value)) topic.value = rules.value[0]?.topic ?? 'resale_inventory'
    if (reset) blankForm()
    else { selected.value = saved.find(item => item.id === previousSelected); result.value = savedChecks.find(item => item.id === previousResult); editing.value = previousEditing || !selected.value }
  } catch (cause) { if (!disposed && requested === generation && scope === scopeKey.value) error.value = fail(cause) }
  finally { if (!disposed && requested === generation && scope === scopeKey.value) loading.value = false }
}
async function more(kind: 'observations' | 'checks') {
  if (busy.value || loading.value || !props.snapshotId) return
  const scope = scopeKey.value, requested = ++actionGeneration
  actionController?.abort(); actionController = new AbortController(); busy.value = 'more'; error.value = ''
  const list = kind === 'observations' ? observations.value : checks.value
  const params = new URLSearchParams({ offset: String(kind === 'observations' ? observationCursor : checkCursor), limit: String(PAGE_SIZE) })
  try {
    const rows = await api<(PolicyObservationResponse | PolicyCheckResponse)[]>(`/books/${encodeURIComponent(props.bookId)}/policy-${kind}?${params}`, { signal: actionController.signal })
    if (disposed || requested !== actionGeneration || scope !== scopeKey.value) return
    if (rows.some(item => item.book_id !== props.bookId) || new Set(rows.map(item => item.id)).size !== rows.length) throw new Error('分页记录的来源范围或身份不一致，请刷新。')
    const merged = new Map(list.map(item => [item.id, item]))
    for (const item of rows) {
      const existing = merged.get(item.id)
      if (existing && (existing.snapshot_id !== item.snapshot_id || ('digest' in existing && 'digest' in item ? existing.digest !== item.digest : 'artifact_digest' in existing && 'artifact_digest' in item ? existing.artifact_digest !== item.artifact_digest : true))) throw new Error('同一历史记录的冻结内容发生变化，请刷新。')
      merged.set(item.id, item)
    }
    const ordered = [...merged.values()].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))
    if (kind === 'observations') { observations.value = ordered as PolicyObservationResponse[]; observationCursor += rows.length; moreObservations.value = rows.length === PAGE_SIZE }
    else { checks.value = ordered as PolicyCheckResponse[]; checkCursor += rows.length; moreChecks.value = rows.length === PAGE_SIZE; if (selected.value && !result.value) result.value = selectedChecks.value.at(-1) }
  } catch (cause) { if (!disposed && requested === actionGeneration && scope === scopeKey.value) error.value = fail(cause) }
  finally { if (!disposed && requested === actionGeneration && scope === scopeKey.value) busy.value = '' }
}
function draft(): PolicyObservationInput {
  const refs = new Set(evidenceOptions.value.map(item => item.value))
  const allRefs = [...treatmentEvidence.value, ...beneficiaryEvidence.value, ...conditions.value.flatMap(item => item.evidenceIds)]
  if (allRefs.some(ref => !refs.has(ref))) throw new Error('所选来源不在当前快照可见资料中，请重新选择。')
  if (conditions.value.some(item => item.evidenceIds.length > 10) || treatmentEvidence.value.length > 10 || beneficiaryEvidence.value.length > 10) throw new Error('每项条件最多引用10条来源。')
  if (treatment.value !== 'unknown' && !treatmentEvidence.value.length) throw new Error('已知当前处理需要至少一条来源证据。')
  if (conditions.value.some(item => item.confirmed && (item.value === 'unknown' || !item.evidenceIds.length))) throw new Error('人工确认的条件必须有确定值及来源证据。')
  if (beneficiaryConfirmed.value && (beneficiary.value === 'unknown' || !beneficiaryEvidence.value.length)) throw new Error('人工确认受益对象需要确定对象及来源证据。')
  return { snapshot_id: props.snapshotId!, logical_observation_key: logicalKey.value.trim(), parent_version_id: parentId.value,
    human_confirmation: humanConfirmed.value, topic: topic.value,
    conditions: Object.fromEntries(conditions.value.map(item => [item.name, { value: item.value, confirmation: item.confirmed ? 'human_confirmed' : item.origin, evidence_ids: [...item.evidenceIds] }])),
    reported_treatment: treatment.value, treatment_evidence_ids: [...treatmentEvidence.value],
    beneficiary: topic.value === 'routine_repair' ? beneficiary.value : 'unknown',
    beneficiary_confirmation: topic.value === 'routine_repair' && beneficiaryConfirmed.value ? 'human_confirmed' : 'unconfirmed',
    beneficiary_evidence_ids: topic.value === 'routine_repair' ? [...beneficiaryEvidence.value] : [], comment: comment.value,
  }
}
async function save() {
  if (!canSave.value) return
  const scope = scopeKey.value, requested = ++actionGeneration
  actionController?.abort(); actionController = new AbortController(); busy.value = 'save'; error.value = ''
  try {
    const saved = await api<PolicyObservationResponse>(`/books/${encodeURIComponent(props.bookId)}/policy-observations`, { method: 'POST', body: JSON.stringify(draft()), signal: actionController.signal })
    if (disposed || requested !== actionGeneration || scope !== scopeKey.value) return
    if (saved.book_id !== props.bookId || saved.snapshot_id !== props.snapshotId) throw new Error('保存结果的账套快照不一致。')
    observations.value = observations.value.map(item => item.logical_key === saved.logical_key ? { ...item, latest: false, stale: true } : item).concat(saved)
    selected.value = saved; editing.value = false; result.value = undefined; humanConfirmed.value = false
    notice.value = `条件版本 ${saved.version} 已保存；尚未执行政策检查。`
  } catch (cause) { if (!disposed && requested === actionGeneration && scope === scopeKey.value) error.value = fail(cause) }
  finally { if (!disposed && requested === actionGeneration && scope === scopeKey.value) busy.value = '' }
}
async function runCheck() {
  if (!selected.value || !canCheck.value) return
  const scope = scopeKey.value, observationId = selected.value.id, requested = ++actionGeneration
  actionController?.abort(); actionController = new AbortController(); busy.value = 'check'; error.value = ''
  try {
    const checked = await api<PolicyCheckResponse>(`/books/${encodeURIComponent(props.bookId)}/policy-observations/${encodeURIComponent(observationId)}/check`, { method: 'POST', signal: actionController.signal })
    if (disposed || requested !== actionGeneration || scope !== scopeKey.value || selected.value?.id !== observationId) return
    if (checked.book_id !== props.bookId || checked.snapshot_id !== props.snapshotId || checked.observation_id !== observationId) throw new Error('检查结果的来源范围不一致。')
    checks.value = checks.value.filter(item => item.id !== checked.id).concat(checked); result.value = checked
    notice.value = '检查结果已保存。原账簿未修改。'
  } catch (cause) { if (!disposed && requested === actionGeneration && scope === scopeKey.value) error.value = fail(cause) }
  finally { if (!disposed && requested === actionGeneration && scope === scopeKey.value) busy.value = '' }
}
async function exportCheck() {
  if (!result.value || busy.value) return
  const scope = scopeKey.value, id = result.value.id, savedSnapshotId = result.value.snapshot_id, requested = ++actionGeneration
  actionController?.abort(); actionController = new AbortController(); busy.value = 'export'; error.value = ''
  try {
    const exported = await api<PolicyCheckResponse>(`/books/${encodeURIComponent(props.bookId)}/policy-checks/${encodeURIComponent(id)}/export`, { signal: actionController.signal })
    if (disposed || requested !== actionGeneration || scope !== scopeKey.value || result.value?.id !== id) return
    if (exported.id !== id || exported.book_id !== props.bookId || exported.snapshot_id !== savedSnapshotId) throw new Error('导出底稿的范围不一致。')
    const url = URL.createObjectURL(new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `policy-check-${id}.json`; anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000); notice.value = '已导出包含来源、条件版本和规则身份的底稿。'
  } catch (cause) { if (!disposed && requested === actionGeneration && scope === scopeKey.value) error.value = fail(cause) }
  finally { if (!disposed && requested === actionGeneration && scope === scopeKey.value) busy.value = '' }
}
watch(scopeKey, () => loadScope(), { immediate: true })
onBeforeUnmount(() => { disposed = true; generation++; actionGeneration++; controller?.abort(); actionController?.abort() })
</script>

<template>
  <section class="policy-checks panel spaced" aria-label="政策一致性检查">
    <header class="policy-heading"><div><span class="policy-kicker">SOURCE-BOUND POLICY CHECK</span><h2>先确认条件，再核对处理。</h2><p>依据归档财政部问答检查有限业务路径。人工确认、来源证据与处理结论分别留存。</p></div><span class="badge neutral">{{ framework === 'CAS' ? '企业会计准则' : '小企业会计准则' }}</span></header>
    <div class="policy-scope"><span>本制度 {{ rules.length }} 条实验性规则</span><span v-if="snapshot">{{ scopedRecords.length }} 条可见来源 · 截止 {{ time(snapshot.created_at) }}</span><code v-if="snapshotId">快照 {{ snapshotId.slice(0, 8) }}</code><button type="button" class="text-button" :disabled="loading || !!busy" @click="loadScope(false)">刷新记录</button></div>
    <p class="policy-boundary">条件确认是本地用户的复核陈述，尚无专业认证；规则公开日期不是法规生效日期。检查结果不会生成正式分录或自动成为业务事实。</p>
    <div v-if="error" class="notice error policy-message" role="alert">{{ error }}</div>
    <div v-if="notice" class="notice neutral policy-message" role="status">{{ notice }}</div>
    <div v-if="snapshot && !loading" class="policy-loaded-history" aria-label="政策历史加载范围"><span>已载入 {{ observations.length }} 个条件版本 · {{ checks.length }} 份底稿{{ moreObservations || moreChecks ? '；列表尚未全部载入' : '' }}</span><button v-if="moreObservations" type="button" class="text-button" :disabled="!!busy" @click="more('observations')">加载更多条件版本</button><button v-if="moreChecks" type="button" class="text-button" :disabled="!!busy" @click="more('checks')">加载更多底稿</button></div>
    <div v-if="!snapshotId" class="policy-empty">当前账套尚无数据快照。先导入资料并创建快照，再为业务条件绑定来源。</div>
    <div v-else-if="loading" class="policy-empty" role="status">正在读取当前快照与政策记录…</div>
    <div v-else-if="snapshot" class="policy-layout">
<aside class="policy-history"><div class="policy-subheading"><h3>已保存条件</h3><button type="button" class="text-button" :disabled="!!busy" @click="newObservation">新增检查</button></div><p v-if="!observations.length" class="policy-caption">还没有保存的条件版本。</p><button v-for="item in [...observations].reverse()" :key="item.id" type="button" class="policy-history-item" :class="{ selected: selected?.id === item.id }" @click="selectObservation(item)"><strong>{{ item.logical_key }}</strong><span>版本 {{ item.version }} · {{ item.stale ? '已过期，只读' : item.latest ? '当前版本' : '历史版本' }}</span><small>{{ time(item.created_at) }} · 快照 {{ item.snapshot_id.slice(0, 8) }}</small></button><div class="policy-source-preview"><h3>查看当前快照来源</h3><NSelect v-model:value="inspectedEvidence" :options="evidenceOptions" filterable clearable placeholder="按来源编号、主体或金额查找" aria-label="查看原始记录"/><dl v-if="inspectedRecord" class="policy-record" aria-label="所选原始记录"><template v-for="(value, key) in inspectedRecord" :key="key"><dt>{{ key }}</dt><dd>{{ stringify(value) }}</dd></template></dl><p v-else class="policy-caption">这里仅列出当前快照成员。金额按原始字符串展示。</p></div></aside>
      <div class="policy-content">
        <p v-if="selected && selected.snapshot_id !== snapshotId" class="notice warning policy-historical-snapshot">正在查看历史快照 {{ selected.snapshot_id }} 的条件及底稿。仅可回查和导出；当前快照来源不会补入旧结果。</p>
        <form v-if="editing" @submit.prevent="save">
          <div class="policy-subheading"><h3>{{ parentId ? '修订条件草稿' : '新增条件检查' }}</h3><span class="policy-caption">{{ confirmedCount }} / {{ conditions.length }} 项条件已确认</span></div>
          <div class="policy-form-top"><label>检查名称<input v-model="logicalKey" aria-label="检查名称" maxlength="200" required/></label><label>业务主题<select v-model="topic" aria-label="业务主题" :disabled="!!parentId" @change="changeTopic"><option v-for="item in rules" :key="item.rule_id" :value="item.topic">{{ item.title }}</option></select></label></div>
          <div v-if="rule" class="policy-rule-help"><p>{{ rule.applicable_conditions }}</p><a v-if="sourceUrl(rule.source.url)" :href="sourceUrl(rule.source.url)" target="_blank" rel="noopener noreferrer">财政部依据 <Icon name="link" :size="12"/></a><span>公开于 {{ rule.source.publication_date }} · 实验性</span><details><summary>适用限制</summary><p v-for="limit in [...rule.unsupported_conditions, ...rule.known_limits]" :key="limit">{{ limit }}</p></details></div>
          <div class="policy-conditions"><fieldset v-for="item in conditions" :key="item.name" :data-predicate="item.name"><legend>{{ conditionLabels[item.name] ?? item.name }}</legend><div class="policy-condition-fields"><label>当前判断<select v-model="item.value" :aria-label="`${conditionLabels[item.name]}判断`" @change="changeCondition(item)"><option value="unknown">未知 / 需要补证</option><option value="yes">是</option><option value="no">否</option></select></label><div><label class="policy-field-label">来源证据</label><NSelect v-model:value="item.evidenceIds" multiple filterable :max-tag-count="2" :options="evidenceOptions" :aria-label="`${conditionLabels[item.name]}证据`" placeholder="选择当前快照来源" @update:value="item.confirmed = false; humanConfirmed = false"/></div></div><label class="policy-confirm"><input v-model="item.confirmed" type="checkbox" :disabled="item.value === 'unknown' || !item.evidenceIds.length" :aria-label="`人工确认${conditionLabels[item.name]}`" @change="humanConfirmed = false"/>我已依据所选来源确认此条件</label><small v-if="!item.confirmed" class="policy-caption">此条件仍未确认，模型建议或勾选“是”不会自动确认。</small></fieldset></div>
          <fieldset v-if="topic === 'routine_repair'" class="policy-extra"><legend>受益对象</legend><div class="policy-condition-fields"><label>支出受益对象<select v-model="beneficiary" aria-label="支出受益对象" @change="beneficiaryConfirmed = false; humanConfirmed = false"><option v-for="(text, value) in beneficiaryLabels" :key="value" :value="value">{{ text }}</option></select></label><div><label class="policy-field-label">来源证据</label><NSelect v-model:value="beneficiaryEvidence" multiple filterable :options="evidenceOptions" :max-tag-count="2" aria-label="受益对象证据" @update:value="beneficiaryConfirmed = false; humanConfirmed = false"/></div></div><label class="policy-confirm"><input v-model="beneficiaryConfirmed" type="checkbox" :disabled="beneficiary === 'unknown' || !beneficiaryEvidence.length" aria-label="人工确认受益对象" @change="humanConfirmed = false"/>我已依据来源确认受益对象</label></fieldset>
          <fieldset class="policy-extra"><legend>当前账务处理</legend><div class="policy-condition-fields"><label>已采用的处理<select v-model="treatment" aria-label="已采用的处理" @change="humanConfirmed = false"><option v-for="(text, value) in treatmentLabels" :key="value" :value="value">{{ rule?.treatment_captions[value] ?? text }}</option></select></label><div><label class="policy-field-label">处理来源</label><NSelect v-model:value="treatmentEvidence" multiple filterable :options="evidenceOptions" :max-tag-count="2" aria-label="当前处理证据" placeholder="当前处理未知时可留空" @update:value="humanConfirmed = false"/></div></div></fieldset>
          <label class="policy-comment">说明与待补资料<textarea v-model="comment" rows="3" maxlength="4000" aria-label="说明与待补资料" @input="humanConfirmed = false" placeholder="记录判断依据、尚未确定的条件或待补资料…"/></label>
          <label class="policy-confirm policy-final-confirm"><input v-model="humanConfirmed" type="checkbox" aria-label="确认提交本次条件版本"/>我确认提交上述条件及来源；未知事项保留为未确认</label>
          <div class="policy-actions"><button type="submit" class="button primary" :disabled="!canSave">{{ busy === 'save' ? '正在保存…' : parentId ? '保存修订版本' : '保存条件版本' }}</button><span class="policy-caption">保存条件后，再单独执行检查。</span></div>
        </form>
        <div v-else-if="selected" class="policy-saved"><div class="policy-subheading"><h3>{{ selected.logical_key }}</h3><span :class="['badge', selected.stale ? 'warning' : 'neutral']">版本 {{ selected.version }} · {{ selected.stale ? '已过期，只读' : '已保存' }}</span></div><dl class="policy-saved-facts"><template v-for="(item, name) in selected.payload.conditions" :key="name"><dt>{{ conditionLabels[name] ?? name }}</dt><dd>{{ item.value === 'yes' ? '是' : item.value === 'no' ? '否' : '未知' }} · {{ item.confirmation === 'human_confirmed' ? '人工确认' : '未获人工确认' }}<small>{{ item.evidence_ids?.length ?? 0 }} 条来源</small></dd></template><dt>当前处理</dt><dd>{{ label(selected.payload.reported_treatment) }}</dd><dt v-if="selected.payload.topic === 'routine_repair'">受益对象</dt><dd v-if="selected.payload.topic === 'routine_repair'">{{ beneficiaryLabels[selected.payload.beneficiary ?? 'unknown'] }} · {{ selected.payload.beneficiary_confirmation === 'human_confirmed' ? '人工确认' : '未获人工确认' }}</dd></dl><p v-if="selected.comment" class="policy-comment-text">{{ selected.comment }}</p><div v-if="selected.stale" class="notice warning">来源或条件版本已更新。此记录仅供回查，请在当前快照创建新检查。</div><div class="policy-actions"><button type="button" class="button primary" :disabled="!canCheck" @click="runCheck">{{ busy === 'check' ? '正在检查…' : '执行政策检查' }}</button><button type="button" class="button" :disabled="selected.stale || !selected.latest || !!busy" @click="revise">修订条件</button></div><label v-if="selectedChecks.length > 1" class="policy-check-picker">查看保存的规则版本<select :value="result?.id" aria-label="查看保存的规则版本" @change="result = selectedChecks.find(item => item.id === ($event.target as HTMLSelectElement).value)"><option v-for="item in selectedChecks" :key="item.id" :value="item.id">{{ time(item.created_at) }} · {{ item.binding.policy_rule_identity.version }} · {{ item.pack_compatible ? '当前规则' : '历史规则' }}</option></select></label></div>
        <article v-if="result" class="policy-result" aria-label="政策检查结果"><header><div><span class="policy-kicker">SAVED CONSISTENCY RESULT</span><h3>{{ statusLabels[result.result.status] }}</h3></div><span :class="['badge', result.result.status === 'normal' ? 'success' : result.result.status === 'supported' ? 'danger' : 'warning']">{{ result.stale ? '历史结果 / 已过期' : '归档问答检查' }}</span></header><p>{{ result.result.reason }}</p><div class="policy-treatments"><div><span>当前处理</span><strong>{{ label(result.result.reported_treatment) }}</strong></div><div><span>规则支持的处理</span><strong>{{ label(result.result.expected_treatment) }}</strong></div></div><p v-if="result.result.missing_conditions.length" class="policy-missing">待确认：{{ result.result.missing_conditions.map(name => conditionLabels[name] ?? name).join('、') }}</p><p v-if="!result.pack_compatible" class="notice warning">执行规则已更新；保留原结果，未用当前规则覆盖历史结论。</p><details class="policy-result-sources"><summary>{{ result.binding.evidence_locators.length }} 条已定位来源</summary><div v-for="item in result.binding.evidence_locators" :key="item.evidence_id"><strong>{{ item.document_name }}</strong><span>{{ item.source_id }} · {{ stringify(item.locator) }} · {{ item.synthetic ? '合成资料' : '导入资料' }}</span><code>{{ item.document_sha256 }}</code></div></details><div class="policy-result-footer"><a v-if="sourceUrl(result.result.source.url)" :href="sourceUrl(result.result.source.url)" target="_blank" rel="noopener noreferrer">财政部依据 <Icon name="link" :size="12"/></a><span>公开于 {{ result.result.source.publication_date }}</span><button type="button" class="button" :disabled="!!busy" @click="exportCheck"><Icon name="download" :size="13"/>导出政策底稿</button></div><details class="policy-fingerprints"><summary>快照与执行版本</summary><dl><dt>账务快照</dt><dd>{{ result.binding.ledger_snapshot_id }}</dd><dt>条件版本</dt><dd>{{ result.binding.observation_version }} · {{ result.binding.observation_digest }}</dd><dt>规则包</dt><dd>{{ result.binding.policy_rule_identity.version }} · {{ result.binding.policy_rule_identity.source_sha256 }}</dd><dt>底稿摘要</dt><dd>{{ result.artifact_digest }}</dd></dl></details><small class="policy-caption">此底稿为有限政策一致性检查，原账簿未修改。{{ result.result.scope_note }}</small></article>
      </div>
    </div>
  </section>
</template>

<style scoped>
.policy-loaded-history{padding:10px 26px;border-bottom:1px solid #e8ecdf;display:flex;align-items:center;flex-wrap:wrap;gap:12px;font-size:10px;color:#8b9b7c}.policy-loaded-history .text-button{font-size:10px}@media(max-width:760px){.policy-loaded-history{padding:10px 16px}}
.policy-checks{overflow:hidden;color:#344c43}.policy-heading{padding:25px 26px 20px;display:flex;justify-content:space-between;align-items:flex-start;gap:20px}.policy-kicker{font-size:9px;font-family:Consolas,monospace;letter-spacing:1.5px;color:#6e9381}.policy-heading h2{font:400 24px 'Noto Serif SC','SimSun',serif;margin:10px 0;color:#284b40}.policy-heading p{font-size:12px;line-height:1.9;color:#768c7f;margin:0}.policy-scope{padding:12px 26px;display:flex;flex-wrap:wrap;align-items:center;gap:12px 20px;border-top:1px solid #e3e9dd;background:#f6f8f1;font-size:10px;color:#7c907f}.policy-scope code{font-size:9px}.policy-scope .text-button{margin-left:auto}.policy-boundary{padding:16px 26px;margin:0;font-size:11px;color:#768778;line-height:1.9;border-bottom:1px solid #e6ebdf}.policy-message{margin:15px 24px}.policy-empty{padding:30px 26px;color:#81917c;font-size:12px}.policy-layout{display:grid;grid-template-columns:260px minmax(0,1fr)}.policy-history{padding:22px 18px;background:#fafbf6;border-right:1px solid #e5ebde;min-width:0}.policy-subheading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px}.policy-subheading h3{margin:0;font:400 16px 'Noto Serif SC','SimSun',serif;color:#3e5e4d}.policy-history .text-button{font-size:10px}.policy-history-item{display:flex;flex-direction:column;align-items:flex-start;gap:7px;background:transparent;border:1px solid #e2e8d9;border-radius:5px;width:100%;padding:14px;margin-bottom:9px;text-align:left;cursor:pointer;overflow-wrap:anywhere;color:#69805d}.policy-history-item.selected{background:#eef4e7;border-color:#9aad88;box-shadow:inset 3px 0 #6b8c65}.policy-history-item strong{font-weight:500;font-size:12px}.policy-history-item span{font-size:10px}.policy-history-item small{font-family:Consolas,monospace;font-size:9px;color:#99a68d}.policy-source-preview{margin-top:28px;border-top:1px solid #e3e8da;padding-top:18px}.policy-source-preview h3{font-size:12px;font-weight:500;margin:0 0 13px}.policy-record{display:grid;grid-template-columns:82px minmax(0,1fr);font-size:10px;max-height:360px;overflow:auto;background:#fff;border:1px solid #e0e6d7;margin-top:12px}.policy-record dt,.policy-record dd{padding:8px 7px;border-bottom:1px solid #edf0e6;overflow-wrap:anywhere}.policy-record dt{color:#8b9c81}.policy-record dd{margin:0;white-space:pre-wrap}.policy-content{min-width:0;padding:23px 25px 27px}.policy-form-top{display:grid;grid-template-columns:minmax(140px,.75fr) minmax(0,1.5fr);gap:16px}.policy-content label,.policy-field-label{font-size:11px;color:#7b8d70;display:block}.policy-content input:not([type=checkbox]),.policy-content select,.policy-content textarea{display:block;margin-top:7px;width:100%;min-width:0;font-size:12px;color:#3d5947}.policy-rule-help{background:#f5f8ef;padding:13px 15px;margin:18px 0;border-left:2px solid #92a777;font-size:11px;line-height:1.9;color:#72866c}.policy-rule-help p{margin:0 0 7px}.policy-rule-help a{display:inline-flex;align-items:center;gap:5px;color:#578065;text-decoration:none;margin-right:15px}.policy-rule-help span{font-size:10px;color:#93a183}.policy-rule-help details{margin-top:8px}.policy-rule-help summary{cursor:pointer}.policy-conditions{display:grid;gap:13px}.policy-content fieldset{min-width:0;border:1px solid #e2e8d9;border-radius:5px;margin:0;padding:13px 15px 14px}.policy-content legend{padding:0 7px;font-size:12px;color:#567353}.policy-condition-fields{display:grid;grid-template-columns:minmax(130px,.7fr) minmax(0,1.5fr);gap:14px;align-items:end}.policy-field-label{margin-bottom:7px}.policy-content .policy-confirm{display:flex;align-items:center;gap:8px;line-height:1.7;font-size:11px;color:#607b59;margin:12px 0 5px}.policy-confirm input{width:14px;height:14px;accent-color:#557e62;margin:0;flex-shrink:0}.policy-confirm input:disabled{opacity:.4}.policy-caption{font-size:10px;line-height:1.9;color:#99a48a;overflow-wrap:anywhere}.policy-extra{margin-top:18px!important}.policy-comment{margin-top:20px}.policy-final-confirm{padding:15px 0 5px;border-top:1px solid #e9ecdf;margin-top:20px!important}.policy-actions{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-top:14px}.policy-actions .button{font-size:11px}.policy-saved-facts{display:grid;grid-template-columns:minmax(140px,.8fr) minmax(0,1.5fr);font-size:12px;margin:0}.policy-saved-facts dt,.policy-saved-facts dd{padding:13px 0;border-bottom:1px solid #ecf0e4;overflow-wrap:anywhere}.policy-saved-facts dt{color:#819176}.policy-saved-facts dd{margin:0;color:#55704c}.policy-saved-facts small{display:block;font-size:10px;color:#99a28d;margin-top:5px}.policy-comment-text{font-size:12px;white-space:pre-wrap;color:#7d8f74;line-height:1.9}.policy-check-picker{margin-top:18px}.policy-result{margin-top:27px;padding:22px 21px;background:#fbfcf7;border:1px solid #dfe7d5;border-top:3px solid #648765;border-radius:5px;min-width:0}.policy-result header{display:flex;align-items:center;justify-content:space-between;gap:15px}.policy-result h3{font:400 21px 'Noto Serif SC','SimSun',serif;color:#3e6648;margin:9px 0}.policy-result>p{font-size:12px;line-height:1.9;color:#7b8d6f}.policy-treatments{display:grid;grid-template-columns:1fr 1fr;gap:15px;margin:19px 0;padding:15px 0;border-top:1px solid #e2e9d9;border-bottom:1px solid #e2e9d9}.policy-treatments span{display:block;font-size:10px;color:#91a081;margin-bottom:9px}.policy-treatments strong{font-weight:500;font-size:13px;line-height:1.8;color:#506b46;overflow-wrap:anywhere}.policy-result .policy-missing{background:#f4f1e4;padding:10px 12px;color:#8b8059}.policy-result-sources{font-size:11px;color:#7b9170;margin:18px 0}.policy-result-sources summary,.policy-fingerprints summary{cursor:pointer}.policy-result-sources>div{padding:12px 0;border-bottom:1px solid #e3e9d9;display:flex;flex-direction:column;gap:6px}.policy-result-sources strong{font-weight:500}.policy-result-sources span{font-size:10px;overflow-wrap:anywhere}.policy-result-sources code{font-size:9px;overflow-wrap:anywhere;color:#a1ab92}.policy-result-footer{display:flex;align-items:center;flex-wrap:wrap;gap:13px;font-size:10px;color:#97a184;margin:15px 0}.policy-result-footer a{color:#638661;text-decoration:none;display:flex;align-items:center;gap:4px}.policy-result-footer button{margin-left:auto;font-size:10px}.policy-fingerprints{font-size:10px;color:#90a07c;margin:18px 0}.policy-fingerprints dl{display:grid;grid-template-columns:70px minmax(0,1fr);gap:8px}.policy-fingerprints dd{margin:0;overflow-wrap:anywhere;font-family:Consolas,monospace;font-size:9px}.policy-checks button:focus-visible,.policy-checks summary:focus-visible,.policy-checks a:focus-visible{outline:2px solid #578168;outline-offset:3px}
@media(max-width:1100px){.policy-layout{grid-template-columns:210px minmax(0,1fr)}.policy-history{padding:20px 14px}.policy-content{padding:20px 18px}.policy-form-top{grid-template-columns:1fr}.policy-condition-fields{grid-template-columns:1fr}.policy-heading h2{font-size:22px}}
@media(max-width:760px){.policy-heading{padding:21px 16px;flex-direction:column;gap:10px}.policy-scope{padding:12px 16px;gap:9px 14px}.policy-scope .text-button{margin-left:0}.policy-boundary{padding:14px 16px}.policy-message{margin:12px 15px}.policy-layout{grid-template-columns:1fr}.policy-history{border-right:0;border-bottom:1px solid #e5ebde;padding:18px 16px}.policy-history-item{padding:12px}.policy-source-preview{margin-top:18px}.policy-content{padding:21px 16px}.policy-subheading{flex-wrap:wrap;gap:8px}.policy-condition-fields{gap:12px}.policy-content fieldset{padding:12px}.policy-saved-facts{grid-template-columns:minmax(100px,.8fr) minmax(0,1.5fr);font-size:11px}.policy-result{padding:17px 14px}.policy-result header{align-items:flex-start;flex-direction:column;gap:3px}.policy-result h3{font-size:20px}.policy-treatments{grid-template-columns:1fr;gap:18px}.policy-result-footer button{margin-left:0}.policy-result .notice{overflow-wrap:anywhere}.policy-record{max-height:260px}.policy-checks :deep(.n-base-selection){min-width:0}.policy-checks :deep(.n-base-selection-tag-wrapper){max-width:100%}}
</style>
