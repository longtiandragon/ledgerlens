import { test, expect, type Page, type Locator } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'

// Real Vue/API regression with explicit teaching assumptions, no mocked responses.
const timing = 'transport_cost_amortization_in_current_revenue_period'
const transport = { transport_before_control_transfer: 'yes', transport_not_separate_performance_obligation: 'yes' }
const scenarios = [
  { name: '运输未到收入确认时点', topic: 'transport_fulfillment_cost',
    values: { ...transport, [timing]: 'no' }, reported: 'other', expected: 'unsupported', treatment: null },
  { name: '运输收入时点未知', topic: 'transport_fulfillment_cost',
    values: { ...transport, [timing]: 'unknown' }, reported: 'operating_cost', expected: 'insufficient', treatment: null },
  { name: '本期履约运输成本摊销', topic: 'transport_fulfillment_cost',
    values: { ...transport, [timing]: 'yes' }, reported: 'selling_expense', expected: 'supported', treatment: 'operating_cost' },
  { name: '长期租赁本金与短租租金混付', topic: 'lease_cash_flow',
    values: { lease_liability_principal_or_interest: 'yes', prepaid_rent_or_lease_deposit: 'no',
      simplified_short_term_or_low_value_lease: 'yes', variable_payment_outside_lease_liability: 'no' },
    reported: 'operating_cash_outflow', expected: 'unsupported', treatment: null },
]

async function evidence(page: Page, field: Locator, source: string) {
  await field.locator('.n-base-selection').click()
  await page.locator('.n-base-select-menu:visible .n-base-select-option').filter({ hasText: source }).last().click()
  await expect(field.locator('.n-tag')).toContainText(source)
  await page.getByRole('heading', { name: '先确认条件，再核对处理。' }).click()
}

for (const scenario of scenarios) {
  test(`真实收入与现金流边界：${scenario.name}`, async ({ page, request }, testInfo) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    const created = await request.post('/api/v1/books', { data: {
      name: `教学边界 ${scenario.name} ${Date.now()}`, framework: 'CAS', period: '2026-10',
    } })
    expect(created.status()).toBe(201)
    const book = await created.json()
    const sourceId = `教学条件-${scenario.name}`
    const original = Buffer.from(JSON.stringify([{
      source_id: sourceId, kind: 'order', business_date: '2026-10-01', period: '2026-10', amount: '1100.00',
      description: `独立教学假设：${scenario.name}。条件为人工构造，非官方样例事实或真实企业确认。`,
    }]))
    const imported = await request.post('/api/v1/imports', { multipart: {
      book_id: book.id, synthetic: 'true', file: { name: 'policy-boundary-teaching.json', mimeType: 'application/json', buffer: original },
    } })
    expect(imported.ok()).toBeTruthy()
    const confirmed = await request.post(`/api/v1/imports/${(await imported.json()).id}/confirm`)
    expect(confirmed.ok()).toBeTruthy()
    const snapshotId = (await confirmed.json()).snapshot_id
    const records = await (await request.get(`/api/v1/books/${book.id}/records`)).json()
    expect(records).toHaveLength(1)

    await page.goto(`/?book=${book.id}`)
    await page.locator('nav').getByRole('button', { name: /账套与政策/ }).click()
    await page.getByLabel('业务主题').selectOption(scenario.topic)
    await page.getByLabel('检查名称').fill(scenario.name)
    await expect(page.locator('[data-predicate]')).toHaveCount(Object.keys(scenario.values).length)
    for (const [name, value] of Object.entries(scenario.values)) {
      const field = page.locator(`[data-predicate="${name}"]`)
      await field.locator('select').selectOption(value!)
      if (name === timing) await expect(field.locator('legend')).toContainText('随相关收入确认')
      if (value !== 'unknown') {
        await evidence(page, field, sourceId)
        await field.getByRole('checkbox').check()
      }
    }
    await page.getByLabel('已采用的处理').selectOption(scenario.reported)
    await evidence(page, page.locator('fieldset').filter({ has: page.getByLabel('已采用的处理') }), sourceId)
    await page.getByRole('checkbox', { name: '确认提交本次条件版本' }).check()
    const saving = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/policy-observations'))
    await page.getByRole('button', { name: '保存条件版本', exact: true }).click()
    const savedResponse = await saving
    expect(savedResponse.status()).toBe(201)
    const saved = await savedResponse.json()
    const checking = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith(`/${saved.id}/check`))
    await page.getByRole('button', { name: '执行政策检查' }).click()
    const checkedResponse = await checking
    expect(checkedResponse.ok()).toBeTruthy()
    const checked = await checkedResponse.json()
    expect(checked.result.policy_rule_identity.version).toBe('policy-consistency-v9')
    expect([checked.result.status, checked.result.expected_treatment]).toEqual([scenario.expected, scenario.treatment])
    expect(checked.ledger_writes).toBe(0)
    expect(checked.binding.ledger_snapshot_id).toBe(snapshotId)
    expect(checked.binding.evidence_locators).toHaveLength(1)
    expect(checked.binding.evidence_locators[0]).toMatchObject({
      evidence_id: records[0].evidence_id, synthetic: true, document_sha256: createHash('sha256').update(original).digest('hex'),
    })
    const downloading = page.waitForEvent('download')
    await page.getByRole('button', { name: '导出政策底稿' }).click()
    const path = (await (await downloading).path())!
    const exported = JSON.parse(await readFile(path, 'utf8'))
    expect(exported.result).toEqual(checked.result)
    expect(exported.binding).toEqual(checked.binding)
    await testInfo.attach('收入与现金流边界底稿', { path, contentType: 'application/json' })
    expect(await (await request.get(`/api/v1/books/${book.id}/records`)).json()).toEqual(records)
    expect(errors).toEqual([])
  })
}
