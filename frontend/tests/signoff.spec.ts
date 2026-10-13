import { test, expect, type APIRequestContext } from '@playwright/test'

// Requires an API started with AUTH_MODE=multi_user and the accounts paul (preparer),
// rita (reviewer + approver) and amy (approver) created through `python -m app.manage_users`.
const PASSWORD = process.env.SIGNOFF_PASSWORD || ''
test.skip(process.env.MULTI_USER_TESTS !== '1' || !PASSWORD, '需要多用户模式的 API 与预建账户（MULTI_USER_TESTS=1）')

async function bearer(request:APIRequestContext, username:string) {
  const response = await request.post('/api/v1/auth/login', { data: { username, password: PASSWORD } })
  expect(response.ok()).toBe(true)
  return { Authorization: `Bearer ${(await response.json()).token}` }
}

async function signIn(page:import('@playwright/test').Page, username:string, caseTitle:string) {
  await expect(page.getByRole('heading', { name: '登录账证智核' })).toBeVisible()
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(PASSWORD)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '登录账证智核' })).toBeHidden()
  // Initialisation opens the case named in the URL on the investigation page; navigating
  // before it finishes would be undone by that final step.
  await expect(page.getByRole('heading', { name: '异常调查', level: 1 })).toBeVisible()
  await expect(page.getByRole('heading', { level: 2, name: caseTitle })).toBeVisible()
}

test('调查、复核与审批由不同账户完成，签署链完整可追溯', async ({ page, request }) => {
  expect((await request.get('/api/v1/books')).status()).toBe(401)
  const paul = await bearer(request, 'paul')
  const book = await (await request.post('/api/v1/books', { headers: paul, data: { name: '审签浏览器验收 · 合成', framework: 'CAS', period: '2026-09' } })).json()
  const demo = await (await request.post(`/api/v1/books/${book.id}/demo`, { headers: paul })).json()
  const checks = await (await request.post('/api/v1/check-runs', { headers: paul, data: { book_id: book.id, snapshot_id: demo.snapshot_id } })).json()
  const caseId = checks.cases[0].id
  const caseTitle = checks.cases[0].title
  expect((await request.post(`/api/v1/cases/${caseId}/investigate`, { headers: paul, data: { mode: 'rules' } })).ok()).toBe(true)
  const ownReview = await request.post('/api/v1/reviews', { headers: paul, data: { case_id: caseId, expected_snapshot_id: demo.snapshot_id, decision: 'accepted', comment: '' } })
  expect(ownReview.status()).toBe(403)   // preparer lacks the reviewer duty

  await page.goto(`/?book=${book.id}&case=${caseId}`)
  await signIn(page, 'rita', caseTitle)
  await page.locator('nav').getByRole('button', { name: /复核底稿/ }).click()
  await page.getByLabel('复核结论').selectOption('accepted')
  await page.getByLabel('意见与未决事项').fill('规则结果与来源证据核对一致。')
  await page.getByRole('button', { name: '保存复核意见', exact: true }).click()
  await expect(page.getByText('复核意见已保存，并绑定当前案件快照。')).toBeVisible()
  const chain = page.getByRole('region', { name: '复核与审批签署链' })
  await expect(chain.getByText('复核 · 接受')).toBeVisible()
  await expect(chain.getByText('Rita', { exact: true })).toBeVisible()
  await expect(chain.getByText('复核人不能审批自己的复核（不相容职务分离）。')).toBeVisible()
  await expect(chain.getByRole('button', { name: '审批签发' })).toHaveCount(0)

  await page.getByRole('button', { name: '退出', exact: true }).click()
  await signIn(page, 'amy', caseTitle)
  await page.locator('nav').getByRole('button', { name: /复核底稿/ }).click()
  await chain.getByLabel('审批意见').fill('同意签发。')
  await chain.getByRole('button', { name: '审批签发' }).click()
  await expect(chain.getByText('已审批签发，签署记录已追加。')).toBeVisible()
  await expect(chain.getByText('Amy', { exact: true })).toBeVisible()
  const amy = await bearer(request, 'amy')
  const detail = await (await request.get(`/api/v1/cases/${caseId}`, { headers: amy })).json()
  expect(detail.approvals.map((a:{decision:string}) => a.decision)).toEqual(['approved'])
  expect(Object.values(detail.actors).map((a:any) => a.username).sort()).toEqual(['amy', 'paul', 'rita'])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
