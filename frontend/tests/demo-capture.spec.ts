import { test, expect, type Locator, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

// Screenshots and an optional captioned recording for the README, the introduction and the
// demo video. Skipped in normal runs; scripts/capture_demo.py runs it against a fresh, isolated
// API with no model key, so every step uses rules mode and labelled synthetic data.
const capture = process.env.DEMO_CAPTURE === '1'
const recording = process.env.DEMO_VIDEO === '1'
const out = process.env.DEMO_OUTPUT || path.resolve('../runtime/demo-capture')
const pause = Number(process.env.DEMO_PAUSE_MS || '0')
// With DEMO_SCRIPT, captions and hold times come from the narration build (scripts/build_demo_video.py):
// each beat holds for its narration, and timeline.json records when every beat started.
const script: Record<string, { caption?: string, hold_ms?: number }> =
  process.env.DEMO_SCRIPT ? JSON.parse(fs.readFileSync(process.env.DEMO_SCRIPT, 'utf8')).beats : {}
const timeline: { id: string, start_ms: number }[] = []
let started = 0

test.skip(!capture, 'Demo capture runs only through scripts/capture_demo.py')
test.use({ viewport: { width: 1440, height: 900 },
           video: recording ? { mode: 'on', size: { width: 1440, height: 900 } } : 'off' })

async function caption(page: Page, text: string) {
  await page.evaluate((value) => {
    let bar = document.getElementById('demo-caption')
    if (!bar) {
      bar = document.createElement('div')
      bar.id = 'demo-caption'
      Object.assign(bar.style, { position: 'fixed', left: '50%', bottom: '28px', transform: 'translateX(-50%)',
        maxWidth: '1100px', padding: '10px 22px', borderRadius: '8px', background: 'rgba(18, 46, 40, 0.88)',
        color: '#ffffff', font: '600 22px "Microsoft YaHei", "PingFang SC", sans-serif', zIndex: '99999',
        pointerEvents: 'none', textAlign: 'center' })
      document.body.appendChild(bar)
    }
    bar.textContent = value
    bar.style.display = value ? 'block' : 'none'
  }, text)
}

async function beat(page: Page, id: string, text: string) {
  if (!recording) return
  await caption(page, script[id]?.caption ?? text)
  timeline.push({ id, start_ms: Date.now() - started })
  await page.waitForTimeout(script[id]?.hold_ms ?? pause)
}

async function toTop(page: Page, target: Locator) {
  await target.evaluate((el) => el.scrollIntoView({ block: 'start' }))
  await page.waitForTimeout(300)
}

async function dismiss(page: Page) {
  const close = page.getByRole('button', { name: '关闭提示' })
  if (await close.isVisible()) await close.click()
}

async function shot(page: Page, name: string) {
  if (!recording) await page.screenshot({ path: path.join(out, `${name}.png`) })
}

test('演示：建账、检查、调查、证据、模拟、复核与资料更新', async ({ page }) => {
  // Each beat comes after the screen it describes, so the narration always matches what is shown.
  started = Date.now()
  test.setTimeout(10 * 60_000)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '月结工作台', exact: false })).toBeVisible()
  await page.getByRole('button', { name: '新建账套', exact: true }).click()
  await page.getByLabel('账套名称', { exact: true }).fill('演示账套（合成数据）')
  await beat(page, 'b01', '新建企业会计准则账套，期间 2026-09')
  await page.getByRole('button', { name: '保存账套', exact: true }).click()
  await expect(page.getByText('账套已建立，可以开始导入资料。')).toBeVisible()
  await page.getByRole('button', { name: '载入合成演示账套', exact: true }).click()
  await expect(page.getByText('合成账套已载入。请执行月结检查，生成真实计算的异常案件。')).toBeVisible()
  await beat(page, 'b02', '载入标注为合成的十万元采购演示资料')
  await page.getByRole('button', { name: '执行月结检查', exact: true }).click()
  await expect(page.getByText('检查已完成，结果来自当前数据快照。')).toBeVisible()
  await beat(page, 'b03', '执行月结检查：结果来自当前固定的数据快照')
  await shot(page, '01-workbench')

  await page.locator('.issue-item').filter({ hasText: '发票重复确认' }).click()
  await expect(page.locator('.investigation-detail').getByRole('heading', { name: '发票重复确认', exact: true })).toBeVisible()
  await beat(page, 'b04', '用确定性规则调查，不调用模型')
  await page.getByRole('button', { name: '启动调查', exact: true }).click()
  await expect(page.getByRole('heading', { name: '已核验结果' })).toBeVisible({ timeout: 45_000 })
  await toTop(page, page.getByRole('heading', { name: '已核验结果' }))
  await beat(page, 'b05', '每条结论都带着可回查的引用')
  await shot(page, '02-investigation')

  await page.getByRole('button', { name: '浏览证据', exact: true }).click()
  await expect(page.locator('.evidence-graph')).toBeVisible()
  await beat(page, 'b06', '证据关系图：记录、单据和规则如何相连')
  await shot(page, '03-evidence')
  await page.getByRole('button', { name: '查看记录', exact: false }).first().click()
  await expect(page.getByText('来源证据详情', { exact: true })).toBeVisible()
  await beat(page, 'b07', '点开即可看到原始来源编号和字段')
  await shot(page, '04-source-record')
  await page.getByRole('button', { name: '关闭', exact: true }).click()

  await page.locator('nav').getByRole('button', { name: /模拟处理/ }).click()
  await page.getByLabel('关联发票号（可选）').fill('INV-001')
  await page.getByLabel('第1行科目', { exact: true }).fill('2202')
  await page.getByLabel('第1行借方金额', { exact: true }).fill('100000.00')
  await page.getByLabel('第1行贷方金额', { exact: true }).fill('0.00')
  await page.getByRole('button', { name: '新增分录' }).click()
  await page.getByLabel('第2行科目', { exact: true }).fill('1405')
  await page.getByLabel('第2行借方金额', { exact: true }).fill('0.00')
  await page.getByLabel('第2行贷方金额', { exact: true }).fill('100000.00')
  await beat(page, 'b08', '在隔离模拟中输入调整草稿')
  await page.getByRole('button', { name: '计算处理影响' }).click()
  await expect(page.getByRole('heading', { name: '模拟前后对比' })).toBeVisible()
  await toTop(page, page.getByRole('heading', { name: '模拟前后对比' }))
  await beat(page, 'b09', '程序逐分复算：借贷平衡，原始账簿不变')
  await shot(page, '05-simulation')

  await page.locator('nav').getByRole('button', { name: /复核底稿/ }).click()
  await page.getByLabel('意见与未决事项').fill('合成案例规则与来源核对完成。调整草稿仅供模拟。')
  await page.getByRole('button', { name: '保存复核意见', exact: true }).click()
  await expect(page.getByText('复核意见已保存，并绑定当前案件快照。')).toBeVisible()
  await beat(page, 'b10', '保存复核意见，绑定当前案件快照')
  await shot(page, '06-review')

  await page.locator('nav').getByRole('button', { name: /数据接入/ }).click()
  await page.getByRole('button', { name: /补充迟到收货单/ }).click()
  await expect(page.getByText('已加入迟到收货资料并创建新快照，请重新执行检查。')).toBeVisible()
  await beat(page, 'b11', '迟到的收货单到了：生成新快照')
  await page.locator('nav').getByRole('button', { name: /复核底稿/ }).click()
  await expect(page.getByRole('button', { name: '保存复核意见', exact: true })).toBeDisabled()
  await expect(page.getByText(/当前资料或政策已经更新/)).toBeVisible()
  await beat(page, 'b12', '旧复核自动失效，不能用新资料改写历史判断')
  await shot(page, '07-review-invalidated')

  await page.locator('nav').getByRole('button', { name: /账套与政策/ }).click()
  await expect(page.getByRole('heading', { name: '规则覆盖矩阵' })).toBeVisible()
  await dismiss(page)
  await toTop(page, page.getByRole('heading', { name: '先确认条件，再核对处理。' }))
  await beat(page, 'b13', '政策检查：33 条来自财政部实施问答的有限规则')
  await shot(page, '08-policy')

  await page.locator('nav').getByRole('button', { name: /评测中心/ }).click()
  await expect(page.getByRole('heading', { name: '规则基准记录' })).toBeVisible()
  await dismiss(page)
  await toTop(page, page.locator('#root-cause-title'))
  await beat(page, 'b14', '评测中心：模型效果如实展示，包括负面结果')
  await shot(page, '09-evaluation')
  if (recording) {
    await caption(page, '')
    fs.writeFileSync(path.join(out, 'timeline.json'),
                     JSON.stringify({ beats: timeline, end_ms: Date.now() - started }, null, 2))
  }
})
