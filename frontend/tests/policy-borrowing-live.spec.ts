import { test, expect, type Page, type Locator } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'

// This file uses the real API and Vue page, with no route fulfillment or result stubs.
// Conditions are explicit teaching hypotheses, not facts extracted from a real loan.
const scopePredicate = 'specific_borrowing_interest_only'
const ordinaryValues: Record<string, string> = {
  qualifying_asset_long_construction: 'yes', asset_expenditure_incurred: 'yes',
  borrowing_cost_incurred: 'yes', manufacturer_production_started: 'yes',
  asset_ready_for_intended_use: 'no', capitalization_suspension_excluded: 'yes',
  uniform_capitalization_phase_for_observation: 'yes',
}

async function evidence(page: Page, field: Locator, source: string) {
  await field.locator('.n-base-selection').click()
  await page.locator('.n-base-select-menu:visible .n-base-select-option').filter({ hasText: source }).last().click()
  await expect(field.locator('.n-tag')).toContainText(source)
  await page.getByRole('heading', { name: '先确认条件，再核对处理。' }).click()
}

for (const scenario of [
  { name: '范围未知', value: 'unknown', confirmed: false, expected: 'insufficient', treatment: null },
  { name: '辅助费用', value: 'no', confirmed: true, expected: 'unsupported', treatment: null },
  { name: '选择利息但未人工确认', value: 'yes', confirmed: false, expected: 'insufficient', treatment: null },
  { name: '人工确认专门借款利息', value: 'yes', confirmed: true, expected: 'normal', treatment: 'capitalize_borrowing_costs' },
]) {
  test(`真实借款政策闭环：${scenario.name}`, async ({ page, request }, testInfo) => {
    test.setTimeout(120000)
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    const created = await request.post('/api/v1/books', { data: {
      name: `教学借款范围 ${scenario.name} ${Date.now()}`, framework: 'CAS', period: '2026-10',
    } })
    expect(created.status()).toBe(201)
    const book = await created.json()
    const sourceId = `教学条件-${scenario.name}`
    const original = Buffer.from(JSON.stringify([{
      source_id: sourceId, kind: 'order', business_date: '2026-10-01', period: '2026-10', amount: '100.00',
      description: `独立教学假设：合格定制设备生产中、未就绪、单一阶段且无暂停。费用范围=${scenario.name}。非真实企业贷款或专业确认。`,
    }]))
    const imported = await request.post('/api/v1/imports', { multipart: {
      book_id: book.id, synthetic: 'true', file: { name: 'borrowing-teaching.json', mimeType: 'application/json', buffer: original },
    } })
    expect(imported.ok()).toBeTruthy()
    const batch = await imported.json()
    const confirmation = await request.post(`/api/v1/imports/${batch.id}/confirm`)
    expect(confirmation.ok()).toBeTruthy()
    const snapshotId = (await confirmation.json()).snapshot_id
    const before = await (await request.get(`/api/v1/books/${book.id}/records`)).json()
    expect(before).toHaveLength(1)
    const source = before[0].evidence_id

    await page.goto(`/?book=${book.id}`)
    await page.locator('nav').getByRole('button', { name: /账套与政策/ }).click()
    await page.getByLabel('业务主题').selectOption('custom_equipment_borrowing_cost')
    await page.getByLabel('检查名称').fill(`借款范围-${scenario.name}`)
    const fields = page.locator('[data-predicate]')
    await expect(fields).toHaveCount(8)
    for (const [name, value] of Object.entries(ordinaryValues)) {
      const field = page.locator(`[data-predicate="${name}"]`)
      await field.locator('select').selectOption(value)
      await evidence(page, field, sourceId)
      await field.getByRole('checkbox').check()
    }
    const scope = page.locator(`[data-predicate="${scopePredicate}"]`)
    await expect(scope.locator('legend')).toContainText('不含辅助费用')
    await scope.locator('select').selectOption(scenario.value)
    if (scenario.value !== 'unknown') await evidence(page, scope, sourceId)
    if (scenario.confirmed) await scope.getByRole('checkbox').check()
    else await expect(scope.getByRole('checkbox')).not.toBeChecked()
    await page.getByLabel('已采用的处理').selectOption('capitalize_borrowing_costs')
    await evidence(page, page.locator('fieldset').filter({ has: page.getByLabel('已采用的处理') }), sourceId)
    await page.getByRole('checkbox', { name: '确认提交本次条件版本' }).check()
    const saving = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/policy-observations'))
    await page.getByRole('button', { name: '保存条件版本', exact: true }).click()
    const savedResponse = await saving
    expect(savedResponse.status()).toBe(201)
    const saved = await savedResponse.json()
    expect(saved.payload.conditions[scopePredicate].confirmation).toBe(scenario.confirmed ? 'human_confirmed' : 'unconfirmed')
    const checking = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(`/${saved.id}/check`))
    await page.getByRole('button', { name: '执行政策检查' }).click()
    const checkResponse = await checking
    expect(checkResponse.ok()).toBeTruthy()
    const checked = await checkResponse.json()
    expect(checked.result.status).toBe(scenario.expected)
    expect(checked.result.expected_treatment).toBe(scenario.treatment)
    expect(checked.result.policy_rule_identity.version).toBe('policy-consistency-v9')
    expect(checked.ledger_writes).toBe(0)
    expect(checked.binding.ledger_snapshot_id).toBe(snapshotId)
    expect(checked.binding.evidence_locators.map((x: any) => x.evidence_id)).toEqual([source])
    expect(checked.binding.evidence_locators[0].synthetic).toBe(true)
    expect(checked.binding.evidence_locators[0].document_sha256).toBe(createHash('sha256').update(original).digest('hex'))
    if (scenario.expected === 'insufficient') expect(checked.result.missing_conditions).toContain(scopePredicate)

    const downloading = page.waitForEvent('download')
    await page.getByRole('button', { name: '导出政策底稿' }).click()
    const downloaded = await downloading
    const downloadedPath = (await downloaded.path())!
    const exported = JSON.parse(await readFile(downloadedPath, 'utf8'))
    expect(exported.result).toEqual(checked.result)
    expect(exported.binding).toEqual(checked.binding)
    await testInfo.attach('真实政策底稿', { path: downloadedPath, contentType: 'application/json' })
    expect(await (await request.get(`/api/v1/books/${book.id}/records`)).json()).toEqual(before)

    if (scenario.confirmed && scenario.value === 'yes') {
      await page.getByRole('button', { name: '修订条件', exact: true }).click()
      await expect(page.locator('[data-predicate] input[type=checkbox]:checked')).toHaveCount(0)
      await expect(page.getByRole('checkbox', { name: '确认提交本次条件版本' })).not.toBeChecked()
      await page.setViewportSize({ width: 390, height: 844 })
      await expect(page.locator('#workspace-navigation')).toHaveAttribute('aria-hidden', 'true')
      await expect.poll(async () => page.locator('#workspace-navigation').evaluate(node => node.getBoundingClientRect().right)).toBeLessThanOrEqual(1)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      await scope.scrollIntoViewIfNeeded()
      expect((await scope.boundingBox())!.x).toBeGreaterThanOrEqual(0)
      await page.screenshot({ path: testInfo.outputPath('borrowing-scope-mobile.png') })
      await testInfo.attach('利息条件修订需要重新确认', { path: testInfo.outputPath('borrowing-scope-mobile.png'), contentType: 'image/png' })
      const late = await request.post(`/api/v1/books/${book.id}/supplement-demo`)
      expect(late.ok()).toBeTruthy()
      const stale = await request.post(`/api/v1/books/${book.id}/policy-observations/${saved.id}/check`)
      expect(stale.status()).toBe(409)
      const historical = await (await request.get(`/api/v1/books/${book.id}/policy-checks/${checked.id}/export`)).json()
      expect(historical.stale).toBe(true)
      expect(historical.result).toEqual(checked.result)
      expect(historical.binding).toEqual(checked.binding)
    }
    expect(errors).toEqual([])
  })
}
