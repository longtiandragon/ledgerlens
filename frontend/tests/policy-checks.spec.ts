import { test, expect, type Page, type APIRequestContext, type Route } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const sha = 'a'.repeat(64)
const pack = { version: 'policy-consistency-v1', source_sha256: sha }
const predicates = ['ordinary_course', 'held_for_sale', 'economic_benefits_probable', 'cost_reliably_measurable']
const names = ['属于日常经营活动', '持有目的为出售', '经济利益很可能流入', '成本能够可靠计量']
const source = { source_id: 'mof-qa-3861813', url: 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/chzzsswd/202301/t20230103_3861813.htm', publication_date: '2023-01-03', source_sha256: sha, locator: 'div.TRS_Editor' }
const rule = { rule_id: 'POLICY_CAS_RESALE_INVENTORY', framework: 'CAS', topic: 'resale_inventory', title: '日常购进待售商品的存货确认', source_id: source.source_id, publication_date: source.publication_date, url: source.url, source_sha256: sha, applicable_conditions: '日常经营待售且确认经济利益及成本计量条件。', unsupported_conditions: ['非待售用途不在此规则范围内'], known_limits: ['不判断资本化或专业有效性'], treatment_captions: { inventory: '存货' }, source, required_conditions: predicates, version: pack.version, policy_rule_identity: pack, validation_status: 'experimental', status: 'experimental', reference_available_from: source.publication_date, effective_from: null, effective_to: null, scope_note: '归档问答的有限处理路径。' }
const records = [
  { id: 'r-A', evidence_id: 'ev-A', source_id: '订单-A', kind: 'order', amount: '9007199254740993.01', description: '采购条件原始单据A' },
  { id: 'r-B', evidence_id: 'ev-B', source_id: '订单-B', kind: 'order', amount: '200.00', description: '采购条件原始单据B' },
  { id: 'r-hidden', evidence_id: 'ev-hidden', source_id: '非快照来源', kind: 'order', amount: '999.00' },
]
const snapshot = (book = 'A') => ({ id: `snap-${book}`, book_id: `book-${book}`, created_at: '2026-10-02T00:00:00+00:00', record_ids: [`r-${book}`], policy: { framework: 'CAS', period: '2026-09' } })
const literalMismatch = { status: 'supported', expected_treatment: 'inventory', missing_conditions: [], reason: '已确认条件支持存货处理；所采用的固定资产处理存在差异。' }
const literalUnknown = { status: 'insufficient', expected_treatment: null, missing_conditions: predicates, reason: '必要业务条件未获人工确认，不能形成确定处理结论。' }
type ResultFixture = typeof literalMismatch | typeof literalUnknown
type Override = (route: Route, url: URL, method: string) => Promise<boolean>

async function mountPolicy(page: Page, request: APIRequestContext, options: { result?: ResultFixture; override?: Override; unsafeLink?: boolean } = {}) {
  const component = await request.get('/src/components/PolicyChecks.vue')
  expect(component.ok()).toBe(true)
  const vueImport = (await component.text()).match(/from\s+["']([^"']*\/vue\.js[^"']*)["']/)?.[1]
  expect(vueImport).toBeTruthy()
  const observed: { method: string; path: string; body?: Record<string, any> }[] = []
  const unexpected: string[] = []
  const errors: string[] = []
  const saved: Record<string, any>[] = []
  const checks: Record<string, any>[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/**', async route => {
    const incoming = route.request(), url = new URL(incoming.url()), method = incoming.method()
    observed.push({ method, path: url.pathname, body: incoming.postData() ? incoming.postDataJSON() : undefined })
    if (options.override && await options.override(route, url, method)) return
    if (method === 'GET' && url.pathname === '/api/v1/policy-checks/catalog') {
      await route.fulfill({ json: { policy_rule_identity: pack, rules: [{ ...rule, source: options.unsafeLink ? { ...source, url: 'javascript:alert(1)' } : source }], scope: 'six_source_bounded_consistency' } }); return
    }
    if (method === 'GET' && url.pathname.startsWith('/api/v1/snapshots/snap-')) {
      await route.fulfill({ json: snapshot(url.pathname.slice(-1)) }); return
    }
    const book = url.pathname.includes('/books/book-B/') ? 'B' : 'A'
    if (method === 'GET' && url.pathname.endsWith('/policy-observations')) {
      expect(url.searchParams.get('snapshot_id')).toBeNull()
      await route.fulfill({ json: saved.filter(item => item.book_id === `book-${book}`) }); return
    }
    if (method === 'GET' && url.pathname.endsWith('/policy-checks')) {
      expect(url.searchParams.get('snapshot_id')).toBeNull()
      await route.fulfill({ json: checks.filter(item => item.book_id === `book-${book}`) }); return
    }
    if (method === 'POST' && url.pathname.endsWith('/policy-observations')) {
      const body = incoming.postDataJSON(), existing = saved.filter(item => item.book_id === `book-${book}` && item.logical_key === body.logical_observation_key)
      existing.forEach(item => { item.latest = false; item.stale = true; checks.filter(check => check.observation_id === item.id).forEach(check => { check.stale = true }) })
      const id = `observation-${book}-${saved.length + 1}`
      const row = { id, created_at: '2026-10-09T08:00:00+00:00', book_id: `book-${book}`, snapshot_id: `snap-${book}`, logical_key: body.logical_observation_key, version: existing.length + 1, parent_version_id: body.parent_version_id, payload: { observation_id: id, book_id: `book-${book}`, snapshot_id: `snap-${book}`, topic: body.topic, conditions: body.conditions, beneficiary: body.beneficiary, beneficiary_confirmation: body.beneficiary_confirmation, beneficiary_evidence_ids: body.beneficiary_evidence_ids, reported_treatment: body.reported_treatment, treatment_evidence_ids: body.treatment_evidence_ids }, digest: sha, comment: body.comment, latest: true, stale: false, confirmation_notice: 'Local review assertion', source_cutoff: snapshot(book).created_at, annotation_recorded_at: '2026-10-09T08:00:00+00:00' }
      saved.push(row); await route.fulfill({ status: 201, json: row }); return
    }
    if (method === 'POST' && /\/policy-observations\/[^/]+\/check$/.test(url.pathname)) {
      const id = url.pathname.split('/').at(-2), row = saved.find(item => item.id === id)!
      const runId = `check-${row.id}`
      const run = { id: runId, created_at: '2026-10-09T08:05:00+00:00', book_id: row.book_id, snapshot_id: row.snapshot_id, observation_id: row.id, pack_key: `${pack.version}:${sha}`, observation_digest: sha, artifact_digest: sha, stale: false, pack_compatible: true, historical_result: true, ledger_writes: 0, notice: 'Bounded consistency only',
        result: { ...(options.result ?? literalMismatch), rule_id: rule.rule_id, title: rule.title, reported_treatment: row.payload.reported_treatment, evidence_ids: options.result === literalUnknown ? [] : [`ev-${book}`], policy_rule_identity: pack, source, scope_note: '归档问答有限路径，不代表真实企业业务准确率。' },
        binding: { ledger_snapshot_id: row.snapshot_id, ledger_snapshot_digest: sha, book_revision: 1, framework: 'CAS', period: '2026-09', source_cutoff: snapshot(book).created_at, observation_version: row.version, observation_id: row.id, observation_digest: sha, annotation_recorded_at: row.created_at, annotation_envelope: Object.fromEntries(['id', 'created_at', 'book_id', 'snapshot_id', 'logical_key', 'version', 'parent_version_id', 'payload', 'comment'].map(key => [key, row[key]])), policy_rule_identity: pack, observation: row.payload, evidence_locators: options.result === literalUnknown ? [] : [{ evidence_id: `ev-${book}`, record_id: `r-${book}`, source_id: `订单-${book}`, document_id: `doc-${book}`, document_name: `来源-${book}.json`, document_sha256: sha, synthetic: true, locator: { row: 1 }, record_fingerprint: sha, source_known_at: '2026-10-01T00:00:00+00:00', confirmed_import_ids: [`import-${book}`] }], result_sha256: sha, confirmation_scope: 'Local assertion' },
      }
      checks.push(run); await route.fulfill({ json: run }); return
    }
    if (method === 'GET' && /\/policy-checks\/[^/]+\/export$/.test(url.pathname)) {
      const run = checks.find(item => item.id === url.pathname.split('/').at(-2))
      await route.fulfill({ json: run }); return
    }
    unexpected.push(`${method} ${url.pathname}`); await route.abort()
  })
  await page.route('**/__component-test-policy', route => route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><button id="switch-book" type="button">切换到B账套</button><div id="component-root" style="max-width:1200px;margin:auto;padding:14px"></div><script type="module">
    import {createApp,h,ref} from ${JSON.stringify(vueImport)};
    import '/src/style.css';import PolicyChecks from '/src/components/PolicyChecks.vue';
    const props=ref({bookId:'book-A',snapshotId:'snap-A',framework:'CAS',records:${JSON.stringify(records)}});
    document.getElementById('switch-book').onclick=()=>{props.value={...props.value,bookId:'book-B',snapshotId:'snap-B'}};
    createApp({render:()=>h(PolicyChecks,props.value)}).mount('#component-root');
    </script></html>` }))
  await page.goto('/__component-test-policy')
  await expect(page.getByRole('heading', { name: '先确认条件，再核对处理。' })).toBeVisible()
  return { observed, unexpected, errors, saved, checks }
}
async function ready(page: Page) { await expect(page.getByLabel('检查名称')).toBeVisible() }
async function selectEvidence(page: Page, field: ReturnType<Page['locator']>, book = 'A') {
  await field.locator('.n-base-selection').click()
  await page.locator('.n-base-select-menu:visible .n-base-select-option').filter({ hasText: `订单-${book}` }).last().click()
  await expect(field.locator('.n-tag')).toContainText(`订单-${book}`)
  await page.getByRole('heading', { name: '先确认条件，再核对处理。' }).click()
  await expect(page.locator('.n-base-select-menu:visible')).toHaveCount(0)
}
async function confirmAll(page: Page) {
  for (let i = 0; i < predicates.length; i++) {
    const field = page.locator(`[data-predicate="${predicates[i]}"]`)
    await field.getByLabel(`${names[i]}判断`).selectOption('yes')
    await selectEvidence(page, field)
    await field.getByRole('checkbox', { name: `人工确认${names[i]}` }).check()
  }
}
async function saveAndCheck(page: Page) {
  await page.getByRole('checkbox', { name: '确认提交本次条件版本' }).check()
  await page.getByRole('button', { name: /保存条件版本|保存修订版本/ }).click()
  await expect(page.getByRole('button', { name: '执行政策检查' })).toBeEnabled()
  await page.getByRole('button', { name: '执行政策检查' }).click()
  await expect(page.getByLabel('政策检查结果')).toBeVisible()
}
function clean(observed: Awaited<ReturnType<typeof mountPolicy>>) { expect(observed.unexpected).toEqual([]); expect(observed.errors).toEqual([]) }

test('v4及v5新增政策的条件以中文呈现，切换主题不继承人工确认', async ({ page, request }) => {
  const response = await request.get('/api/v1/policy-checks/catalog')
  expect(response.ok()).toBe(true)
  const catalog = await response.json()
  expect(catalog.policy_rule_identity.version).toBe('policy-consistency-v9')
  const seen = await mountPolicy(page, request, { override: async (route, url, method) => {
    if (method === 'GET' && url.pathname === '/api/v1/policy-checks/catalog') {
      await route.fulfill({ json: catalog }); return true
    }
    return false
  } })
  await page.setViewportSize({ width: 390, height: 844 })
  await ready(page)
  for (const topic of ['group_share_payment', 'debt_release_timing', 'initial_revenue_ecl', 'rent_subsidy', 'voluntary_share_cancellation',
    'renovation_useful_life', 'renovation_replaced_part', 'data_resource_development', 'equity_method_impairment',
    'goodwill_cgu_allocation', 'debt_restructuring_asset_measurement', 'debt_to_equity_classification',
    'construction_progress_grant', 'custom_equipment_borrowing_cost', 'land_use_right_borrowing_cost',
    'non_exchangeable_currency_rate', 'one_year_lease_term', 'prior_period_error_materiality']) {
    await page.getByLabel('业务主题').selectOption(topic)
    const fields = page.locator('[data-predicate]')
    const current = catalog.rules.find((item: any) => item.topic === topic)
    await expect(fields).toHaveCount(current.required_conditions.length)
    for (const field of await fields.all()) {
      await expect(field.locator('legend')).toContainText(/[\u4e00-\u9fff]/)
      await expect(field.locator('select')).toHaveValue('unknown')
      await expect(field.getByRole('checkbox')).toBeDisabled()
    }
    await expect(page.getByRole('checkbox', { name: '确认提交本次条件版本' })).not.toBeChecked()
    await expect(page.getByRole('button', { name: '保存条件版本' })).toBeDisabled()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  }
  expect(seen.observed.filter(item => item.method === 'POST')).toHaveLength(0)
  clean(seen)
})

test('未知条件不自动确认，必须显式提交；证据不足不会显示处理一致', async ({ page, request }) => {
  const seen = await mountPolicy(page, request, { result: literalUnknown })
  await ready(page)
  for (const name of names) { await expect(page.getByLabel(`${name}判断`)).toHaveValue('unknown'); await expect(page.getByRole('checkbox', { name: `人工确认${name}` })).toBeDisabled() }
  await expect(page.getByRole('button', { name: '保存条件版本' })).toBeDisabled()
  expect(seen.observed.filter(item => item.method === 'POST')).toHaveLength(0)
  await saveAndCheck(page)
  await expect(page.getByLabel('政策检查结果')).toContainText('证据不足')
  await expect(page.getByLabel('政策检查结果')).not.toContainText('处理一致')
  const body = seen.observed.find(item => item.method === 'POST')!.body!
  expect(body.human_confirmation).toBe(true)
  expect(body.reported_treatment).toBe('unknown')
  predicates.forEach(name => expect(body.conditions[name]).toEqual({ value: 'unknown', confirmation: 'unconfirmed', evidence_ids: [] }))
  for (const key of ['book_id', 'framework', 'context', 'as_of']) expect(body).not.toHaveProperty(key)
  clean(seen)
})

test('逐项人工确认与真实快照来源绑定，差异底稿显示独立预期处理和来源', async ({ page, request }) => {
  const seen = await mountPolicy(page, request)
  await ready(page)
  await page.getByLabel('检查名称').fill('待售采购复核')
  await confirmAll(page)
  await page.getByLabel('已采用的处理').selectOption('fixed_asset')
  await selectEvidence(page, page.locator('.policy-extra').last())
  await saveAndCheck(page)
  const result = page.getByLabel('政策检查结果')
  await expect(result).toContainText('存在有依据的差异')
  await expect(result.locator('.policy-treatments')).toContainText('固定资产')
  await expect(result.locator('.policy-treatments')).toContainText('存货')
  await result.getByText('1 条已定位来源', { exact: true }).click()
  await expect(result).toContainText('来源-A.json')
  await expect(result).toContainText('合成资料')
  await result.getByText('快照与执行版本', { exact: true }).click()
  await expect(result).toContainText('snap-A')
  const posted = seen.observed.find(item => item.method === 'POST')!.body!
  expect(posted.snapshot_id).toBe('snap-A')
  predicates.forEach(name => expect(posted.conditions[name]).toEqual({ value: 'yes', confirmation: 'human_confirmed', evidence_ids: ['ev-A'] }))
  expect(posted.treatment_evidence_ids).toEqual(['ev-A'])
  await expect(result.getByRole('link', { name: '财政部依据' })).toHaveAttribute('href', source.url)
  clean(seen)
})

test('修订必须重新确认并追加版本，旧底稿只读且导出保留原结果', async ({ page, request }) => {
  const seen = await mountPolicy(page, request)
  await ready(page); await confirmAll(page)
  await page.getByLabel('已采用的处理').selectOption('fixed_asset')
  await selectEvidence(page, page.locator('.policy-extra').last())
  await saveAndCheck(page)
  await page.getByRole('button', { name: '修订条件', exact: true }).click()
  await expect(page.getByRole('checkbox', { name: '确认提交本次条件版本' })).not.toBeChecked()
  for (const name of names) await expect(page.getByRole('checkbox', { name: `人工确认${name}` })).not.toBeChecked()
  await expect(page.getByRole('button', { name: '保存修订版本' })).toBeDisabled()
  for (const name of names) await page.getByRole('checkbox', { name: `人工确认${name}` }).check()
  await saveAndCheck(page)
  expect(seen.saved).toHaveLength(2)
  expect(seen.saved[1]!.parent_version_id).toBe(seen.saved[0]!.id)
  expect(seen.saved[1]!.version).toBe(2)
  await page.locator('.policy-history-item').filter({ hasText: '版本 1' }).click()
  await expect(page.getByRole('button', { name: '执行政策检查' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '修订条件', exact: true })).toBeDisabled()
  const downloading = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出政策底稿' }).click()
  const downloaded = await downloading
  const output = JSON.parse(await readFile((await downloaded.path())!, 'utf8'))
  expect(output.id).toBe('check-observation-A-1')
  expect(output.result.status).toBe('supported')
  expect(output.binding.observation_version).toBe(1)
  expect(output.stale).toBe(true)
  clean(seen)
})

test('来源列表只含当前快照成员，原金额字符串保持精度且不执行不安全链接', async ({ page, request }) => {
  const seen = await mountPolicy(page, request, { unsafeLink: true })
  await ready(page)
  await expect(page.locator('.policy-rule-help').getByRole('link')).toHaveCount(0)
  await page.locator('.policy-source-preview .n-base-selection').click()
  await expect(page.locator('.n-base-select-option').filter({ hasText: '非快照来源' })).toHaveCount(0)
  await expect(page.locator('.n-base-select-option').filter({ hasText: '订单-B' })).toHaveCount(0)
  await page.locator('.n-base-select-option').filter({ hasText: '订单-A' }).click()
  await expect(page.getByLabel('所选原始记录')).toContainText('9007199254740993.01')
  clean(seen)
})

test('切换账套后迟到快照被丢弃，表单和证据只使用新范围', async ({ page, request }) => {
  let release!: () => void
  const waiting = new Promise<void>(resolve => { release = resolve })
  let held = false
  const seen = await mountPolicy(page, request, { override: async (route, url) => {
    if (url.pathname.endsWith('/snapshots/snap-A')) { held = true; await waiting; await route.fulfill({ json: snapshot('A') }).catch(() => {}); return true }
    return false
  } })
  await expect.poll(() => held).toBe(true)
  await page.getByRole('button', { name: '切换到B账套' }).click()
  await ready(page)
  release()
  await expect(page.locator('.policy-scope')).toContainText('snap-B')
  await expect(page.locator('.policy-scope')).not.toContainText('snap-A')
  for (const name of names) await expect(page.getByLabel(`${name}判断`)).toHaveValue('unknown')
  await page.locator('.policy-source-preview .n-base-selection').click()
  await expect(page.locator('.n-base-select-option').filter({ hasText: '订单-A' })).toHaveCount(0)
  await expect(page.locator('.n-base-select-option').filter({ hasText: '订单-B' })).toHaveCount(1)
  clean(seen)
})

test('390像素下条件、确认按钮与长金额可访问，页面不横向溢出', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const seen = await mountPolicy(page, request)
  await ready(page)
  await expect(page.getByLabel('属于日常经营活动判断')).toBeVisible()
  await page.getByRole('checkbox', { name: '确认提交本次条件版本' }).check()
  await expect(page.getByRole('button', { name: '保存条件版本' })).toBeEnabled()
  const size = await page.evaluate(() => ({ content: document.documentElement.scrollWidth, viewport: window.innerWidth }))
  expect(size.content).toBeLessThanOrEqual(size.viewport + 1)
  await page.screenshot({ path: '../runtime/policy-checks-mobile-v1.png', fullPage: true })
  clean(seen)
})

// A literal prior-snapshot artifact. Its evidence is deliberately absent from
// the current snapshot, so history must not graft current sources onto it.
const priorObservation = {
  id: 'prior-observation', created_at: '2026-09-01T00:00:00+00:00', book_id: 'book-A', snapshot_id: 'prior-snapshot',
  logical_key: '上月采购复核', version: 1, parent_version_id: null, comment: '历史条件', digest: sha,
  latest: true, stale: true, confirmation_notice: 'Local review assertion', source_cutoff: '2026-09-01T00:00:00+00:00', annotation_recorded_at: '2026-09-01T00:00:00+00:00',
  payload: { observation_id: 'prior-observation', book_id: 'book-A', snapshot_id: 'prior-snapshot', topic: 'resale_inventory',
    conditions: { ordinary_course: { value: 'yes', confirmation: 'human_confirmed', evidence_ids: ['prior-evidence'] },
      held_for_sale: { value: 'yes', confirmation: 'human_confirmed', evidence_ids: ['prior-evidence'] },
      economic_benefits_probable: { value: 'yes', confirmation: 'human_confirmed', evidence_ids: ['prior-evidence'] },
      cost_reliably_measurable: { value: 'yes', confirmation: 'human_confirmed', evidence_ids: ['prior-evidence'] } },
    beneficiary: 'unknown', beneficiary_confirmation: 'unconfirmed', beneficiary_evidence_ids: [], reported_treatment: 'fixed_asset', treatment_evidence_ids: ['prior-evidence'] },
}
const priorCheck = {
  id: 'prior-check', created_at: '2026-09-01T01:00:00+00:00', book_id: 'book-A', snapshot_id: 'prior-snapshot', observation_id: 'prior-observation', pack_key: `${pack.version}:${sha}`, observation_digest: sha, artifact_digest: sha,
  stale: true, pack_compatible: true, historical_result: true, ledger_writes: 0, notice: 'Frozen prior artifact',
  result: { ...literalMismatch, rule_id: rule.rule_id, title: rule.title, reported_treatment: 'fixed_asset', evidence_ids: ['prior-evidence'], policy_rule_identity: pack, source, scope_note: '独立界面测试的历史底稿，不衡量规则或业务准确率。' },
  binding: { ledger_snapshot_id: 'prior-snapshot', ledger_snapshot_digest: sha, book_revision: 1, framework: 'CAS', period: '2026-08', source_cutoff: '2026-09-01T00:00:00+00:00', observation_version: 1, observation_id: 'prior-observation', observation_digest: sha, annotation_recorded_at: '2026-09-01T00:00:00+00:00', annotation_envelope: priorObservation, policy_rule_identity: pack, observation: priorObservation.payload, result_sha256: sha, confirmation_scope: 'Local assertion', evidence_locators: [{ evidence_id: 'prior-evidence', record_id: 'prior-record', source_id: '上月原始资料', document_id: 'prior-document', document_name: '上月资料.json', document_sha256: sha, synthetic: true, locator: { row: 9 }, record_fingerprint: sha, source_known_at: '2026-08-31T00:00:00+00:00', confirmed_import_ids: ['prior-import'] }] },
}

test('数据更新后旧快照条件和底稿仍可读导出，不能用当前来源修改或重查', async ({ page, request }) => {
  const seen = await mountPolicy(page, request, { override: async (route, url, method) => {
    if (method === 'GET' && url.pathname.endsWith('/policy-observations')) { expect(url.searchParams.get('snapshot_id')).toBeNull(); await route.fulfill({ json: [priorObservation] }); return true }
    if (method === 'GET' && url.pathname.endsWith('/policy-checks')) { await route.fulfill({ json: [priorCheck] }); return true }
    if (method === 'GET' && url.pathname.endsWith('/prior-check/export')) { await route.fulfill({ json: priorCheck }); return true }
    return false
  } })
  await ready(page)
  await page.locator('.policy-history-item').filter({ hasText: '上月采购复核' }).click()
  await expect(page.locator('.policy-historical-snapshot')).toContainText('prior-snapshot')
  await expect(page.getByRole('button', { name: '执行政策检查' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '修订条件', exact: true })).toBeDisabled()
  await page.getByText('1 条已定位来源', { exact: true }).click()
  await expect(page.getByLabel('政策检查结果')).toContainText('上月资料.json')
  await expect(page.getByLabel('政策检查结果')).not.toContainText('订单-A')
  const downloading = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出政策底稿' }).click()
  const downloaded = await downloading
  const output = JSON.parse(await readFile((await downloaded.path())!, 'utf8'))
  expect(output.snapshot_id).toBe('prior-snapshot')
  expect(output.result.evidence_ids).toEqual(['prior-evidence'])
  expect(seen.observed.every(item => item.method === 'GET')).toBe(true)
  clean(seen)
})

test('200条历史页不会冒充完整列表，继续加载使用显式offset与limit', async ({ page, request }) => {
  const history = Array.from({ length: 201 }, (_, index) => ({ ...priorObservation, id: `prior-${index}`, logical_key: `历史检查-${index}`, payload: { ...priorObservation.payload, observation_id: `prior-${index}` } }))
  const seen = await mountPolicy(page, request, { override: async (route, url, method) => {
    if (method === 'GET' && url.pathname.endsWith('/policy-observations')) {
      expect(url.searchParams.get('snapshot_id')).toBeNull()
      const offset = Number(url.searchParams.get('offset') ?? '0'), limit = Number(url.searchParams.get('limit'))
      expect(limit).toBe(200); expect([0, 200]).toContain(offset)
      await route.fulfill({ json: history.slice(offset, offset + limit) }); return true
    }
    return false
  } })
  await ready(page)
  await expect(page.getByLabel('政策历史加载范围')).toContainText('200 个条件版本')
  await expect(page.getByLabel('政策历史加载范围')).toContainText('列表尚未全部载入')
  await page.getByRole('button', { name: '加载更多条件版本' }).click()
  await expect(page.getByLabel('政策历史加载范围')).toContainText('201 个条件版本')
  await expect(page.getByRole('button', { name: '加载更多条件版本' })).toHaveCount(0)
  await expect(page.locator('.policy-history-item').filter({ hasText: '历史检查-200' })).toBeVisible()
  clean(seen)
})

test('分页期间新增版本不会推进服务器cursor，后续页合并而不跳过旧记录', async ({ page, request }) => {
  const history: Record<string, any>[] = Array.from({ length: 201 }, (_, index) => ({ ...priorObservation, id: `prior-${index}`, logical_key: `历史检查-${index}`, payload: { ...priorObservation.payload, observation_id: `prior-${index}` } }))
  const offsets: number[] = []
  const seen = await mountPolicy(page, request, { override: async (route, url, method) => {
    if (method === 'GET' && url.pathname.endsWith('/policy-observations')) {
      const offset = Number(url.searchParams.get('offset') ?? '0'), limit = Number(url.searchParams.get('limit'))
      offsets.push(offset); await route.fulfill({ json: history.slice(offset, offset + limit) }); return true
    }
    if (method === 'POST' && url.pathname.endsWith('/policy-observations')) {
      const body = route.request().postDataJSON()
      const added = { ...priorObservation, id: 'new-observation', snapshot_id: 'snap-A', logical_key: body.logical_observation_key, created_at: '2026-10-09T10:00:00+00:00', stale: false,
        payload: { observation_id: 'new-observation', book_id: 'book-A', snapshot_id: 'snap-A', topic: body.topic, conditions: body.conditions, reported_treatment: body.reported_treatment, treatment_evidence_ids: body.treatment_evidence_ids } }
      history.push(added); await route.fulfill({ status: 201, json: added }); return true
    }
    return false
  } })
  await ready(page)
  await page.getByLabel('检查名称').fill('本次新增')
  await page.getByRole('checkbox', { name: '确认提交本次条件版本' }).check()
  await page.getByRole('button', { name: '保存条件版本' }).click()
  await expect(page.getByLabel('政策历史加载范围')).toContainText('201 个条件版本')
  await page.getByRole('button', { name: '加载更多条件版本' }).click()
  await expect(page.getByLabel('政策历史加载范围')).toContainText('202 个条件版本')
  expect(offsets).toEqual([0, 200])
  await expect(page.locator('.policy-history-item').filter({ hasText: '历史检查-200' })).toBeVisible()
  await expect(page.locator('.policy-history-item').filter({ hasText: '本次新增' })).toHaveCount(1)
  await expect(page.getByRole('alert')).toHaveCount(0)
  clean(seen)
})
