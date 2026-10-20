// Screenshot local HTML pages to PNG with the system Chrome through the project's Playwright.
// Usage: node scripts/render_pages.cjs jobs.json   where jobs.json is [{"html": "...", "png": "...", "width": 1440, "height": 900}]
const { createRequire } = require('module');
const fs = require('fs');
const path = require('path');
const projectRequire = createRequire(path.join(__dirname, '..', 'frontend', 'package.json'));
const { chromium } = projectRequire('@playwright/test');

(async () => {
  const jobs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const browser = await chromium.launch({ channel: 'chrome' });
  try {
    for (const job of jobs) {
      const page = await browser.newPage({ viewport: { width: job.width, height: job.height }, deviceScaleFactor: 1 });
      await page.goto('file:///' + path.resolve(job.html).replace(/\\/g, '/'), { waitUntil: 'networkidle' });
      await page.screenshot({ path: job.png, timeout: 60000 });
      await page.close();
      console.log('rendered', job.png);
    }
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(String(error).slice(0, 400)); process.exit(1); });
