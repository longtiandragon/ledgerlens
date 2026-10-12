import { test, expect, type Page, type APIRequestContext, type Route } from '@playwright/test'

const sha = 'a'.repeat(64)
const evidence = { id: 'ev-A', record_id: 'r-A', source_id: '原始订单-A', kind: 'order', locator: { row: 1 }, document_id: 'doc-A', document_name: '原始资料-A.json', record: { amount: '9007199254740993.01' } }
const captured = (run = 'run-A', changes: Record<string, unknown> = {}) => ({
  status: 'captured', digest: sha, stale: false, stale_reasons: [], notice: 'Frozen context',
  payload: { schema_version: 'investigation-policy-context-v1', run_id: run, book_id: 'book-A', snapshot_id: 'snapshot-A', snapshot_digest: sha, captured_at: '2026-10-09T08:00:00+00:00',
    policy_rule_identity: { version: 'policy-consistency-v1', source_sha256: sha }, observation_manifest: [{ id: 'obs-A', digest: sha, logical_key: '日常购进商品', version: 1 }], notice: '固定人工条件与检查结果。', checks: [{ id: 'check-A', created_at: '2026-10-09T07:59:00+00:00', observation_id: 'obs-A', pack_key: 'policy-consistency-v1:' + sha, observation_digest: sha, artifact_digest: sha,
      binding: { source_cutoff: '2026-10-02T00:00:00+00:00', observation_version: 1, observation: { conditions: { held_for_sale: { value: 'yes', confirmation: 'human_confirmed', evidence_ids: ['ev-A'] } } }, evidence_locators: [{ evidence_id: 'ev-A', source_id: '原始订单-A', document_name: '原始资料-A.json', locator: { row: 1 } }] },
      result: { title: '待售商品有限检查', status: 'normal', reason: '人工确认条件与保存的程序结果一致。', expected_treatment: 'inventory', reported_treatment: 'inventory', missing_conditions: [], source: { source_id: 'mof-qa-3861813', url: 'https://kjs.mof.gov.cn/zt/kjzzss/sswd/chzzsswd/202301/t20230103_3861813.htm', locator: 'div.TRS_Editor' }, scope_note: '不代表真实企业会计准确率。' } }] }, ...changes,
})

async function mount(page: Page, request: APIRequestContext, handler: (route: Route) => Promise<void>) {
  const module = await request.get('/src/components/InvestigationPolicyContext.vue')
  expect(module.ok()).toBe(true)
  const vue = (await module.text()).match(/from\s+["']([^"']*\/vue\.js[^"']*)["']/)?.[1]
  expect(vue).toBeTruthy()
  const errors: string[] = [], methods: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/**', async route => { methods.push(route.request().method()); await handler(route) })
  await page.route('**/__component-test-investigation-policy', route => route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><button id="switch" type="button">切换到B运行</button><div id="root" style="max-width:1100px;margin:auto;padding:14px"></div><output id="inspected"></output><script type="module">
    import {createApp,h,ref} from ${JSON.stringify(vue)};import '/src/style.css';import Component from '/src/components/InvestigationPolicyContext.vue';
    const id=ref('run-A');document.getElementById('switch').onclick=()=>{id.value='run-B'};
    createApp({render:()=>h(Component,{runId:id.value,evidence:${JSON.stringify([evidence])},interpretations:[{text:'模型解释仍待复核',policy_check_ids:['check-A']}],onInspect:e=>{document.getElementById('inspected').textContent=e.record.amount}})}).mount('#root');
  </script></html>` }))
  await page.goto('/__component-test-investigation-policy')
  await expect(page.getByRole('heading', { name: '本次调查的政策依据' })).toBeVisible()
  return { errors, methods }
}

test('冻结依据区分两种时间，引用可返回原记录且金额字符串完整', async ({ page, request }) => {
  const audit = await mount(page, request, route => route.fulfill({ json: captured() }))
  await expect(page.getByText('2026-10-02 00:00:00 UTC')).toBeVisible()
  await expect(page.getByText('2026-10-09 08:00:00 UTC')).toBeVisible()
  await page.getByText('待售商品有限检查', { exact: true }).click()
  await expect(page.getByText('是 · 人工确认')).toBeVisible()
  await page.getByRole('button', { name: '原始订单-A' }).click()
  await expect(page.locator('#inspected')).toHaveText('9007199254740993.01')
  await expect(page.getByRole('link', { name: '财政部依据 · mof-qa-3861813' })).toHaveAttribute('href', /^https:\/\/kjs\.mof\.gov\.cn\//)
  await expect(page.getByRole('heading', { name: '模型政策解释 · 待复核' })).toBeVisible()
  expect(audit.methods).toEqual(['GET']); expect(audit.errors).toEqual([])
})

test('校验拒绝显示失败，不能当作无政策结果，刷新可以恢复', async ({ page, request }) => {
  let failed = true
  const audit = await mount(page, request, route => route.fulfill(failed ? { status: 409, json: { detail: '保存的政策绑定内容已变化' } } : { json: captured() }))
  await expect(page.getByRole('alert')).toContainText('保存的政策绑定内容已变化')
  await expect(page.getByText('本次没有可提供的已保存政策检查。', { exact: false })).toHaveCount(0)
  failed = false; await page.getByRole('button', { name: '刷新版本状态' }).click()
  await expect(page.getByText('待售商品有限检查', { exact: true })).toBeVisible()
  expect(audit.methods.every(method => method === 'GET')).toBe(true); expect(audit.errors).toEqual([])
})

test('旧运行不补入今天依据，过期上下文只读且危险来源不可点击', async ({ page, request }) => {
  const old = captured('run-B', { stale: true, stale_reasons: ['policy_observations_changed'] })
  old.payload.checks[0].result.source.url = 'javascript:alert(1)'
  const audit = await mount(page, request, route => route.fulfill({ json: route.request().url().includes('run-A') ? { status: 'legacy_uncaptured', payload: null, digest: null, stale: false, stale_reasons: [], notice: 'Legacy' } : old }))
  await expect(page.getByText('此历史运行未保存政策检查上下文', { exact: false })).toBeVisible()
  await expect(page.getByText('待售商品有限检查', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '切换到B运行' }).click()
  await expect(page.getByText('已有后续变更，此上下文供历史回查。', { exact: false })).toContainText('条件版本已更新')
  await page.getByText('待售商品有限检查', { exact: true }).click()
  await expect(page.getByText('来源网址未通过校验')).toBeVisible()
  await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0)
  expect(audit.errors).toEqual([])
})

test('切换运行后迟到响应不能覆盖当前运行，390px无横向溢出', async ({ page, request }) => {
  let release!: () => void, complete!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  const delivered = new Promise<void>(resolve => { complete = resolve })
  await page.setViewportSize({ width: 390, height: 844 })
  const audit = await mount(page, request, async route => {
    if (route.request().url().includes('run-A')) { await pending; await route.fulfill({ json: captured() }); complete() }
    else { const empty = captured('run-B'); empty.payload.snapshot_id = 'snapshot-B'; empty.payload.checks = []; empty.payload.observation_manifest = []; await route.fulfill({ json: empty }) }
  })
  await page.getByRole('button', { name: '切换到B运行' }).click()
  await expect(page.getByText('snapshot-B', { exact: true })).toBeVisible()
  release(); await delivered
  await expect(page.getByText('待售商品有限检查', { exact: true })).toHaveCount(0)
  await expect(page.getByText('本次没有可提供的已保存政策检查。', { exact: false })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(audit.errors).toEqual([])
})
