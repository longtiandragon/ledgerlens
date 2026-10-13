import { test, expect } from '@playwright/test'
import path from 'node:path'
import { readFile, writeFile } from 'node:fs/promises'
import type { OcrRun } from '../src/ocr'

test('扫描 PDF 多页逐条核实、修订、整批导入与历史保持', async ({ page, request }, info) => {
  test.skip(process.env.OCR_LOCAL_TESTS !== '1', '需要已安装本地OCR与合成扫描PDF，无付费请求')
  test.setTimeout(180000)
  const errors: string[] = [], modelRequests: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('request', r => { if (/investigate|deepseek|chat\/completions/.test(r.url())) modelRequests.push(r.url()) })
  const bookResponse = await request.post('/api/v1/books', { data: { name: `OCR多记录合成验收-${Date.now()}`, framework: 'CAS', period: '2026-09' } })
  expect(bookResponse.status()).toBe(201)
  const book = await bookResponse.json()
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`/?book=${book.id}`)
  await page.getByRole('button', { name: '数据接入', exact: true }).click()
  const panel = page.getByRole('region', { name: '扫描件人工核实' })
  const sourcePath = path.resolve('../.local/ocr-pdf-v1/synthetic-scans.pdf')
  await panel.getByLabel('扫描原件', { exact: true }).setInputFiles(sourcePath)
  const started = page.waitForResponse(r => r.url().endsWith('/api/v1/ocr/runs') && r.request().method() === 'POST')
  await panel.getByRole('button', { name: '上传并本地识别' }).click()
  const createdResponse = await started
  expect(createdResponse.status()).toBe(202)
  const created = await createdResponse.json()
  let run: OcrRun | undefined
  await expect.poll(async () => {
    run = await (await request.get(`/api/v1/ocr/runs/${created.id}?book_id=${book.id}`)).json()
    return run?.status
  }, { timeout: 130000, intervals: [1500, 2500] }).toBe('complete')
  await expect(panel.getByText('待人工核实', { exact: true })).toBeVisible()
  // Expected amounts/dates were read from the two printed synthetic source pages.
  // OCR is only used to select locations, never to produce the expected answers.
  const inputs = [
    { kind: 'order', source: 'printed-order-A017', date: '2026-09-12', amount: '1394.99', text: '1,394.99', page: 1 },
    { kind: 'bank', source: 'printed-receipt-B028', date: '2026-09-18', amount: '8008.08', text: '8,008.08', page: 2 },
  ]
  for (const [index, input] of inputs.entries()) {
    if (index) await panel.getByRole('button', { name: '新增一条记录', exact: true }).click()
    const lines = run!.result.pages!.find(p => p.page === input.page)!.observations!
    const date = lines.find(line => line.text.includes(input.date))!
    const amount = lines.find(line => line.text.includes(input.text))!
    expect(date).toBeTruthy(); expect(amount).toBeTruthy()
    await panel.getByLabel('OCR记录类型', { exact: true }).selectOption(input.kind)
    await panel.getByLabel('OCR来源编号', { exact: true }).fill(input.source)
    await panel.locator('#ocr-value-business_date').fill(input.date)
    await panel.getByLabel('业务日期原文行', { exact: true }).selectOption(date.observation_id)
    await panel.locator('#ocr-value-amount').fill(input.amount)
    await panel.getByLabel('业务金额原文行', { exact: true }).selectOption(amount.observation_id)
    await expect(panel.getByLabel('页码', { exact: true })).toHaveValue(String(input.page))
    await expect(panel.getByLabel('所选原文行定位框')).toBeVisible()
  }
  await panel.getByRole('button', { name: '新增一条记录', exact: true }).click()
  await panel.getByRole('button', { name: '移除当前草稿', exact: true }).click()
  await expect(panel.getByLabel('OCR来源编号')).toHaveValue(inputs[1]!.source)
  await panel.getByLabel('核对说明', { exact: true }).fill('逐页对照合成采购单和收款单，人工确认两条记录；没有自动字段推断。')
  await panel.getByRole('checkbox').check()
  await panel.getByRole('button', { name: /01 printed-order-A017/ }).click()
  await expect(panel.getByRole('checkbox')).toBeChecked()
  await panel.locator('#ocr-value-amount').fill('1394.9')
  await expect(panel.getByRole('checkbox')).not.toBeChecked()
  await panel.locator('#ocr-value-amount').fill('1394.99')
  await panel.getByRole('checkbox').check()
  const savedResponse = page.waitForResponse(r => r.url().includes(`/ocr/runs/${created.id}/reviews`) && r.request().method() === 'POST')
  await panel.getByRole('button', { name: '保存人工复核并生成预览' }).click()
  const firstResponse = await savedResponse
  expect(firstResponse.status()).toBe(201)
  const first = await firstResponse.json()
  expect(first.preview.record_count).toBe(2)
  expect(first.review.payload.schema_version).toBe('ocr-batch-review-v1')
  expect(first.preview.rows.map((r: any) => r.locator.review_record_index)).toEqual([1, 2])
  expect(await (await request.get(`/api/v1/books/${book.id}/records`)).json()).toEqual([])
  await expect(panel.getByText('人工复核已保存为新版本。请在下方预览，再确认导入并建立快照。')).toBeVisible()
  await panel.getByRole('button', { name: /02 printed-receipt-B028/ }).click()
  await panel.getByLabel('OCR来源编号').fill('printed-receipt-B028-revised')
  await panel.getByRole('checkbox').check()
  const revisedResponse = page.waitForResponse(r => r.url().includes(`/ocr/runs/${created.id}/reviews`) && r.request().method() === 'POST')
  await panel.getByRole('button', { name: '保存人工复核并生成预览' }).click()
  const secondResponse = await revisedResponse
  expect(secondResponse.status()).toBe(201)
  const second = await secondResponse.json()
  expect(second.review.version).toBe(2)
  expect(second.review.parent_id).toBe(first.review.id)
  expect((await request.post(`/api/v1/imports/${first.preview.id}/confirm`)).status()).toBe(409)
  await expect(page.getByRole('cell', { name: '1394.99', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '8008.08', exact: true })).toBeVisible()
  await page.screenshot({ path: info.outputPath('ocr-batch-desktop.png'), fullPage: true })
  await page.getByRole('button', { name: '确认导入并建立快照', exact: true }).click()
  await expect(page.getByText('成功导入 2 条记录，跳过 0 条重复来源。')).toBeVisible()
  const records = await (await request.get(`/api/v1/books/${book.id}/records`)).json()
  expect(records).toHaveLength(2)
  expect(records.map((r: any) => r.amount).sort()).toEqual(['1394.99', '8008.08'])
  const batchesResponse = await request.get(`/api/v1/books/${book.id}/imports`)
  const batches = await batchesResponse.json()
  const batch = batches.find((b: any) => b.id === second.preview.id)
  const snapshotResponse = await request.get(`/api/v1/snapshots/${batch.snapshot_id}`)
  const snapshot = await snapshotResponse.json()
  expect([...snapshot.record_ids].sort()).toEqual(records.map((r: any) => r.id).sort())
  expect(batch.rows.map((r: any) => r.locator.field_evidence.amount[0].page)).toEqual([1, 2])
  expect((await request.post(`/api/v1/imports/${batch.id}/confirm`)).status()).toBe(200)
  expect(await (await request.get(`/api/v1/documents/${created.document_id}/file`)).body()).toEqual(await readFile(sourcePath))
  await panel.getByRole('button', { name: '刷新记录', exact: true }).click()
  await expect(panel.getByRole('button', { name: '保存人工复核并生成预览' })).toBeDisabled()
  await expect(panel.getByRole('button', { name: '新增一条记录' })).toBeDisabled()
  await panel.getByRole('button', { name: /02 printed-receipt-B028-revised/ }).click()
  await expect(panel.locator('#ocr-value-amount')).toHaveValue('8008.08')
  await panel.getByLabel('页码', { exact: true }).selectOption('2')
  await page.setViewportSize({ width: 390, height: 844 })
  await panel.locator('.record-list').scrollIntoViewIfNeeded()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: info.outputPath('ocr-batch-mobile.png') })
  const finalResponse = await request.get(`/api/v1/ocr/runs/${created.id}?book_id=${book.id}`)
  const final = await finalResponse.json()
  expect(final.result).toEqual(run!.result)
  expect(final.reviews[0].payload).toEqual(first.review.payload)
  expect(final.reviews[1].payload.entries.map((e: any) => e.record.amount)).toEqual(['1394.99', '8008.08'])
  expect(modelRequests).toEqual([]); expect(errors).toEqual([])
  await writeFile(info.outputPath('ocr-batch-run.json'), await finalResponse.body())
  await writeFile(info.outputPath('ocr-batch-imports.json'), await batchesResponse.body())
  await writeFile(info.outputPath('ocr-batch-snapshot.json'), await snapshotResponse.body())
  await writeFile(info.outputPath('ocr-batch-evidence.json'), JSON.stringify({ book_id: book.id, records, batches, snapshot,
    paid_model_requests: 0, page_errors: errors, record_count: 2, source_pages: [1, 2], old_preview_rejected: true,
    original_ocr_unchanged: true, original_file_unchanged: true, mobile_width: 390 }, null, 2))
})
