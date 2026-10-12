import { test, expect, type Page, type APIRequestContext } from '@playwright/test'

const endpoint='/api/v1/evaluations/numeric'
const sourceSha='a847fb7e0d61a3125a1e2909852df6b89f1ee64d2c5ff1bf689e332214deee51'
const scopeNotes=[
 '给定参考程序的算术重放，不是模型读题或会计准确率。',
 '首轮与结构化步骤复测是同一批公开dev题目，存在开发反馈；下一组也不是封存测试。',
 '错误、保留判断进入已评分分母；没有参考答案的样本另列，不能当作答错或答对。',
 '来源编号可定位和算术通过，不保证年份、单位、公式或业务语义正确。',
 '只读取随项目保存的历史摘要；刷新不运行模型，不代表新验证。费用是已知用量列表价格区间。',
]

// Fixed copies of the saved dev-summary counts. UI assertions below retain
// independent expected values instead of calculating expectations from fixtures.
const historicalResults={
 arithmetic_replay:{calculator_version:'financial-math-v3',records:883,executed:883,exact_matches:883,model_requests:0,source_sha256:sourceSha,created_at:'2026-10-08T13:00:12.084080+00:00'},
 model_pilots:[
  {id:'finqa-v1',title:'首轮程序字符串',prompt_version:'finqa-question-only-v1',sample_group:'首32题 · 开发试跑',samples:32,scorable:32,unscorable:0,answered:20,failed:12,abstained:0,exact_matches:9,exact_match_rate:0.28125,paid_requests:48,tokens:85597,created_at:'2026-10-08T13:17:09.118554+00:00',source_sha256:sourceSha,cost_min_usd:'0.009610848',cost_max_usd:'0.019221696'},
  {id:'finqa-v2',title:'结构化步骤',prompt_version:'finqa-question-only-v2',sample_group:'同32题 · 开发复测',samples:32,scorable:32,unscorable:0,answered:30,failed:2,abstained:0,exact_matches:21,exact_match_rate:0.65625,paid_requests:37,tokens:75142,created_at:'2026-10-08T13:28:22.448270+00:00',source_sha256:sourceSha,cost_min_usd:'0.008914296',cost_max_usd:'0.017828592'},
  {id:'finqa-v2-next32',title:'结构化步骤 · 下一组',prompt_version:'finqa-question-only-v2',sample_group:'下一组32题 · 公开dev',samples:32,scorable:32,unscorable:0,answered:27,failed:4,abstained:1,exact_matches:18,exact_match_rate:0.5625,paid_requests:41,tokens:84826,created_at:'2026-10-08T13:34:46.260390+00:00',source_sha256:sourceSha,cost_min_usd:'0.009598302',cost_max_usd:'0.019196604'},
 ],
 issues:[],scope_notes:scopeNotes,
}

type Reply={data:Record<string,unknown>}|{abort:true}

// Mount the real Vue component and workbench styles through the existing Vite
// server. Every API request is intercepted; unexpected routes/mutations fail.
// No App boot, business write, model dispatch or service launch occurs here.
async function mountNumeric(page:Page,request:APIRequestContext,replies:Reply[]){
 const component=await request.get('/src/components/NumericEvaluation.vue')
 expect(component.ok()).toBe(true)
 const source=await component.text()
 const vueImport=source.match(/from\s+["']([^"']*\/vue\.js[^"']*)["']/)?.[1]
 expect(vueImport).toBeTruthy()
 const apiRequests:{method:string;path:string}[]=[]
 const unexpectedRequests:string[]=[]
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/**',async route=>{
  const incoming=route.request()
  const path=new URL(incoming.url()).pathname
  apiRequests.push({method:incoming.method(),path})
  if(path!==endpoint||incoming.method()!=='GET'){
   unexpectedRequests.push(`${incoming.method()} ${path}`)
   await route.abort()
   return
  }
  const reply=replies[Math.min(apiRequests.length-1,replies.length-1)]!
  if('abort' in reply)await route.abort('failed')
  else await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(reply.data)})
 })
 await page.route('**/__component-test-numeric-evaluation',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><div class="page-content" id="component-root"></div><script type="module">
 import {createApp,h} from ${JSON.stringify(vueImport)};
 import '/src/style.css';
 import NumericEvaluation from '/src/components/NumericEvaluation.vue';
 createApp({render:()=>h(NumericEvaluation)}).mount('#component-root');
 </script></html>`}))
 await page.goto('/__component-test-numeric-evaluation')
 await expect(page.getByRole('heading',{name:'数值推理组件',exact:true})).toBeVisible()
 return {apiRequests,unexpectedRequests,errors}
}

function expectReadOnly(observed:Awaited<ReturnType<typeof mountNumeric>>,count:number){
 expect(observed.apiRequests).toEqual(Array.from({length:count},()=>({method:'GET',path:endpoint})))
 expect(observed.unexpectedRequests).toEqual([])
 expect(observed.errors).toEqual([])
}

test('历史成绩保留失败分母、精确费用与开发集边界，390px只滚动表格且刷新只读',async({page,request})=>{
 await page.setViewportSize({width:390,height:844})
 const observed=await mountNumeric(page,request,[{data:historicalResults}])
 await expect(page.locator('.replay-score>strong')).toHaveText('883 / 883')
 await expect(page.locator('.replay-facts')).toContainText('模型请求 0 次')
 await expect(page.locator('.replay-details')).toContainText('不能当作模型答题准确率')
 const rows=page.locator('.numeric-table tbody tr')
 await expect(rows).toHaveCount(3)
 await expect(rows.nth(0).locator('.score-fraction')).toHaveText('9 / 32')
 await expect(rows.nth(0).locator('td').nth(0)).toContainText('28.1%')
 await expect(rows.nth(0).locator('td').nth(1)).toContainText('20 / 32')
 await expect(rows.nth(0).locator('td').nth(2)).toContainText('12 / 0')
 await expect(rows.nth(0).locator('td').nth(3)).toContainText('48 次 / 85,597')
 await expect(rows.nth(1).locator('.score-fraction')).toHaveText('21 / 32')
 await expect(rows.nth(1).locator('td').nth(0)).toContainText('65.6%')
 await expect(rows.nth(2).locator('.score-fraction')).toHaveText('18 / 32')
 await expect(rows.nth(2).locator('td').nth(0)).toContainText('56.3%')
 await expect(rows.nth(2).locator('td').nth(2)).toContainText('4 / 1')
 await expect(page.locator('.numeric-model-heading')).toContainText('所有已评分失败保留在分母中')
 await expect(page.locator('.numeric-boundaries')).toContainText('下一组也不是封存测试')
 await expect(page.locator('.numeric-boundaries')).toContainText('刷新不运行模型，不代表新验证')
 await page.locator('.numeric-provenance summary').click()
 const provenance=page.locator('.numeric-provenance>div')
 await expect(provenance.nth(0)).toContainText('USD 0.009610848–0.019221696；不是实际账单。')
 await expect(provenance.nth(1)).toContainText('USD 0.008914296–0.017828592')
 await expect(provenance.nth(2)).toContainText('USD 0.009598302–0.019196604')
 await expect(provenance.nth(0)).toContainText(`数据 SHA256 ${sourceSha}`)
 const wrapper=page.getByLabel('可横向滚动的模型历史结果')
 await expect(wrapper).toHaveAttribute('tabindex','0')
 expect(await wrapper.evaluate(element=>element.scrollWidth>element.clientWidth)).toBe(true)
 await wrapper.evaluate(element=>{element.scrollLeft=element.scrollWidth})
 expect(await wrapper.evaluate(element=>element.scrollLeft)).toBeGreaterThan(0)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true)
 const lastCell=await rows.nth(0).locator('td').last().boundingBox()
 const scrollBox=await wrapper.boundingBox()
 expect(lastCell!.x+lastCell!.width).toBeLessThanOrEqual(scrollBox!.x+scrollBox!.width+1)
 await page.getByRole('button',{name:'刷新数值组件历史结果'}).click()
 await expect.poll(()=>observed.apiRequests.length).toBe(2)
 await expect(page.getByRole('button',{name:'刷新数值组件历史结果'})).toBeEnabled()
 await expect(rows.nth(0).locator('.score-fraction')).toHaveText('9 / 32')
 expectReadOnly(observed,2)
})

test('无参考答案和未知费用保留未知，实际零正确率仍显示0.0%',async({page,request})=>{
 const observed=await mountNumeric(page,request,[{data:{
  arithmetic_replay:null,
  model_pilots:[
   {...historicalResults.model_pilots[0],id:'unscorable',title:'无参考答案样本',samples:3,scorable:0,unscorable:3,answered:1,failed:0,abstained:2,exact_matches:0,exact_match_rate:null,paid_requests:3,tokens:0,cost_min_usd:null,cost_max_usd:null},
   {...historicalResults.model_pilots[0],id:'zero-score',title:'已评分零正确样本',samples:3,scorable:3,unscorable:0,answered:1,failed:1,abstained:1,exact_matches:0,exact_match_rate:0,paid_requests:0,tokens:0,cost_min_usd:'0',cost_max_usd:'0'},
  ],issues:[],scope_notes:scopeNotes,
 }}])
 await expect(page.getByText('尚无有效的参考程序重放摘要。',{exact:true})).toBeVisible()
 await expect(page.locator('.replay-band')).toHaveCount(0)
 const rows=page.locator('.numeric-table tbody tr')
 await expect(rows).toHaveCount(2)
 await expect(rows.nth(0).locator('.score-fraction')).toHaveText('0 / 0')
 await expect(rows.nth(0).locator('td').nth(0).locator('span')).toHaveText('—')
 await expect(rows.nth(0)).toContainText('3 题无参考答案')
 await expect(rows.nth(0).locator('td').nth(3)).toContainText('3 次 / 0')
 await expect(rows.nth(1).locator('.score-fraction')).toHaveText('0 / 3')
 await expect(rows.nth(1).locator('td').nth(0).locator('span')).toHaveText('0.0%')
 await expect(rows.nth(1).locator('td').nth(3)).toContainText('0 次 / 0')
 await page.locator('.numeric-provenance summary').click()
 await expect(page.locator('.numeric-provenance>div').nth(0)).toContainText('费用用量不完整或无法核验，保留未知。')
 await expect(page.locator('.numeric-provenance>div').nth(0)).not.toContainText('USD')
 await expect(page.locator('.numeric-provenance>div').nth(1)).toContainText('USD 0–0；不是实际账单。')
 await expect(page.locator('.numeric-evaluation')).not.toContainText(/NaN|Infinity/)
 expectReadOnly(observed,1)
})

test('空结果及缺失或校验失败摘要显示不可用，不生成成绩',async({page,request})=>{
 const observed=await mountNumeric(page,request,[{data:{
  arithmetic_replay:null,model_pilots:[],
  issues:[{report_id:'arithmetic-replay',code:'not_available'},{report_id:'finqa-v2',code:'invalid_or_unreadable'}],
  scope_notes:scopeNotes,
 }}])
 await expect(page.getByText('尚无有效的参考程序重放摘要。',{exact:true})).toBeVisible()
 await expect(page.getByText('尚无有效的模型试跑摘要，未生成模型成绩。',{exact:true})).toBeVisible()
 await expect(page.locator('.numeric-issues')).toContainText('部分摘要不可用')
 await expect(page.locator('.numeric-issues')).toContainText('arithmetic-replay：尚未提供')
 await expect(page.locator('.numeric-issues')).toContainText('finqa-v2：校验失败或无法读取')
 await expect(page.locator('.numeric-table,.replay-band,.numeric-provenance')).toHaveCount(0)
 await expect(page.locator('.numeric-evaluation')).not.toContainText('100.0%')
 expectReadOnly(observed,1)
})

test('网络失败可重试，刷新失败保留历史成绩并明确报错',async({page,request})=>{
 const observed=await mountNumeric(page,request,[{abort:true},{data:historicalResults},{abort:true}])
 const refresh=page.getByRole('button',{name:'刷新数值组件历史结果'})
 await expect(page.getByRole('alert')).toContainText('刷新只读取结果，不会调用模型。')
 await expect(page.locator('.numeric-table,.replay-band')).toHaveCount(0)
 await expect(refresh).toBeEnabled()
 await refresh.click()
 await expect(page.locator('.numeric-table tbody tr')).toHaveCount(3)
 await expect(page.getByRole('alert')).toHaveCount(0)
 await expect(refresh).toBeEnabled()
 await refresh.click()
 await expect(page.getByRole('alert')).toContainText('刷新只读取结果，不会调用模型。')
 await expect(page.locator('.numeric-table tbody tr')).toHaveCount(3)
 await expect(page.locator('.replay-score>strong')).toHaveText('883 / 883')
 await expect(page.locator('.numeric-table tbody tr').nth(0).locator('.score-fraction')).toHaveText('9 / 32')
 await expect(refresh).toBeEnabled()
 expectReadOnly(observed,3)
})
