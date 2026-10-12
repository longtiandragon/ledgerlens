import { test, expect, type Page, type APIRequestContext, type Route } from '@playwright/test'

const endpoint = '/api/v1/statement-datasets'
const snapshotId = 'snapshot-2025'
const scopeA = `${endpoint}/public-a/snapshots/${snapshotId}`
const scopeB = `${endpoint}/public-b/snapshots/${snapshotId}`
const sha = 'a'.repeat(64)
const snapshot = (dataset_scope_id: string, title: string) => ({
  dataset_scope_id, snapshot_id: snapshotId, title, observed_at: '2026-10-09T03:00:00Z',
  report_count: 120, observed_count: 90, rejected_count: 30, parser_source_sha256: sha,
  scope: 'public_consolidated_balance_sheet', read_only: true,
})
const snapshots = {
  snapshots: [snapshot('public-a', '公开年报样例 A'), snapshot('public-b', '公开年报样例 B')],
  read_only: true, scope: 'public_consolidated_balance_sheet', notice: '固定快照的原文提取记录，业务语义尚未复核。',
}

// Independently handwritten amounts. These fixtures verify presentation and
// provenance boundaries; they do not measure extraction or accounting accuracy.
const observation = (metric: string, amount: string, row: number, reportId = 'r-0001') => ({
  evidence_id: `${reportId}-${metric}`, metric, amount_reported: amount,
  previous_amount_reported: '100.00', normalized_cny: amount, unit: '元', currency: 'CNY',
  pdf_page: 2, table: 1, table_row: row,
  current_table_column_zero_based: 1, previous_table_column_zero_based: 2,
  current_cell: amount, previous_cell: '100.00', label_cell: metric === 'total_assets' ? '资产总计' : metric === 'total_liabilities' ? '负债合计' : '所有者权益合计',
  current_cell_bbox: [80, 180, 200, 210], previous_cell_bbox: [220, 180, 340, 210],
  text_lines: [{ line: 12, text: '资产总计 120.00 100.00', bbox: [20, 180, 340, 210] }],
  semantic_status: 'unverified_extraction',
})
const report = (number: number, companyName = `样例公司 ${number}`, rejected = false) => {
  const id = `r-${String(number).padStart(4, '0')}`
  return {
    report_id: id, company_code: String(600000 + number), company_name: companyName, report_year: 2025,
    status: rejected ? 'rejected' : 'observed', error_code: rejected ? 'balance_sheet_not_found' : null,
    raw_sha256: sha, url: rejected ? 'javascript:alert(1)' : 'https://example.com/public/annual-report.pdf',
    source_license: 'unspecified',
    observations: rejected ? [] : [observation('total_assets', '120.00', 1, id), observation('total_liabilities', '90.00', 2, id), observation('total_equity', '30.00', 3, id)],
    balance_check: rejected ? null : { difference_reported_unit: '0.00', equal_as_reported: true },
  }
}
const first = report(1, '原文样例股份')
const precise = report(2, '精度样例股份')
precise.observations[0]!.amount_reported = '9007199254740993.01'
precise.observations[0]!.normalized_cny = '9007199254740993.01'
precise.observations[0]!.current_cell = '9007199254740993.01'
const rejected = report(30, '拒绝样例股份', true)
const firstPage = [first, precise, ...Array.from({ length: 27 }, (_, i) => report(i + 3)), rejected]
const secondPage = Array.from({ length: 30 }, (_, i) => report(i + 31, i === 0 ? '第二页股份' : undefined))
const detail = (item = first, metric = 'total_assets') => ({
  observation: item.observations.find(value => value.metric === metric)!,
  report: { report_id: item.report_id, company_name: item.company_name, report_year: item.report_year, raw_sha256: item.raw_sha256, url: item.url },
  context: { statement: '合并资产负债表', semantic_status: 'unverified_extraction' },
  column_header: { current: '2025年12月31日', previous: '2024年12月31日', unit: '元' },
  page: { width: 612, height: 792, rotation: 0 }, read_only: true, semantic_status: 'unverified_extraction',
})

// A two-page local PDF fixture with correct xref byte offsets. No external PDF
// URL, download dependency, model call or real ledger is involved.
function smallPdf() {
  const streams = ['BT /F1 12 Tf 80 600 Td (Cover page) Tj ET', 'BT /F1 12 Tf 80 592 Td (Total assets 120.00 / Previous 100.00) Tj ET']
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ...streams.map(value => `<< /Length ${Buffer.byteLength(value)} >>\nstream\n${value}\nendstream`),
  ]
  let body = '%PDF-1.4\n'
  const offsets = [0]
  objects.forEach((value, index) => { offsets.push(Buffer.byteLength(body)); body += `${index + 1} 0 obj\n${value}\nendobj\n` })
  const xref = Buffer.byteLength(body)
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.slice(1).map(value => `${String(value).padStart(10, '0')} 00000 n \n`).join('')}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return Buffer.from(body)
}
type Override = (route: Route, url: URL) => Promise<boolean>

async function mountStatement(page: Page, request: APIRequestContext, override?: Override) {
  const component = await request.get('/src/components/StatementEvidence.vue')
  expect(component.ok()).toBe(true)
  const vueImport = (await component.text()).match(/from\s+["']([^"']*\/vue\.js[^"']*)["']/)?.[1]
  expect(vueImport).toBeTruthy()
  const requests: { method: string; path: string; search: string }[] = []
  const unexpected: string[] = []
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/**', async route => {
    const incoming = route.request()
    const url = new URL(incoming.url())
    requests.push({ method: incoming.method(), path: url.pathname, search: url.search })
    if (incoming.method() !== 'GET' || !(url.pathname === endpoint || url.pathname.startsWith(`${scopeA}/`) || url.pathname.startsWith(`${scopeB}/`))) {
      unexpected.push(`${incoming.method()} ${url.pathname}`)
      await route.abort(); return
    }
    if (override && await override(route, url)) return
    if (url.pathname === endpoint) {
      await route.fulfill({ json: snapshots }); return
    }
    if (url.pathname.endsWith('/reports')) {
      const offset = Number(url.searchParams.get('offset') || '0')
      const status = url.searchParams.get('status') || 'all'
      const q = url.searchParams.get('q') || ''
      const items = status === 'rejected' ? [rejected] : q ? [precise] : offset ? secondPage : firstPage
      await route.fulfill({ json: { items, total: status === 'rejected' || q ? 1 : 120, offset, limit: 30, next_offset: status === 'rejected' || q || offset ? null : 30 } }); return
    }
    if (url.pathname.includes('/evidence/')) {
      const evidenceId = decodeURIComponent(url.pathname.split('/evidence/')[1]!)
      const item = evidenceId.startsWith(precise.report_id) ? precise : first
      const metric = evidenceId.slice(item.report_id.length + 1)
      await route.fulfill({ json: detail(item, metric) }); return
    }
    if (url.pathname.endsWith(`/reports/${first.report_id}/file`) || url.pathname.endsWith(`/reports/${precise.report_id}/file`)) {
      await route.fulfill({ contentType: 'application/pdf', body: smallPdf() }); return
    }
    unexpected.push(`${incoming.method()} ${url.pathname}`)
    await route.abort()
  })
  await page.route('**/__component-test-statement-evidence', route => route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><div class="page-content" id="component-root"></div><script type="module">
    import {createApp,h} from ${JSON.stringify(vueImport)};
    import '/src/style.css';
    import StatementEvidence from '/src/components/StatementEvidence.vue';
    window.statementTestApp=createApp({render:()=>h(StatementEvidence)});
    window.statementTestApp.mount('#component-root');
    </script></html>` }))
  await page.goto('/__component-test-statement-evidence')
  await expect(page.getByRole('heading', { name: /让报表数字，\s*回到它的原始页面。/ })).toBeVisible()
  return { requests, unexpected, errors }
}
function expectReadOnly(observed: Awaited<ReturnType<typeof mountStatement>>) {
  expect(observed.requests.every(value => value.method === 'GET')).toBe(true)
  expect(observed.unexpected).toEqual([])
  expect(observed.errors).toEqual([])
}

test('原金额与未复核边界保留，PDF打开指定页并标注本期原文单元格', async ({ page, request }) => {
  const observed = await mountStatement(page, request, async (route, url) => {
    if (url.pathname.includes('/evidence/')) {
      const data = detail()
      await route.fulfill({ json: { ...data, observation: { ...data.observation, table_rows: [1, 2], label_fragments: [
        { table_row: 1, table_column_zero_based: 0, text: '资产', bbox: [20, 180, 60, 195] },
        { table_row: 2, table_column_zero_based: 0, text: '总计', bbox: [20, 195, 60, 210] },
      ] } } }); return true
    }
    return false
  })
  await expect(page.getByLabel('年报快照统计')).toContainText('120')
  await expect(page.getByLabel('年报快照统计')).toContainText('90')
  await expect(page.getByLabel('年报快照统计')).toContainText('30')
  const card = page.getByRole('article', { name: '原文样例股份 2025 年报' })
  await expect(card).toContainText('120.00')
  await expect(card).toContainText('90.00')
  await expect(card).toContainText('30.00')
  await expect(card).toContainText('提取未复核')
  await expect(card).toContainText('CNY · 元')
  await expect(page.getByRole('article', { name: '精度样例股份 2025 年报' })).toContainText('9007199254740993.01')
  const bad = page.getByRole('article', { name: '拒绝样例股份 2025 年报' })
  await expect(bad).toContainText('balance_sheet_not_found')
  await expect(bad.getByRole('button', { name: /原文定位/ })).toHaveCount(0)
  await expect(bad.getByRole('link')).toHaveCount(0)
  await card.getByRole('button', { name: '原文样例股份 资产总计 原文定位' }).click()
  await expect(page.locator('.statement-locator dd').first()).toHaveText('2')
  await expect(page.getByLabel('年报证据定位')).toContainText('业务含义仍需复核')
  await expect(page.getByLabel('年报证据定位')).toContainText('关联原表行索引：1 / 2')
  const fragments = page.getByLabel('项目标签原单元格与坐标').locator('tbody tr')
  await expect(fragments).toHaveCount(2)
  await expect(fragments.nth(0)).toContainText('资产')
  await expect(fragments.nth(0)).toContainText('20, 180, 60, 195')
  await expect(fragments.nth(1)).toContainText('总计')
  await expect(page.getByLabel('本期单元格定位框')).toBeVisible()
  await expect(page.locator('.statement-pdf-pages')).toContainText('2 / 2')
  await expect(page.locator('.statement-pdf-surface canvas')).toBeVisible()
  await expect(page.getByRole('link', { name: '在新窗口打开已校验原 PDF' })).toHaveAttribute('href', `${scopeA}/reports/r-0001/file#page=2`)
  await expect(page.locator('iframe')).toHaveCount(0)
  await expect.poll(() => observed.requests.some(value => value.path === `${scopeA}/reports/r-0001/file`)).toBe(true)
  await page.getByRole('button', { name: '关闭年报证据' }).click()
  await expect(page.getByLabel('本期单元格定位框')).toHaveCount(0)
  expectReadOnly(observed)
})

test('分页、筛选和查询绑定快照，无账套独立可用', async ({ page, request }) => {
  const observed = await mountStatement(page, request)
  await expect(page.getByRole('article')).toHaveCount(30)
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.getByRole('article', { name: '第二页股份 2025 年报' })).toBeVisible()
  await expect(page.getByRole('button', { name: '下一页', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '上一页', exact: true }).click()
  await expect(page.getByRole('article', { name: '原文样例股份 2025 年报' })).toBeVisible()
  await page.getByRole('combobox', { name: '解析状态', exact: true }).selectOption('rejected')
  await expect(page.getByRole('article')).toHaveCount(1)
  await expect(page.getByRole('article')).toContainText('拒绝样例股份')
  await page.getByRole('combobox', { name: '解析状态', exact: true }).selectOption('all')
  await page.getByLabel('查找公司或报告').fill('  精度  ')
  await page.getByRole('button', { name: '查找', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(1)
  await expect(page.getByRole('article')).toContainText('9007199254740993.01')
  const search = observed.requests.find(value => value.search.includes('q=%E7%B2%BE%E5%BA%A6'))
  expect(search?.path).toBe(`${scopeA}/reports`)
  await page.getByLabel('年报数据快照').selectOption(JSON.stringify(['public-b', snapshotId]))
  await expect(page.getByLabel('查找公司或报告')).toHaveValue('')
  await expect(page.getByRole('combobox', { name: '解析状态', exact: true })).toHaveValue('all')
  await expect.poll(() => observed.requests.some(value => value.path === `${scopeB}/reports`)).toBe(true)
  expectReadOnly(observed)
})

test('网络失败保留未知和失败原因，重试只读且不显示成功空列表', async ({ page, request }) => {
  let snapshotCalls = 0
  let listCalls = 0
  const observed = await mountStatement(page, request, async (route, url) => {
    if (url.pathname === endpoint && ++snapshotCalls === 1) {
      await route.fulfill({ status: 503, json: { detail: '快照校验不可用' } }); return true
    }
    if (url.pathname.endsWith('/reports') && ++listCalls === 1) {
      await route.fulfill({ status: 503, json: { detail: '报告索引不可用' } }); return true
    }
    return false
  })
  await expect(page.getByRole('alert')).toContainText('快照校验不可用')
  await expect(page.getByLabel('年报快照统计').locator('dd')).toHaveText(['—', '—', '—'])
  await expect(page.getByText('尚无年报证据快照', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '重试', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('报告索引不可用')
  await expect(page.locator('.statement-list-summary')).toContainText('报告列表未取得')
  await expect(page.getByText('当前筛选没有匹配报告。', { exact: true })).toHaveCount(0)
  await expect(page.getByText('0 份匹配年报', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '重试读取', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(30)
  await expect(page.getByRole('alert')).toHaveCount(0)
  expectReadOnly(observed)
})

test('关闭、换快照及卸载隔离晚到证据，不跨快照挂载PDF', async ({ page, request }) => {
  let release: (() => void) | undefined
  let pending = 0
  const observed = await mountStatement(page, request, async (route, url) => {
    if (url.pathname.includes('/evidence/')) {
      pending++
      await new Promise<void>(resolve => { release = resolve })
      await route.fulfill({ json: detail() }).catch(() => {})
      return true
    }
    return false
  })
  const locate = page.getByRole('button', { name: '原文样例股份 资产总计 原文定位' })
  await locate.click()
  await expect.poll(() => pending).toBe(1)
  await page.getByRole('button', { name: '关闭年报证据' }).click()
  release!()
  await expect(page.getByLabel('年报证据定位')).toHaveCount(0)
  await locate.click()
  await expect.poll(() => pending).toBe(2)
  // A host/programmatic snapshot change can occur behind the drawer backdrop.
  // It must close the old drawer and invalidate its in-flight response.
  await page.getByLabel('年报数据快照').selectOption(JSON.stringify(['public-b', snapshotId]), { force: true })
  release!()
  await expect(page.getByRole('article')).toHaveCount(30)
  await locate.click()
  await expect.poll(() => pending).toBe(3)
  await page.evaluate(() => (window as unknown as { statementTestApp: { unmount(): void } }).statementTestApp.unmount())
  release!()
  await expect(page.locator('.statement-evidence')).toHaveCount(0)
  await expect(page.getByLabel('年报证据定位')).toHaveCount(0)
  expect(observed.requests.filter(value => value.path.endsWith('/file'))).toEqual([])
  expectReadOnly(observed)
})

test('390px布局仅表格横向滚动，抽屉可关闭且来源文字不执行HTML', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const observed = await mountStatement(page, request, async (route, url) => {
    if (url.pathname.includes('/evidence/')) {
      const data = detail()
      data.observation = { ...data.observation, text_lines: [{ line: 12, text: '<img src=x onerror=alert(1)>', bbox: [20, 180, 340, 210] }] }
      await route.fulfill({ json: data }); return true
    }
    return false
  })
  await expect(page.getByRole('article')).toHaveCount(30)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const table = page.getByRole('article', { name: '原文样例股份 2025 年报' }).locator('.statement-observations')
  expect(await table.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true)
  await table.evaluate(element => { element.scrollLeft = element.scrollWidth })
  await page.getByRole('button', { name: '原文样例股份 资产总计 原文定位' }).click()
  await expect(page.getByText('<img src=x onerror=alert(1)>', { exact: true })).toBeVisible()
  await expect(page.getByLabel('本期单元格定位框')).toBeVisible()
  await expect(page.locator('.statement-detail-lines img')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.getByRole('button', { name: '关闭年报证据' }).click()
  await expect(page.getByLabel('年报证据定位')).toHaveCount(0)
  expectReadOnly(observed)
})

test('证据失败、原文件失败与坐标不匹配明确显示，均不绘制误导定位框', async ({ page, request }) => {
  let evidenceCalls = 0
  let fileCalls = 0
  const observed = await mountStatement(page, request, async (route, url) => {
    if (url.pathname.includes('/evidence/')) {
      evidenceCalls++
      if (evidenceCalls === 1) {
        await route.fulfill({ status: 409, json: { detail: '证据不属于当前快照' } }); return true
      }
      if (evidenceCalls === 2) {
        await route.fulfill({ json: { ...detail(), page: { width: 650, height: 792, rotation: 0 } } }); return true
      }
    }
    if (url.pathname.endsWith('/file') && ++fileCalls === 2) {
      await route.fulfill({ status: 409, json: { detail: '原 PDF 指纹不匹配' } }); return true
    }
    return false
  })
  const locate = page.getByRole('button', { name: '原文样例股份 资产总计 原文定位' })
  await locate.click()
  await expect(page.getByRole('alert')).toContainText('证据不属于当前快照')
  await expect(page.getByLabel('年报原 PDF 预览')).toHaveCount(0)
  await page.getByRole('button', { name: '关闭年报证据' }).click()
  await locate.click()
  await expect(page.getByLabel('年报原 PDF 预览')).toContainText('未绘制定位框')
  await expect(page.getByLabel('本期单元格定位框')).toHaveCount(0)
  await expect(page.locator('.statement-pdf-surface canvas')).toBeVisible()
  await page.getByRole('button', { name: '关闭年报证据' }).click()
  await locate.click()
  await expect(page.getByRole('alert')).toContainText('无法读取原 PDF')
  await expect(page.getByLabel('本期单元格定位框')).toHaveCount(0)
  await expect(page.locator('.statement-value-pair')).toContainText('120.00')
  expectReadOnly(observed)
})

test('真实导入快照的原PDF可读取（仅显式启用）', async ({ page, request }) => {
  test.skip(process.env.STATEMENT_DATASET_TESTS !== '1', '真实来源检查需要显式启用 STATEMENT_DATASET_TESTS=1')
  const index = await request.get(endpoint)
  expect(index.ok()).toBe(true)
  const data = await index.json() as typeof snapshots
  expect(data.snapshots.length).toBeGreaterThan(0)
  const selected = data.snapshots[0]!
  const prefix = `${endpoint}/${encodeURIComponent(selected.dataset_scope_id)}/snapshots/${encodeURIComponent(selected.snapshot_id)}`
  const reports = await request.get(`${prefix}/reports?status=observed&offset=0&limit=30`)
  expect(reports.ok()).toBe(true)
  const items = (await reports.json()).items as (typeof first)[]
  const chosen = items.find(value => value.observations.length > 0)
  expect(chosen).toBeTruthy()
  const file = await request.get(`${prefix}/reports/${encodeURIComponent(chosen!.report_id)}/file`)
  expect(file.ok()).toBe(true)
  expect(file.headers()['content-type']).toContain('application/pdf')
  expect((await file.body()).subarray(0, 5).toString()).toBe('%PDF-')
  // Read-only live UI check: select a report from the API, then follow the same
  // report and its actual page in the workstation. This is not an accuracy eval.
  await page.goto('/')
  await page.getByRole('button', { name: '年报证据', exact: true }).click()
  await expect(page.getByLabel('年报快照统计').locator('dd').first()).toHaveText(String(selected.report_count))
  await expect(page.getByRole('article')).toHaveCount(Math.min(selected.report_count, 30))
  await page.screenshot({ path: '../runtime/statement-evidence-desktop.png', fullPage: false })
  const splitLabelReport = items.find(value => value.report_id === 'annual-601933-2023')
  const target = splitLabelReport || items.reduce((current, value) => value.observations[0]!.pdf_page > current.observations[0]!.pdf_page ? value : current, chosen!)
  const targetMetric = splitLabelReport ? 'total_equity' : 'total_assets'
  const sourceObservation = target.observations.find(value => value.metric === targetMetric)!
  await page.getByLabel('查找公司或报告').fill(target.report_id)
  await page.getByRole('button', { name: '查找', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(1)
  await page.getByRole('article', { name: `${target.company_name} ${target.report_year} 年报`, exact: true }).getByRole('button', { name: splitLabelReport ? /所有者权益合计 原文定位/ : /资产总计 原文定位/ }).click()
  await expect(page.getByLabel('本期单元格定位框')).toBeVisible({ timeout: 30000 })
  await expect(page.locator('.statement-locator dd').first()).toHaveText(String(sourceObservation.pdf_page))
  await expect(page.locator('.statement-value-pair')).toContainText(sourceObservation.amount_reported)
  await expect(page.locator('.statement-value-pair')).toContainText(sourceObservation.previous_amount_reported)
  if (splitLabelReport) {
    await expect(page.locator('.statement-locator dd').first()).toHaveText('120')
    await expect(page.locator('.statement-value-pair')).toContainText('5934737117.56')
    await expect(page.locator('.statement-value-pair')).toContainText('7656899905.63')
  }
  await page.screenshot({ path: `../runtime/statement-evidence-real-p${sourceObservation.pdf_page}.png`, fullPage: false })
  await page.getByRole('button', { name: '关闭年报证据' }).click()
  await expect(page.getByLabel('年报证据定位')).toHaveCount(0)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.locator('#workspace-navigation')).toHaveAttribute('inert', '')
  await expect.poll(() => page.locator('#workspace-navigation').evaluate(element => element.getBoundingClientRect().right)).toBeLessThanOrEqual(0)
  await page.evaluate(() => window.scrollTo(0, 0))
  await expect(page.getByLabel('年报快照统计')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: '../runtime/statement-evidence-mobile.png', fullPage: false })
  await page.getByRole('article', { name: `${target.company_name} ${target.report_year} 年报`, exact: true }).getByRole('button', { name: splitLabelReport ? /所有者权益合计 原文定位/ : /资产总计 原文定位/ }).click()
  await expect(page.getByLabel('本期单元格定位框')).toBeVisible({ timeout: 30000 })
  await expect(page.getByRole('button', { name: '关闭年报证据' })).toBeVisible()
  await page.screenshot({ path: `../runtime/statement-evidence-mobile-p${sourceObservation.pdf_page}.png`, fullPage: false })
})
