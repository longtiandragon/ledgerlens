import { test, expect, type APIRequestContext } from '@playwright/test'

type CaseRow = { id:string; rule_id:string }

async function demoBook(request:APIRequestContext,name:string){
 const book=await (await request.post('/api/v1/books',{data:{name,framework:'CAS',period:'2026-09'}})).json()
 const demo=await (await request.post(`/api/v1/books/${book.id}/demo`)).json()
 const cases:CaseRow[]=(await (await request.post('/api/v1/check-runs',{data:{book_id:book.id,snapshot_id:demo.snapshot_id}})).json()).cases
 return {book,demo,cases}
}

test('处理闭环：由案件生成处理草稿，模拟消除本案发现，复核冻结所选方案与未决事项',async({page,request})=>{
 const {book,demo,cases}=await demoBook(request,'处理闭环 · 合成演示')
 const duplicate=cases.find(c=>c.rule_id==='DUPLICATE_INVOICE')!
 await page.goto(`/?book=${book.id}&case=${duplicate.id}`)
 const detail=page.locator('.investigation-detail')
 await expect(detail.getByRole('heading',{name:'发票重复确认',exact:true})).toBeVisible()
 await expect(detail.locator('.treatment-line')).toContainText('冲回重复确认凭证 JV-002')
 await expect(detail.getByLabel('未决事项')).toContainText('尚未选择模拟方案')
 await detail.getByRole('button',{name:'按处理草稿模拟'}).click()
 await expect(page.getByLabel('处理草稿')).toContainText('可测算草稿')
 await expect(page.getByLabel('第1行凭证号',{exact:true})).toHaveValue('SIM-REV-JV-002')
 await expect(page.getByLabel('第1行科目',{exact:true})).toHaveValue('1405')
 await expect(page.getByLabel('第1行贷方金额',{exact:true})).toHaveValue('100000.00')
 await page.getByRole('button',{name:'计算处理影响'}).click()
 await expect(page.getByRole('heading',{name:'模拟前后对比'})).toBeVisible()
 const comparison=page.getByLabel('调整前后发现对比')
 await expect(comparison).toContainText('本案发现已消除')
 await expect(comparison).toContainText('来自处理草稿“冲回重复确认凭证 JV-002”；未修改草稿分录')
 await expect(comparison).toContainText('已消除 1')
 await expect(comparison).toContainText('新引入 0')
 await page.locator('nav').getByRole('button',{name:/复核底稿/}).click()
 await expect(page.getByLabel('复核所依据的模拟方案').locator('option:checked')).toHaveText(/来自处理草稿 · 消除本案发现/)
 await expect(page.getByLabel('当前未决事项（按最新模拟方案计算）')).toContainText('当前没有未决事项')
 await page.getByLabel('复核结论').selectOption('accepted')
 await page.locator('textarea').fill('冲回草稿消除了重复确认，原账未改')
 await page.getByRole('button',{name:'保存复核意见',exact:true}).click()
 await expect(page.locator('.review-history')).toContainText('复核时未决事项 0 项')
 await expect(page.locator('.review-history')).toContainText('依据模拟方案')
 const workpaper=await (await request.get(`/api/v1/exports/cases/${duplicate.id}?format=json`)).json()
 expect(workpaper.reviews[0].simulation_id).toBe(workpaper.simulations[0].id)
 expect(workpaper.reviews[0].open_items).toEqual([])
 expect(workpaper.simulations[0].basis.source).toBe('treatment_draft')
 expect(workpaper.snapshot.id).toBe(demo.snapshot_id)
})

test('补证接续：迟到收货单补齐后重新检查，旧案件显示未再出现，接续核对案件在新快照复核',async({page,request})=>{
 const {book,cases}=await demoBook(request,'补证接续 · 合成演示')
 const gap=cases.find(c=>c.rule_id==='RECEIPT_GAP')!
 await request.post(`/api/v1/cases/${gap.id}/evidence-requests`,{data:{kind:'receipt',reference:'PO-001',description:'订单剩余收货单'}})
 await request.post(`/api/v1/books/${book.id}/supplement-demo`)
 await page.goto(`/?book=${book.id}&case=${gap.id}`)
 const lineage=page.locator('.investigation-detail').getByLabel('案件接续')
 await expect(lineage).toContainText('资料已更新，尚未重新检查')
 await lineage.getByRole('button',{name:'在最新快照重新检查'}).click()
 await expect(lineage).toContainText('后续检查未再出现')
 await lineage.getByRole('button',{name:/打开接续核对案件/}).click()
 const detail=page.locator('.investigation-detail')
 await expect(detail.getByRole('heading',{name:'上次检查的发现未再出现：收货证据尚未齐全',exact:true})).toBeVisible()
 await expect(detail).toContainText('不另行调查')
 await expect(detail.getByRole('button',{name:'启动调查'})).toHaveCount(0)
 await expect(detail.getByLabel('未决事项')).toContainText('前一案件待取得收货单：订单剩余收货单（已有候选资料，待人工确认）')
 await detail.getByLabel('案件接续').getByRole('button',{name:/前一案件/}).click()
 const requests=page.locator('.investigation-detail').getByLabel('补证事项')
 await expect(requests).toContainText('RCV-002')
 await requests.getByPlaceholder('例如：已核对原件与订单编号').fill('已核对迟到收货单原件')
 await requests.getByRole('button',{name:'确认这些记录即所需资料'}).click()
 await expect(requests).toContainText('已人工确认取得')
 await page.locator('.investigation-detail').getByLabel('案件接续').getByRole('button',{name:/打开接续核对案件/}).click()
 await expect(page.locator('.investigation-detail').getByLabel('未决事项')).toHaveCount(0)
 await page.locator('nav').getByRole('button',{name:/复核底稿/}).click()
 await expect(page.getByLabel('当前未决事项（按最新模拟方案计算）')).toContainText('当前没有未决事项')
 await page.getByLabel('复核结论').selectOption('accepted')
 await page.locator('textarea').fill('迟到收货单已人工确认，缺口不再出现')
 await page.getByRole('button',{name:'保存复核意见',exact:true}).click()
 await expect(page.locator('.review-history')).toContainText('复核时未决事项 0 项')
})

test('资金差异：未匹配流水生成需补科目的草稿，补齐后银行与账面一致',async({page,request})=>{
 const book=await (await request.post('/api/v1/books',{data:{name:'资金差异 · 合成演示',framework:'CAS',period:'2026-09',accounts:['1002','2202','6603']}})).json()
 const base={business_date:'2026-09-20',period:'2026-09',counterparty:'合成供应商乙',order_id:'PO-7'}
 const rows=[{...base,source_id:'B-PAY',kind:'bank',amount:'-1000.00',account:'1002',direction:'outgoing'},
  {...base,source_id:'B-FEE',kind:'bank',amount:'-15.00',account:'1002',direction:'outgoing',description:'银行手续费'},
  {...base,source_id:'J-PAY-1',kind:'journal',voucher_id:'J-PAY',account:'2202',debit:'1000.00',credit:'0.00'},
  {...base,source_id:'J-PAY-2',kind:'journal',voucher_id:'J-PAY',account:'1002',debit:'0.00',credit:'1000.00'}]
 const preview=await (await request.post('/api/v1/imports',{multipart:{book_id:book.id,synthetic:'true',file:{name:'funds.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(rows))}}})).json()
 const confirmed=await (await request.post(`/api/v1/imports/${preview.id}/confirm`)).json()
 const cases:CaseRow[]=(await (await request.post('/api/v1/check-runs',{data:{book_id:book.id,snapshot_id:confirmed.snapshot_id}})).json()).cases
 expect(cases.map(c=>c.rule_id).sort()).toEqual(['BANK_LEDGER_DIFFERENCE','BANK_UNMATCHED'])
 const difference=cases.find(c=>c.rule_id==='BANK_LEDGER_DIFFERENCE')!
 await page.goto(`/?book=${book.id}&case=${difference.id}`)
 const detail=page.locator('.investigation-detail')
 await expect(detail.locator('.treatment-line')).toContainText('需补充字段')
 await detail.getByRole('button',{name:'按处理草稿模拟'}).click()
 await expect(page.getByLabel('第2行科目',{exact:true})).toHaveValue('')
 await expect(page.getByLabel('第2行科目',{exact:true})).toHaveAttribute('placeholder','必填：依据原始凭证填写')
 await page.getByRole('button',{name:'计算处理影响'}).click()
 await expect(page.locator('.notice.error')).toBeVisible()
 await page.getByLabel('第2行科目',{exact:true}).fill('6603')
 await page.getByRole('button',{name:'计算处理影响'}).click()
 const comparison=page.getByLabel('调整前后发现对比')
 await expect(comparison).toContainText('本案发现已消除')
 await expect(comparison).toContainText('补齐 1 个必填字段')
 await expect(comparison).toContainText('正常观察 1')
 await expect(comparison).toContainText('资金发生额及候选关联一致')
 expect((await (await request.get(`/api/v1/books/${book.id}/records`)).json())).toHaveLength(4)
})
