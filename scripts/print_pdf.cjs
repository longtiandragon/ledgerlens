// Print a local HTML file to an A4 PDF with the system Chrome through the project's Playwright.
// Usage: node scripts/print_pdf.cjs <input.html> <output.pdf> [footer text]
const { createRequire } = require('module');
const path = require('path');
const projectRequire = createRequire(path.join(__dirname, '..', 'frontend', 'package.json'));
const { chromium } = projectRequire('@playwright/test');

(async () => {
  const [input, output, footer = ''] = process.argv.slice(2);
  const browser = await chromium.launch({ channel: 'chrome' });
  try {
    const page = await browser.newPage();
    await page.goto('file:///' + path.resolve(input).replace(/\\/g, '/'), { waitUntil: 'networkidle' });
    await page.pdf({
      path: output, format: 'A4', printBackground: true, preferCSSPageSize: true,
      displayHeaderFooter: true, headerTemplate: '<span></span>',
      footerTemplate: '<div style="width:100%;font-size:8px;color:#6b7a70;padding:0 16mm;display:flex;'
        + `justify-content:space-between"><span>${footer}</span>`
        + '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>',
      margin: { top: '16mm', bottom: '16mm', left: '16mm', right: '16mm' },
    });
  } finally {
    await browser.close();
  }
  console.log('printed', output);
})().catch((error) => { console.error(String(error).slice(0, 400)); process.exit(1); });
