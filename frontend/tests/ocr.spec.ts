import { test, expect } from '@playwright/test'
import path from 'node:path'
import { readFile, writeFile } from 'node:fs/promises'
import type { OcrRun } from '../src/ocr'

test.describe('可选本地真实 OCR 产品流程（合成扫描件）', () => {
  test.skip(process.env.OCR_LOCAL_TESTS !== '1', '需要显式启用已安装的本地 OCR 环境与合成扫描件，无付费请求')
  for (const format of ['image', 'pdf'] as const) {
    test(`${format} 原件定位、人工字段确认、导入及移动布局`, async ({ page, request }, info) => {
      test.setTimeout(180000)
      const errors: string[] = [], modelRequests: string[] = []
      page.on('pageerror', e => errors.push(e.message))
      page.on('request', r => { if (/investigate|deepseek|chat\/completions/.test(r.url())) modelRequests.push(r.url()) })
      expect((await (await request.get('/api/v1/ocr/capabilities')).json()).available).toBe(true)
      const bookResponse = await request.post('/api/v1/books', { data: { name: `OCR网页合成验收-${format}-${Date.now()}`, framework: 'CAS', period: '2026-09' } })
      expect(bookResponse.status()).toBe(201)
      const book = await bookResponse.json()
      await page.setViewportSize({ width: 1440, height: 1000 })
      await page.goto(`/?book=${book.id}`)
      await page.getByRole('button', { name: '数据接入', exact: true }).click()
      const panel = page.getByRole('region', { name: '扫描件人工核实' })
      const sourcePath = path.resolve(format === 'image' ? '../.local/ocr-scans-v1/images/scan-001.png' : '../.local/ocr-pdf-v1/synthetic-scans.pdf')
      await panel.getByLabel('扫描原件', { exact: true }).setInputFiles(sourcePath)
      const started = page.waitForResponse(r => r.url().endsWith('/api/v1/ocr/runs') && r.request().method() === 'POST')
      await panel.getByRole('button', { name: '上传并本地识别' }).click()
      const response = await started
      expect(response.status()).toBe(202)
      const created = await response.json()
      let run: OcrRun | undefined
      await expect.poll(async () => {
        run = await (await request.get(`/api/v1/ocr/runs/${created.id}?book_id=${book.id}`)).json()
        return run?.status
      }, { timeout: 130000, intervals: [1000, 2000] }).toBe('complete')
      const finished = run!
      await expect(panel.getByText('待人工核实', { exact: true })).toBeVisible({ timeout: 15000 })
      expect(await (await request.get(`/api/v1/books/${book.id}/records`)).json()).toEqual([])
      const lines = finished.result.observations || finished.result.pages?.[0]?.observations || []
      const date = lines.find(l => l.text.includes('2026-09-12'))!
      const amount = lines.find(l => l.text.includes('1,394.99'))!
      expect(date).toBeTruthy(); expect(amount).toBeTruthy()
      await panel.getByLabel('OCR记录类型', { exact: true }).selectOption('order')
      await panel.getByLabel('OCR来源编号', { exact: true }).fill(`ocr-ui-${format}-${created.id}`)
      await panel.locator('#ocr-value-business_date').fill('2026-09-12')
      await panel.getByLabel('业务日期原文行', { exact: true }).selectOption(date.observation_id)
      await panel.locator('#ocr-value-amount').fill('1394.99')
      await panel.getByLabel('业务金额原文行', { exact: true }).selectOption(amount.observation_id)
      await expect(panel.getByLabel('所选原文行定位框')).toBeVisible()
      expect((await panel.locator('polygon').getAttribute('points'))?.split(' ')).toHaveLength(4)
      if (format === 'pdf') {
        await panel.getByLabel('页码', { exact: true }).selectOption('6')
        await expect(panel.getByText('本页未检出文字，不会自动生成空记录。')).toBeVisible()
        await expect(panel.getByLabel('所选原文行定位框')).toHaveCount(0)
        await panel.getByLabel('页码', { exact: true }).selectOption('1')
        await expect(panel.getByLabel('所选原文行定位框')).toBeVisible()
      }
      await panel.getByLabel('核对说明', { exact: true }).fill('合成样本产品验收：人工对照日期及价税合计，仅验证导入边界。')
      await panel.getByRole('checkbox').check()
      await panel.scrollIntoViewIfNeeded()
      await page.screenshot({ path: info.outputPath(`${format}-desktop.png`), fullPage: true })
      await panel.getByRole('button', { name: '保存人工复核并生成预览' }).click()
      await expect(panel.getByText('人工复核已保存为新版本。请在下方预览，再确认导入并建立快照。')).toBeVisible()
      expect(await (await request.get(`/api/v1/books/${book.id}/records`)).json()).toEqual([])
      await expect(page.getByRole('button', { name: '按映射重新校验' })).toHaveCount(0)
      await expect(page.getByRole('cell', { name: '1394.99', exact: true })).toBeVisible()
      await page.getByRole('button', { name: '确认导入并建立快照', exact: true }).click()
      await expect(page.getByText('成功导入 1 条记录，跳过 0 条重复来源。')).toBeVisible()
      const records = await (await request.get(`/api/v1/books/${book.id}/records`)).json()
      expect(records).toHaveLength(1)
      // /records intentionally returns the flat payload; source locators live in the immutable import.
      expect(records[0].amount).toBe('1394.99')
      const batches = await (await request.get(`/api/v1/books/${book.id}/imports`)).json()
      expect(batches[0].rows[0].locator.ocr_run_id).toBe(created.id)
      expect(batches[0].document_id).toBe(created.document_id)
      const snapshot = await (await request.get(`/api/v1/snapshots/${batches[0].snapshot_id}`)).json()
      expect(snapshot.record_ids).toEqual([records[0].id])
      const original = await request.get(`/api/v1/documents/${created.document_id}/file`)
      expect(await original.body()).toEqual(await readFile(sourcePath))
      await panel.getByRole('button', { name: '刷新记录', exact: true }).click()
      await expect(panel.getByText('该记录已导入，原记录保持只读；后续调整请进入模拟分支。')).toBeVisible()
      await expect(panel.getByRole('button', { name: '保存人工复核并生成预览' })).toBeDisabled()
      await page.setViewportSize({ width: 390, height: 844 })
      await panel.scrollIntoViewIfNeeded()
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false)
      await page.screenshot({ path: info.outputPath(`${format}-mobile.png`), fullPage: true })
      const detailResponse = await request.get(`/api/v1/ocr/runs/${created.id}?book_id=${book.id}`)
      const detail = await detailResponse.json()
      // Preserve raw JSON numeric tokens: JS stringify would change 1.0 to 1 and break evidence hashes.
      await writeFile(info.outputPath('ocr-run-response.json'), await detailResponse.body())
      await writeFile(info.outputPath('ocr-product-evidence.json'), JSON.stringify({ book_id: book.id, run: detail, records, batches, snapshot, model_requests: modelRequests, page_errors: errors }, null, 2))
      await info.attach('ocr-product-evidence', { path: info.outputPath('ocr-product-evidence.json'), contentType: 'application/json' })
      expect(detail.result.confirmation).toBe('unconfirmed')
      expect(modelRequests).toEqual([]); expect(errors).toEqual([])
    })
  }
})
