import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

test('教学资料标记经上传和来源下载保留，默认不替用户勾选', async ({ page, request }) => {
  const response = await request.post('/api/v1/books', { data: { name: `教学来源标记 ${Date.now()}`, framework: 'CAS', period: '2023-12' } })
  expect(response.ok()).toBeTruthy()
  const book = await response.json()
  await page.goto(`/?book=${book.id}`)
  await page.locator('nav').getByRole('button', { name: /数据接入/ }).click()
  const label = page.getByRole('checkbox', { name: '教学或模拟资料（非真实企业账簿）' })
  await expect(label).not.toBeChecked()
  await label.check()
  const raw = Buffer.from(JSON.stringify([{ source_id: 'TEACHING-1', kind: 'invoice', business_date: '2023-12-31', period: '2023-12', amount: '0.10', description: '独立合成测试资料' }]))
  await page.getByLabel('业务资料文件', { exact: true }).setInputFiles({ name: 'teaching-label.json', mimeType: 'application/json', buffer: raw })
  await page.getByRole('button', { name: '解析与预览' }).click()
  await page.getByRole('button', { name: '确认导入并建立快照' }).click()
  await expect(page.getByText('成功导入 1 条记录，跳过 0 条重复来源。')).toBeVisible()
  const documents = await (await request.get(`/api/v1/books/${book.id}/documents`)).json()
  expect(documents).toHaveLength(1)
  expect(documents[0].synthetic).toBe(true)
  const downloaded = await request.get(`/api/v1/documents/${documents[0].id}/file`)
  expect(await downloaded.body()).toEqual(raw)
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('财政部公开样例原有三行分录经网页导入并保留原文包', async ({ page, request }) => {
  test.skip(!process.env.MOF_VOUCHER_BUNDLE, '需要明确提供本地已核验财政部样例包；测试不联网下载')
  const filename = 'einv_spcl_20231231_22442000000921291350.xml.json'
  const source = fs.readFileSync(path.join(process.env.MOF_VOUCHER_BUNDLE!, filename))
  const packageData = JSON.parse(source.toString('utf8'))
  expect(packageData.data_nature).toBe('public_standard_example_projection')
  const response = await request.post('/api/v1/books', { data: {
    name: `财政部公开教学样例 ${Date.now()}`, framework: 'CAS', period: '2023-12',
    accounts: ['原材料', '应交税费/应交增值税-进项税额', '银行存款']
  } })
  expect(response.ok()).toBeTruthy()
  const book = await response.json()
  await page.goto(`/?book=${book.id}`)
  await page.locator('nav').getByRole('button', { name: /数据接入/ }).click()
  // Recognized official examples must stay teaching material even when left unchecked.
  await expect(page.getByRole('checkbox', { name: '教学或模拟资料（非真实企业账簿）' })).not.toBeChecked()
  const altered = structuredClone(packageData)
  altered.records[0].debit = '1.00'
  await page.getByLabel('业务资料文件', { exact: true }).setInputFiles({ name: filename, mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(altered)) })
  await page.getByRole('button', { name: '解析与预览' }).click()
  await expect(page.getByRole('alert')).toContainText('公开教学样例包与固定原件不一致')
  expect(await (await request.get(`/api/v1/books/${book.id}/documents`)).json()).toEqual([])
  expect(await (await request.get(`/api/v1/books/${book.id}/records`)).json()).toEqual([])
  await page.getByLabel('业务资料文件', { exact: true }).setInputFiles({ name: filename, mimeType: 'application/json', buffer: source })
  await page.getByRole('button', { name: '解析与预览' }).click()
  await page.getByRole('button', { name: '确认导入并建立快照' }).click()
  await expect(page.getByText('成功导入 3 条记录，跳过 0 条重复来源。')).toBeVisible()
  const records = await (await request.get(`/api/v1/books/${book.id}/records`)).json()
  expect(records).toHaveLength(3)
  expect(records.map((r: any) => [r.account, r.debit, r.credit]).sort()).toEqual([
    ['原材料', '9000.00', '0.00'], ['应交税费/应交增值税-进项税额', '1170.00', '0.00'], ['银行存款', '0.00', '10170.00']
  ].sort())
  const documents = await (await request.get(`/api/v1/books/${book.id}/documents`)).json()
  expect(documents[0].synthetic).toBe(true)
  const restored = await (await request.get(`/api/v1/documents/${documents[0].id}/file`)).body()
  expect(restored).toEqual(source)
  await page.locator('nav').getByRole('button', { name: /证据浏览/ }).click()
  await expect(page.getByRole('row').filter({ hasText: filename })).toContainText('教学／模拟资料')
})
