import { test, expect, type Page, type APIRequestContext } from '@playwright/test'

// Mount only the real component through Vite. No App boot, business API,
// model request, task creation, server launch or restart takes place here.
async function mountResult(page:Page, request:APIRequestContext, result:Record<string,unknown>, evidence:unknown[]=[]){
 const response=await request.get('/src/components/InvestigationResult.vue')
 expect(response.ok()).toBe(true)
 const source=await response.text()
 const vueImport=source.match(/from\s+["']([^"']*\/vue\.js[^"']*)["']/)?.[1]
 expect(vueImport).toBeTruthy()
 const payload=JSON.stringify({result:{status:'insufficient',claims:[],hypotheses:[],...result},evidence}).replace(/</g,'\\u003c')
 await page.route('**/__component-test-investigation-result',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><div id="component-root"></div><script type="module">
 import {createApp,h} from ${JSON.stringify(vueImport)};
 import InvestigationResult from '/src/components/InvestigationResult.vue';
 const props=${payload};
 createApp({render:()=>h(InvestigationResult,{...props,onInspect:evidence=>{window.__inspectedEvidenceId=evidence.id;}})}).mount('#component-root');
 </script></html>`}))
 const apiRequests:string[]=[]
 page.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/api/'))apiRequests.push(request.url())})
 await page.goto('/__component-test-investigation-result')
 await expect(page.locator('.investigation-results')).toBeVisible()
 return apiRequests
}

const selectedQuery={query_id:'a'.repeat(64),tool:'get_business_chain',pair_count:1,cost_units:3,
 partition:{到账:['h1'],未到账:['h2']},unknown_hypothesis_ids:['h3'],unpredicted_hypothesis_ids:['h4']}

test('主动选择显示候选分区、服务端成本和执行来源，并复用证据查看事件',async({page,request})=>{
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message))
 const apiRequests=await mountResult(page,request,{evidence_selections:[{
  hypotheses:[{hypothesis_id:'h1',explanation:'可能是分批付款'},{hypothesis_id:'h2',explanation:'可能是缺少流水'}],
  selected_query:selectedQuery,
  candidates:[{...selectedQuery,proposal_query_id:'bank-source',eligible:true,exclusion_reason:null}],
  selection:{action:'execute_query',status:'selected',reason:'按可区分解释对数和成本单位选择。'},
  execution:{performed:true,status:'executed',record_evidence_ids:['ev-1','ev-not-loaded']},
 }]},[{id:'ev-1',record_id:'record-1',source_id:'BANK-001',kind:'bank',locator:'第2行',document_id:'doc-1',document_name:'bank.csv',record:{}}])
 const selection=page.locator('.selection-card')
 await expect(selection).toContainText('可能是分批付款')
 await expect(selection).toContainText('可能是缺少流水')
 await expect(selection).toContainText('已执行当前快照查询')
 await expect(selection.locator('.selection-score strong').nth(0)).toHaveText('1')
 await expect(selection.locator('.selection-score strong').nth(1)).toHaveText('3')
 await expect(selection).toContainText('成本单位不是货币')
 await expect(page.locator('.evidence-selection-section')).toContainText('既不是校准概率，也不是会计事实')
 await selection.locator('.candidate-queries summary').click()
 await expect(selection.locator('.partition')).toContainText('到账')
 await expect(selection.locator('.partition')).toContainText('h1')
 await expect(selection).toContainText('结果未知：h3（不计可区分对）')
 await expect(selection).toContainText('尚未预测：h4（不计可区分对）')
 await expect(selection.locator('.unloaded-evidence')).toContainText('ev-not-loaded · 未载入')
 await expect(selection.locator('.execution-evidence .evidence-chip')).toHaveCount(1)
 await selection.getByRole('button',{name:'BANK-001',exact:true}).click()
 expect(await page.evaluate(()=>(window as unknown as {__inspectedEvidenceId:string}).__inspectedEvidenceId)).toBe('ev-1')
 expect(apiRequests).toEqual([])
 expect(errors).toEqual([])
})

test('无解选择明确补证未取得和新快照要求，不显示伪造查询结果',async({page,request})=>{
 const apiRequests=await mountResult(page,request,{evidence_selections:[{
  hypotheses:[{hypothesis_id:'h1',explanation:'资料缺失'}],selected_query:null,candidates:[],
  selection:{action:'request_evidence',status:'insufficient',reason:'没有可区分查询',evidence_request:{description:'请补充收货单'}},
  execution:{performed:false,status:'evidence_requested',requires_new_snapshot:true,retrieved:false},
 }]})
 const selection=page.locator('.selection-card')
 await expect(selection).toContainText('补证请求')
 await expect(selection).toContainText('补充资料尚未取得')
 await expect(selection).toContainText('请补充收货单')
 await expect(selection).toContainText('补充资料后需创建新快照，再继续调查')
 await expect(selection.locator('.selection-score')).toHaveCount(0)
 await expect(selection.locator('.execution-evidence')).toHaveCount(0)
 expect(apiRequests).toEqual([])
})

test('provider用量保留实际零值，未报告字段不显示零或推算金额',async({page,request})=>{
 const apiRequests=await mountResult(page,request,{model:'test-provider',model_requests:[{
  request_index:1,provider_model:'provider-model-1',request_usage:{prompt_tokens:100,completion_tokens:0,prompt_cache_hit_tokens:0,prompt_cache_miss_tokens:null,total_tokens:100},
 },{request_index:2,provider_model:null,request_usage:null}]})
 await page.locator('.provider-usage summary').click()
 await expect(page.locator('.provider-usage summary')).toContainText('已报告 2 次返回')
 const rows=page.locator('.usage-table tbody tr')
 await expect(rows).toHaveCount(2)
 const first=rows.nth(0).locator('td')
 await expect(first.nth(0)).toHaveText('provider-model-1')
 await expect(first.nth(1)).toHaveText('100')
 await expect(first.nth(2)).toHaveText('0')
 await expect(first.nth(3)).toHaveText('0')
 await expect(first.nth(4)).toHaveText('未报告')
 await expect(first.nth(5)).toHaveText('100')
 for(const cell of await rows.nth(1).locator('td').all())await expect(cell).toHaveText('未报告')
 await expect(page.locator('.provider-usage')).toContainText('失败或未返回请求的用量可能未知')
 await expect(page.locator('.provider-usage')).not.toContainText('¥')
 expect(apiRequests).toEqual([])
})

test('旧运行缺少逐请求字段时显示未报告，不补成零次返回',async({page,request})=>{
 const apiRequests=await mountResult(page,request,{model:'legacy-provider',usage:{calls:2,tokens:40,tools:1}})
 await page.locator('.provider-usage summary').click()
 await expect(page.locator('.provider-usage')).toContainText('尚未报告逐次用量')
 await expect(page.locator('.provider-usage summary')).not.toContainText('0 次返回')
 await expect(page.locator('.usage-table')).toHaveCount(0)
 expect(apiRequests).toEqual([])
})
