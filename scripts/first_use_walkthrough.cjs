// First-use walkthrough of a running LedgerLens, driven only by visible labels (docs/AI_FIRST_USE_TRIAL.md).
// Usage: node scripts/first_use_walkthrough.cjs [ui_url]   (default http://127.0.0.1:5188; needs the system Chrome)
// Use a fresh instance: it creates a book, loads the demo and imports docs/trial/judge-voucher-export.csv.
const { createRequire } = require('module');
const fs = require('fs');
const path = require('path');
const ROOT = path.join(__dirname, '..');
const { chromium } = createRequire(path.join(ROOT, 'frontend', 'package.json'))('@playwright/test');
const OUT = path.join(ROOT, 'runtime', 'first-use');
const CSV = path.join(ROOT, 'docs', 'trial', 'judge-voucher-export.csv');
const URL = process.argv[2] || process.env.UI_URL || 'http://127.0.0.1:5188';
(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  const log = []; const step = async (name, fn) => { const t = Date.now(); try { await fn(); log.push(`ok   ${name} (${Date.now() - t} ms)`); } catch (e) { log.push(`FAIL ${name}: ${String(e).split('\n')[0].slice(0, 200)}`); await page.screenshot({ path: `${OUT}/fail-${name}.png` }); } };
  const nav = name => page.locator('nav').getByRole('button', { name: new RegExp(name) }).click();
  fs.mkdirSync(OUT, { recursive: true });
  await page.goto(URL);
  await step('建账套', async () => { await page.getByRole('button', { name: '建立第一个账套' }).click(); await page.getByPlaceholder('例如：青禾商贸 · 演示账套').fill('复测 · 演示'); await page.getByRole('button', { name: '保存账套' }).click(); await page.getByText('账套已建立').waitFor(); });
  await step('载入演示', async () => { await page.getByRole('button', { name: /载入合成演示账套/ }).click(); await page.getByText('合成账套已载入').waitFor(); });
  await step('月结检查', async () => { await page.getByRole('button', { name: '执行月结检查' }).click(); await page.getByText('发票重复确认').first().waitFor(); });
  await step('启动调查', async () => { await page.getByText('发票重复确认').first().click(); await page.getByRole('button', { name: '启动调查' }).click(); await page.getByText('已核验结果').waitFor({ timeout: 30000 }); });
  await step('浏览证据回顶部', async () => { await page.mouse.wheel(0, 1500); await page.getByRole('button', { name: /浏览证据/ }).click(); await page.waitForTimeout(500); const y = await page.evaluate(() => scrollY); if (y !== 0) throw new Error('scrollY=' + y); });
  await step('模拟冲回', async () => { await nav('模拟处理'); await page.getByRole('button', { name: /新增分录/ }).click(); const i = page.locator('input'); await i.nth(5).fill('100000.00'); await i.nth(10).fill('1405'); await i.nth(12).fill('100000.00'); await page.getByRole('button', { name: /计算处理影响/ }).click(); await page.getByText('借贷平衡').first().waitFor(); });
  await step('保存复核', async () => { await nav('复核底稿'); await page.locator('textarea').fill('模拟冲回重复确认后借贷平衡。'); await page.getByRole('button', { name: '保存复核意见' }).click(); await page.getByText(/ UTC/).first().waitFor(); await page.screenshot({ path: `${OUT}/review.png` }); });
  await step('迟到收货单使旧复核失效', async () => { await nav('数据接入'); await page.getByRole('button', { name: /补充迟到收货单/ }).click(); await page.getByText('已加入迟到收货资料').waitFor(); await nav('复核底稿'); await page.getByText('此快照仅可查看，不能接受新的复核').waitFor(); });
  await step('导入自有凭证', async () => { await nav('数据接入'); await page.getByLabel('业务资料文件', { exact: true }).setInputFiles(CSV); await page.getByRole('button', { name: '解析与预览' }).click(); await page.getByText('9 条记录 / 0 项错误').waitFor(); await page.locator('.derived-summary').scrollIntoViewIfNeeded(); await page.screenshot({ path: `${OUT}/import-preview.png` }); await page.getByRole('button', { name: '确认导入并建立快照' }).click(); await page.getByText(/成功导入 9 条记录/).waitFor(); });
  await step('导入后检查', async () => { await nav('月结工作台'); await page.getByRole('button', { name: '执行月结检查' }).click(); await page.getByText('检查已完成').waitFor(); await page.screenshot({ path: `${OUT}/after-import-check.png` }); });
  console.log(log.join('\n')); console.log('page errors: ' + JSON.stringify(errors));
  await browser.close();
})();
