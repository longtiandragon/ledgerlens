import { test, expect } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

test('未启用本地索引时保留默认关键词与明确提示', async ({ page, request }) => {
  const book = await (await request.post('/api/v1/books', { data: { name: `参考默认-${Date.now()}`, framework: 'CAS', period: '2026-09' } })).json()
  await page.route('**/api/v1/reference-indexes/capabilities', route => route.fulfill({ json: { available: false, indexes: [] } }))
  await page.goto(`/?book=${book.id}`)
  await page.getByRole('button', { name: '账套与政策', exact: true }).click()
  const panel = page.getByRole('region', { name: '参考条文选择' })
  await expect(panel.getByText('默认关键词检索', { exact: true })).toBeVisible()
  await expect(panel.getByText('本机未启用实验索引。', { exact: false })).toBeVisible()
  await expect(panel.getByRole('button', { name: '查询本地参考' })).toHaveCount(0)
})

test('切换检索记录后忽略上一任务迟到的轮询结果', async ({ page, request }) => {
  const book = await (await request.post('/api/v1/books', { data: { name: `参考切换-${Date.now()}`, framework: 'CAS', period: '2026-09' } })).json()
  const runs = [{ id: 'older', query: '旧查询', status: 'running' }, { id: 'newer', query: '新查询', status: 'complete' }]
  await page.route('**/api/v1/reference-indexes/searches?*', route => route.fulfill({ json: runs }))
  let release!: () => void, entered!: () => void, visits = 0
  const delayed = new Promise<void>(resolve => { release = resolve })
  const waiting = new Promise<void>(resolve => { entered = resolve })
  await page.route('**/api/v1/reference-indexes/searches/older?*', async route => {
    visits++
    if (visits > 1) { entered(); await delayed }
    await route.fulfill({ json: { ...runs[0], status: visits > 1 ? 'complete' : 'running', stale: false, result: { hits: [] } } })
  })
  await page.route('**/api/v1/reference-indexes/searches/newer?*', route => route.fulfill({ json: { ...runs[1], stale: false, result: { hits: [] } } }))
  await page.goto(`/?book=${book.id}`)
  await page.getByRole('button', { name: '账套与政策', exact: true }).click()
  const panel = page.getByRole('region', { name: '参考条文选择' })
  await panel.getByLabel('本账套检索记录').selectOption('older')
  await waiting
  await panel.getByLabel('本账套检索记录').selectOption('newer')
  await expect(panel.locator('.result-heading strong')).toContainText('新查询')
  const returned = page.waitForResponse(r => r.url().includes('/searches/older?'))
  release(); await returned
  await expect(panel.locator('.result-heading strong')).toContainText('新查询')
  await expect(panel.getByText('没有符合当前账套范围的候选条文。', { exact: true })).toBeVisible()
})

test('候选原文的片语覆盖不能替代企业事实核实', async ({ page, request }) => {
  const book = await (await request.post('/api/v1/books', { data: { name: `参考证据需求-${Date.now()}`, framework: 'CAS', period: '2026-09' } })).json()
  const parent = { source_id: 'fixture-parent', text: '采购成本需要与原始凭证核对。',
    source_binding: { metadata: { title: '合成条文，仅用于界面回归', url: 'https://example.com/fixture' }, page: null, offset: 0, end: 15 } }
  const run = { id: 'support-fixture', query: '采购成本', status: 'complete', stale: false, result_digest: 'a'.repeat(64),
    result: { hits: [{ parent_chunk_id: parent.source_id, score: 0.99, text: parent.text, parent_reference: parent }] },
    answer_boundary: { answer_status: 'not_answered', notice: '候选仅供人工查阅，不判断企业事实。' } }
  await page.route('**/api/v1/reference-indexes/searches?*', route => route.fulfill({ json: [run] }))
  await page.route('**/api/v1/reference-indexes/searches/support-fixture?*', route => route.fulfill({ json: run }))
  const sent: any[] = []
  await page.route('**/api/v1/reference-indexes/support-checks', async route => {
    const body = route.request().postDataJSON(), requirement = body.requirements[0]
    sent.push(body)
    const literal = requirement.kind === 'policy_excerpt'
    await route.fulfill({ json: { decision: literal ? 'manual_review' : 'insufficient_evidence',
      notice: '没有生成业务答案', requirements: [{ ...requirement,
        status: literal ? 'literal_match' : 'unsupported_request',
        missing_evidence: literal ? [] : ['当前快照中的原始合同、票据或交易记录'],
        matches: literal ? [{ parent_id: parent.source_id, start: 0, end: 4, text: '采购成本' }] : [] }] } })
  })
  await page.goto(`/?book=${book.id}`)
  await page.getByRole('button', { name: '账套与政策', exact: true }).click()
  const panel = page.getByRole('region', { name: '参考条文选择' })
  await panel.getByLabel('本账套检索记录').selectOption(run.id)
  await expect(panel.getByText('未生成答案。候选仅供人工查阅，不判断企业事实。')).toBeVisible()
  await panel.getByLabel('本次需要核对什么').selectOption('business_fact')
  await panel.locator('.support-form textarea').fill('我司这笔采购实收数量')
  await panel.getByRole('button', { name: '检查证据需求' }).click()
  await expect(panel.getByText('证据不足，暂不能形成答案')).toBeVisible()
  await expect(panel.getByText('需要：当前快照中的原始合同、票据或交易记录')).toBeVisible()
  await panel.getByLabel('本次需要核对什么').selectOption('policy_excerpt')
  await expect(panel.getByText('证据不足，暂不能形成答案')).toHaveCount(0)
  await panel.locator('.support-form textarea').fill('采购成本')
  await panel.getByRole('button', { name: '检查证据需求' }).click()
  await expect(panel.getByText('已找到原文片语，仍需人工复核')).toBeVisible()
  await expect(panel.getByText('逐字命中「采购成本」', { exact: false })).toBeVisible()
  await expect(panel.getByRole('button', { name: /冻结所选条文/ })).toBeDisabled()
  expect(sent.map(body => body.requirements[0].kind)).toEqual(['business_fact', 'policy_excerpt'])
  expect(sent.every(body => body.book_id === book.id && body.search_id === run.id && body.expected_result_digest === run.result_digest)).toBe(true)
})

test('新快照会立即清除选择并丢弃迟到的证据需求结果', async ({ page, request }) => {
  const book = await (await request.post('/api/v1/books', { data: { name: `参考快照切换-${Date.now()}`, framework: 'CAS', period: '2026-09' } })).json()
  const original = await (await request.post(`/api/v1/books/${book.id}/demo`)).json()
  const parent = { source_id: 'snapshot-parent', text: '采购成本需要与原始凭证核对。',
    source_binding: { metadata: { title: '合成条文，仅用于竞态回归', url: 'https://example.com/fixture' }, page: null, offset: 0, end: 15 } }
  const run = { id: 'snapshot-support-fixture', query: '采购成本', status: 'complete', stale: false, result_digest: 'a'.repeat(64),
    result: { hits: [{ parent_chunk_id: parent.source_id, score: 0.99, text: parent.text, parent_reference: parent }] } }
  let stale = false, checks = 0, choiceWrites = 0
  let release!: () => void, entered!: () => void, finished!: () => void
  const delayed = new Promise<void>(resolve => { release = resolve })
  const waiting = new Promise<void>(resolve => { entered = resolve })
  const returned = new Promise<void>(resolve => { finished = resolve })
  page.on('request', call => { if (call.method() === 'POST' && call.url().endsWith('/reference-indexes/choices')) choiceWrites++ })
  await page.route('**/api/v1/reference-indexes/capabilities', route => route.fulfill({ json: { available: false, indexes: [] } }))
  await page.route('**/api/v1/reference-indexes/searches?*', route => route.fulfill({ json: [run] }))
  await page.route('**/api/v1/reference-indexes/searches/snapshot-support-fixture?*', route => route.fulfill({ json: { ...run, stale } }))
  await page.route('**/api/v1/reference-indexes/support-checks', async route => {
    const delayedCheck = ++checks === 2
    if (delayedCheck) { entered(); await delayed }
    try {
      await route.fulfill({ json: { decision: 'manual_review', notice: '没有生成业务答案', requirements: [{
        kind: 'policy_excerpt', text: '采购成本', status: 'literal_match', missing_evidence: [],
        matches: [{ parent_id: parent.source_id, start: 0, end: 4, text: '采购成本' }] }] } })
    } finally { if (delayedCheck) finished() }
  })
  await page.goto(`/?book=${book.id}`)
  await page.getByRole('button', { name: '账套与政策', exact: true }).click()
  const panel = page.getByRole('region', { name: '参考条文选择' })
  await panel.getByLabel('本账套检索记录').selectOption(run.id)
  await panel.getByLabel('本次需要核对什么').selectOption('policy_excerpt')
  await panel.locator('.support-form textarea').fill('采购成本')
  await panel.getByRole('button', { name: '检查证据需求' }).click()
  await expect(panel.getByText('已找到原文片语，仍需人工复核')).toBeVisible()
  await panel.locator('.reference-card input[type="checkbox"]').check()
  await panel.getByLabel('我已阅读所选条文并核对适用范围', { exact: false }).check()
  await expect(panel.getByRole('button', { name: /冻结所选条文/ })).toBeEnabled()
  await panel.getByRole('button', { name: '检查证据需求' }).click()
  await waiting
  try {
    // Another client adds material while this component is waiting for a check.
    const changed = await request.post(`/api/v1/books/${book.id}/supplement-demo`)
    expect(changed.ok()).toBe(true)
    expect((await changed.json()).snapshot_id).not.toBe(original.snapshot_id)
    stale = true
    await page.getByRole('button', { name: '刷新数据', exact: true }).click()
    await expect(panel.getByLabel('本账套检索记录')).toHaveValue('')
    await expect(panel.locator('.reference-card')).toHaveCount(0)
    await expect(panel.getByRole('button', { name: /冻结所选条文/ })).toHaveCount(0)
  } finally { release() }
  await returned
  await expect(panel.getByText('已找到原文片语，仍需人工复核')).toHaveCount(0)
  await expect(panel.getByLabel('本账套检索记录')).toHaveValue('')
  await panel.getByLabel('本账套检索记录').selectOption(run.id)
  await expect(panel.getByText('检索后账套已有变化；请重新查询后选择。')).toBeVisible()
  await expect(panel.getByRole('button', { name: '检查证据需求' })).toHaveCount(0)
  await expect(panel.getByRole('button', { name: /冻结所选条文/ })).toHaveCount(0)
  expect(choiceWrites).toBe(0)
})

test('真实本地 BGE 查询、人工冻结、规则调查和恢复默认', async ({ page, request }, info) => {
  test.skip(process.env.REFERENCE_LOCAL_TESTS !== '1', '需启用已冻结本地 BGE 与持久 pgvector，无付费请求')
  test.setTimeout(180000)
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  expect((await (await request.get('/api/v1/reference-indexes/capabilities')).json()).available).toBe(true)
  const book = await (await request.post('/api/v1/books', { data: { name: `参考索引合成验收-${Date.now()}`, framework: 'CAS', period: '2026-09' } })).json()
  const demo = await (await request.post(`/api/v1/books/${book.id}/demo`)).json()
  const beforeRaw = await (await request.get(`/api/v1/snapshots/${demo.snapshot_id}`)).text()
  const recordsRaw = await (await request.get(`/api/v1/books/${book.id}/records`)).text()
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`/?book=${book.id}`)
  await page.getByRole('button', { name: '账套与政策', exact: true }).click()
  const panel = page.getByRole('region', { name: '参考条文选择' })
  await panel.getByLabel('要查阅的政策问题').fill('存货采购成本包含哪些费用')
  const started = page.waitForResponse(r => r.url().endsWith('/reference-indexes/searches') && r.request().method() === 'POST')
  await panel.getByRole('button', { name: '查询本地参考' }).click()
  const start = await started
  expect(start.status()).toBe(202)
  const runId = (await start.json()).id
  let result: any
  await expect.poll(async () => {
    result = await (await request.get(`/api/v1/reference-indexes/searches/${runId}?book_id=${book.id}`)).json()
    return result.status
  }, { timeout: 130000, intervals: [1500, 2500] }).toBe('complete')
  expect(result.result.paid_model_requests).toBe(0)
  expect(result.result.ledger_writes).toBe(0)
  expect(result.result.verified_segments).toBe(156)
  expect((await (await request.get(`/api/v1/books/${book.id}/overview`)).json()).latest_snapshot_id).toBe(demo.snapshot_id)
  await expect(panel.locator('.reference-card').first()).toBeVisible()
  await expect(panel.getByRole('button', { name: /冻结所选条文/ })).toBeDisabled()
  const card = panel.locator('.reference-card').first()
  await card.locator('summary').click()
  await expect(card.locator('.reference-text')).toContainText('采购成本')
  await expect(card.locator('.locator')).toContainText('字符 0–800')
  await card.getByRole('checkbox').check()
  await expect(panel.getByRole('button', { name: /冻结所选条文/ })).toBeDisabled()
  await panel.getByLabel('我已阅读所选条文并核对适用范围', { exact: false }).check()
  await panel.screenshot({ path: info.outputPath('reference-candidates-desktop.png') })
  const frozen = page.waitForResponse(r => r.url().endsWith('/reference-indexes/choices') && r.request().method() === 'POST')
  await panel.getByRole('button', { name: /冻结所选条文/ }).click()
  const saved = await frozen
  expect(saved.status()).toBe(201)
  const choice = await saved.json()
  const snapshotRaw = await (await request.get(`/api/v1/snapshots/${choice.snapshot_id}`)).text()
  const snapshot = JSON.parse(snapshotRaw)
  expect(snapshot.id).not.toBe(demo.snapshot_id)
  expect(snapshot.record_ids).toEqual(JSON.parse(beforeRaw).record_ids)
  expect(snapshot.policy.reference_passages).toEqual([result.result.hits[0].parent_reference])
  expect(snapshot.policy.reference_choice.payload.manifest.index_id).toBe(result.manifest.index_id)
  expect(await (await request.get(`/api/v1/books/${book.id}/records`)).text()).toBe(recordsRaw)
  expect(await (await request.get(`/api/v1/snapshots/${demo.snapshot_id}`)).text()).toBe(beforeRaw)
  expect((await request.post('/api/v1/check-runs', { data: { book_id: book.id, snapshot_id: snapshot.id } })).ok()).toBe(true)
  const cases = await (await request.get(`/api/v1/cases?book_id=${book.id}`)).json()
  const caseItem = cases.find((c: any) => c.snapshot_id === snapshot.id)
  const launched = await request.post(`/api/v1/cases/${caseItem.id}/investigate`, { data: { mode: 'rules' } })
  expect(launched.ok()).toBe(true)
  const investigation = await launched.json()
  let completed: any
  await expect.poll(async () => {
    const detail = await (await request.get(`/api/v1/cases/${caseItem.id}`)).json()
    completed = detail.runs.find((r: any) => r.id === investigation.id)
    return completed?.status
  }).toBe('complete')
  expect(completed.mode).toBe('rules')
  expect(completed.usage?.calls ?? 0).toBe(0)
  await expect(panel.getByText('人工冻结条文', { exact: true }).first()).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await panel.scrollIntoViewIfNeeded()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await panel.screenshot({ path: info.outputPath('reference-frozen-mobile.png') })
  await page.screenshot({ path: info.outputPath('reference-mobile-viewport.png') })
  const reset = page.waitForResponse(r => r.url().endsWith('/reference-indexes/choices') && r.request().method() === 'POST')
  await panel.getByRole('button', { name: '恢复默认关键词并建立快照' }).click()
  expect((await reset).status()).toBe(201)
  await expect(panel.getByText('默认关键词检索', { exact: true })).toBeVisible()
  expect(await (await request.get(`/api/v1/snapshots/${snapshot.id}`)).text()).toBe(snapshotRaw)
  expect(await (await request.get(`/api/v1/books/${book.id}/records`)).text()).toBe(recordsRaw)
  const history = await (await request.get(`/api/v1/reference-indexes/choices?book_id=${book.id}`)).json()
  expect(history.choices.map((c: any) => c.version)).toEqual([2, 1])
  expect(history.choices.map((c: any) => c.payload.mode)).toEqual(['keyword', 'persistent_bge'])
  expect(errors).toEqual([])
  await writeFile(info.outputPath('reference-before.json'), beforeRaw)
  await writeFile(info.outputPath('reference-frozen.json'), snapshotRaw)
  await writeFile(info.outputPath('reference-query.json'), await (await request.get(`/api/v1/reference-indexes/searches/${runId}?book_id=${book.id}`)).text())
  await writeFile(info.outputPath('reference-validation.json'), JSON.stringify({ status: 'passed', book_id: book.id, index_id: result.manifest.index_id,
    snapshot_before: demo.snapshot_id, snapshot_frozen: snapshot.id, search_id: runId, choice_versions: [1, 2],
    original_records_unchanged: true, old_snapshots_unchanged: true, rule_investigation_id: completed.id,
    paid_model_requests: 0, page_errors: errors, viewport: 390 }, null, 2))
})
