import { test, expect } from '@playwright/test'

// A first-time user brings an ordinary Chinese voucher export: no source number, record type or
// accounting period columns, and the opposite side of each line left blank. The importer must take it,
// mark every derived value in the preview, and only then let the user confirm and run the checks.
const VOUCHER_EXPORT='﻿日期,凭证号,摘要,科目编码,科目名称,借方金额,贷方金额,发票号\n'+
 '2026/9/3,记-001,采购办公用品,6602,管理费用,1200.00,,FP20260903\n'+
 '2026/9/3,记-001,采购办公用品,2202,应付账款,,1200.00,FP20260903\n'+
 '2026/9/20,记-004,采购办公用品,6602,管理费用,1200.00,,FP20260903\n'+
 '2026/9/20,记-004,采购办公用品,2202,应付账款,,1200.00,FP20260903\n'

test('首次使用：导入普通凭证导出，推导字段逐项标明，确认后检查出一票多凭证',async({page,request})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 const created=await request.post('/api/v1/books',{data:{name:`自有凭证 ${Date.now()}`,framework:'CAS',period:'2026-09',currency:'CNY',accounts:['2202','6602'],locked_periods:[]}})
 expect(created.ok()).toBeTruthy();const book=await created.json()
 await page.goto('/');await page.evaluate(id=>localStorage.setItem('ledgerlens.book',id),book.id);await page.reload()
 await page.locator('nav').getByRole('button',{name:/数据接入/}).click()
 await page.getByLabel('业务资料文件',{exact:true}).setInputFiles({name:'九月凭证.csv',mimeType:'text/csv',buffer:Buffer.from(VOUCHER_EXPORT)})
 await page.getByRole('button',{name:'解析与预览'}).click()
 await expect(page.getByText('4 条记录 / 0 项错误')).toBeVisible()
 const summary=page.locator('.derived-summary')
 await expect(summary).toContainText('记录类型（4 行）')
 await expect(summary).toContainText('会计期间（4 行）')
 await expect(summary).toContainText('来源编号（4 行）')
 await expect(summary).toContainText('贷方金额（2 行）')
 await expect(summary).toContainText('借方金额（2 行）')
 await expect(page.locator('td.derived').first()).toContainText('推导')
 await expect(page.locator('td.derived').filter({hasText:'会计分录'}).first()).toBeVisible()
 await page.getByRole('button',{name:'确认导入并建立快照'}).click()
 await expect(page.getByText('成功导入 4 条记录，跳过 0 条重复来源。')).toBeVisible()
 const records=await (await request.get(`/api/v1/books/${book.id}/records`)).json()
 expect(records.map((r:{credit:string})=>r.credit).sort()).toEqual(['0.00','0.00','1200.00','1200.00'])
 await page.locator('nav').getByRole('button',{name:/月结工作台/}).click()
 await page.getByRole('button',{name:'执行月结检查'}).click()
 await expect(page.getByText('检查已完成，结果来自当前数据快照。')).toBeVisible()
 await expect(page.getByText('INVOICE_EVIDENCE')).toBeVisible()
 expect(errors).toEqual([])
})
