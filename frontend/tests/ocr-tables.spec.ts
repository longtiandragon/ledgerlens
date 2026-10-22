import { test, expect } from '@playwright/test'
import path from 'node:path'
import { writeFile } from 'node:fs/promises'
import type { OcrTables } from '../src/ocr'

for (const variant of ['clear-table', 'unsupported-columns', 'mixed-header', 'wrapped-description']) {
  test(`真实扫描表格候选：${variant}`, async ({ page, request }, info) => {
    test.skip(process.env.OCR_TABLE_TESTS !== '1', '需要显式启用本地OCR与已生成表格样本，无付费调用')
    test.setTimeout(150000)
    const errors: string[] = [], modelRequests: string[] = []
    page.on('pageerror', e => errors.push(e.message))
    page.on('request', r => { if (/investigate|deepseek|chat\/completions/.test(r.url())) modelRequests.push(r.url()) })
    const book = await (await request.post('/api/v1/books', { data: { name: `OCR表格合成验收-${variant}-${Date.now()}`, framework: 'CAS', period: '2026-09' } })).json()
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(`/?book=${book.id}`)
    await page.getByRole('button', { name: '数据接入', exact: true }).click()
    const panel = page.getByRole('region', { name: '扫描件人工核实' })
    const fixtureDirectory = variant === 'mixed-header' ? 'ocr-header-boundary-v1' : 'ocr-tables-v1'
    const fixture = variant === 'wrapped-description'
      ? process.env.OCR_WRAPPED_FIXTURE || '../.local/ocr-wrapped-v1/images/wrapped-description.png'
      : `../.local/${fixtureDirectory}/images/${variant}.png`
    await panel.getByLabel('扫描原件', { exact: true }).setInputFiles(path.resolve(fixture))
    const started = page.waitForResponse(r => r.url().endsWith('/ocr/runs') && r.request().method() === 'POST')
    await panel.getByRole('button', { name: '上传并本地识别' }).click()
    const createdResponse = await started
    expect(createdResponse.status()).toBe(202)
    const created = await createdResponse.json()
    await expect.poll(async () => (await (await request.get(`/api/v1/ocr/runs/${created.id}?book_id=${book.id}`)).json()).status,
      { timeout: 120000, intervals: [1000, 2000] }).toBe('complete')
    const candidatePanel = panel.getByRole('region', { name: '表格候选提取' })
    await expect(candidatePanel.getByRole('button', { name: '识别本页表格候选' })).toBeEnabled()
    const extracted = page.waitForResponse(r => r.url().includes(`/ocr/runs/${created.id}/table-candidates`))
    await candidatePanel.getByRole('button', { name: '识别本页表格候选' }).click()
    const response = await extracted
    expect(response.ok()).toBe(true)
    const candidates: OcrTables = await response.json()
    expect(candidates.schema_version).toBe('ocr-table-candidates-v3')
    expect(await (await request.get(`/api/v1/books/${book.id}/records`)).json()).toEqual([])
    expect(await (await request.get(`/api/v1/books/${book.id}/imports`)).json()).toEqual([])
    if (variant === 'unsupported-columns') {
      expect(candidates.status).toBe('no_table')
      expect(candidates.tables).toEqual([])
      await expect(candidatePanel.getByText('本页未找到支持的表格结构，请继续手工逐条核实。')).toBeVisible()
      await expect(candidatePanel.getByRole('button', { name: /加入人工草稿/ })).toHaveCount(0)
    } else if (variant === 'mixed-header') {
      const rows = candidates.tables.flatMap(t => t.rows)
      expect(rows.map(r => r.record)).toEqual([{ business_date: '2026-09-03', source_id: 'A101', description: '办公采购', amount: '10.00' }])
      expect(candidates.unassigned.some(g => g.reason === 'unsupported_header_boundary')).toBe(true)
      await expect(candidatePanel.getByText('发现不完整或不支持的新表头，已停止沿用前表列定义', { exact: true })).toBeVisible()
      await expect(candidatePanel.getByLabel('选择候选记录 1', { exact: true })).not.toBeChecked()
      await expect(candidatePanel.getByLabel('选择候选记录 2', { exact: true })).toHaveCount(0)
      await candidatePanel.getByText(/未归类文字（/).click()
      const boundary = candidatePanel.locator('li').filter({ hasText: '发现不完整或不支持的新表头' })
      await boundary.getByRole('button', { name: '查看原文 1', exact: true }).click()
      await expect(panel.getByLabel('所选原文行定位框')).toBeVisible()
      await expect(candidatePanel.getByRole('button', { name: /加入人工草稿/ })).toBeDisabled()
      expect(await (await request.get(`/api/v1/books/${book.id}/records`)).json()).toEqual([])
      expect(await (await request.get(`/api/v1/books/${book.id}/imports`)).json()).toEqual([])
    } else if (variant === 'wrapped-description') {
      const rows = candidates.tables.flatMap(t => t.rows)
      expect(rows.map(r => r.record)).toEqual([
        { business_date: '2026-09-03', source_id: 'A101', description: '设备维护\n更换主机配件', amount: '1250.50' },
        { business_date: '2026-09-04', source_id: 'A102', description: '耗材采购', amount: '40.00' },
      ])
      expect(rows[0]!.field_evidence.description).toHaveLength(2)
      expect(rows[0]!.issues).toEqual(['wrapped_description_requires_manual_review'])
      await expect(candidatePanel.getByText(/摘要包含相邻续行/)).toBeVisible()
      await candidatePanel.getByRole('button', { name: '查看续行 2', exact: true }).click()
      await expect(panel.getByLabel('所选原文行定位框')).toBeVisible()
      await candidatePanel.getByLabel('选择候选记录 1', { exact: true }).check()
      await expect(candidatePanel.getByRole('button', { name: /加入人工草稿/ })).toBeDisabled()
      await candidatePanel.getByLabel('候选记录类型').selectOption('order')
      await candidatePanel.getByRole('button', { name: '将所选 1 条加入人工草稿' }).click()
      await expect(panel.locator('#ocr-value-description')).toHaveValue('设备维护\n更换主机配件')
      await panel.getByRole('button', { name: '查看摘要补充原文 2', exact: true }).click()
      await expect(panel.getByLabel('所选原文行定位框')).toBeVisible()
      const descriptionBinding = panel.getByLabel('摘要原文行', { exact: true })
      const originalDescriptionRefs = rows[0]!.field_evidence.description!
      // Replacing only the primary reference must not discard the wrapped line.
      // Restore the correct first line before saving the reviewed source values.
      await descriptionBinding.selectOption(rows[1]!.field_evidence.description![0]!)
      await expect(panel.getByRole('button', { name: '查看摘要补充原文 2', exact: true })).toBeVisible()
      await descriptionBinding.selectOption(originalDescriptionRefs[0]!)
      await expect(panel.locator('#ocr-value-description')).toHaveValue('设备维护\n更换主机配件')
      await expect(panel.getByRole('checkbox', { name: /我已逐条/ })).not.toBeChecked()
      expect(await (await request.get(`/api/v1/books/${book.id}/records`)).json()).toEqual([])
      expect(await (await request.get(`/api/v1/books/${book.id}/imports`)).json()).toEqual([])
      await panel.getByLabel('核对说明', { exact: true }).fill('独立合成表格：核对日期、金额和两段摘要原文，确认续行属于本记录。')
      await panel.getByRole('checkbox', { name: /我已逐条/ }).check()
      await panel.getByRole('button', { name: '保存人工复核并生成预览' }).click()
      await expect(panel.getByText('人工复核已保存为新版本。请在下方预览，再确认导入并建立快照。')).toBeVisible()
      const reviewed = await (await request.get(`/api/v1/ocr/runs/${created.id}?book_id=${book.id}`)).json()
      const saved = reviewed.reviews.at(-1).payload.entries[0]
      expect(saved.record.description).toBe('设备维护\n更换主机配件')
      expect(saved.field_evidence.description).toEqual(originalDescriptionRefs)
      await panel.getByRole('button', { name: '刷新记录', exact: true }).click()
      await expect(descriptionBinding).toHaveValue(originalDescriptionRefs[0]!)
      await expect(panel.locator('#ocr-value-description')).toHaveValue('设备维护\n更换主机配件')
      await expect(panel.getByRole('button', { name: '查看摘要补充原文 2', exact: true })).toBeVisible()
      // Selecting an existing supplemental reference should not display it twice.
      // Reload the saved review afterwards, without persisting this temporary edit.
      await descriptionBinding.selectOption(originalDescriptionRefs[1]!)
      await expect(panel.getByRole('button', { name: '查看摘要补充原文 2', exact: true })).toHaveCount(0)
      await panel.getByRole('button', { name: '刷新记录', exact: true }).click()
      await expect(descriptionBinding).toHaveValue(originalDescriptionRefs[0]!)
      await expect(panel.getByRole('button', { name: '查看摘要补充原文 2', exact: true })).toBeVisible()
      await panel.getByRole('checkbox', { name: /我已逐条/ }).check()
      const resaved = page.waitForResponse(r => r.url().includes(`/ocr/runs/${created.id}/reviews`) && r.request().method() === 'POST')
      await panel.getByRole('button', { name: '保存人工复核并生成预览' }).click()
      expect((await resaved).ok()).toBe(true)
      await expect(panel.getByText('人工复核已保存为新版本。请在下方预览，再确认导入并建立快照。')).toBeVisible()
      const reloaded = await (await request.get(`/api/v1/ocr/runs/${created.id}?book_id=${book.id}`)).json()
      expect(reloaded.reviews).toHaveLength(2)
      expect(reloaded.reviews.at(-1).payload.entries[0].field_evidence.description).toEqual(originalDescriptionRefs)
      expect(reloaded.reviews.at(-1).payload.entries[0].record).toEqual(saved.record)
      expect(await (await request.get(`/api/v1/books/${book.id}/records`)).json()).toEqual([])
    } else {
      const rows = candidates.tables.flatMap(t => t.rows)
      // Handwritten expected values from the printed fixture; not inferred from candidates.
      expect(rows.map(r => r.record.amount)).toEqual(['1250.50', '0.00', '2040.07'])
      expect(rows.map(r => r.record.source_id)).toEqual(['T101', 'T102', 'T103'])
      expect(rows.every(r => r.copyable && r.issues.length === 0)).toBe(true)
      await candidatePanel.getByRole('button', { name: '1,250.50', exact: true }).click()
      await expect(panel.getByLabel('所选原文行定位框')).toBeVisible()
      for (let i = 1; i <= 3; i++) await candidatePanel.getByLabel(`选择候选记录 ${i}`, { exact: true }).check()
      await expect(candidatePanel.getByRole('button', { name: /加入人工草稿/ })).toBeDisabled()
      await candidatePanel.getByLabel('候选记录类型').selectOption('order')
      await candidatePanel.getByRole('button', { name: '将所选 3 条加入人工草稿' }).click()
      await expect(panel.locator('.record-tab')).toHaveCount(3)
      await expect(panel.locator('#ocr-value-amount')).toHaveValue('1250.50')
      await panel.getByRole('button', { name: /02 T102/ }).click()
      await expect(panel.locator('#ocr-value-amount')).toHaveValue('0.00')
      await expect(panel.getByRole('checkbox', { name: /我已逐条/ })).not.toBeChecked()
      expect(await (await request.get(`/api/v1/books/${book.id}/records`)).json()).toEqual([])
      await panel.getByLabel('核对说明', { exact: true }).fill('独立合成表格验收：逐条核实印刷日期、编号、摘要和金额，包括真实零值；候选未经自动确认。')
      await panel.getByRole('checkbox', { name: /我已逐条/ }).check()
      await panel.getByRole('button', { name: '保存人工复核并生成预览' }).click()
      await expect(panel.getByText('人工复核已保存为新版本。请在下方预览，再确认导入并建立快照。')).toBeVisible()
      await page.getByRole('button', { name: '确认导入并建立快照', exact: true }).click()
      await expect(page.getByText('成功导入 3 条记录，跳过 0 条重复来源。')).toBeVisible()
      const records = await (await request.get(`/api/v1/books/${book.id}/records`)).json()
      expect(records.map((r: any) => [r.source_id, r.amount]).sort()).toEqual([['T101', '1250.50'], ['T102', '0.00'], ['T103', '2040.07']])
    }
    await page.setViewportSize({ width: 390, height: 844 })
    await expect.poll(() => page.locator('#workspace-navigation').evaluate(e => e.getBoundingClientRect().right)).toBeLessThanOrEqual(0)
    await candidatePanel.scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.screenshot({ path: info.outputPath('table-mobile.png') })
    await page.setViewportSize({ width: 1440, height: 1000 })
    await candidatePanel.screenshot({ path: info.outputPath('table-candidates-desktop.png') })
    expect(errors).toEqual([]); expect(modelRequests).toEqual([])
    await writeFile(info.outputPath('table-candidates.json'), await response.body())
    await writeFile(info.outputPath('ocr-run.json'), await (await request.get(`/api/v1/ocr/runs/${created.id}?book_id=${book.id}`)).body())
    await writeFile(info.outputPath('imports.json'), await (await request.get(`/api/v1/books/${book.id}/imports`)).body())
    await writeFile(info.outputPath('records.json'), await (await request.get(`/api/v1/books/${book.id}/records`)).body())
    await writeFile(info.outputPath('table-ui-checks.json'), JSON.stringify({ status: 'passed', variant, book_id: book.id, run_id: created.id, paid_model_requests: 0, page_errors: errors, model_requests: modelRequests, viewport_width: 390 }, null, 2))
  })
}
