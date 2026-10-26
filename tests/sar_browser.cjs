const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require(process.env.EO_PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.EO_CHROMIUM_PATH ? { executablePath: process.env.EO_CHROMIUM_PATH } : {}),
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.route('https://cdn.jsdelivr.net/**', route => route.fulfill({ path: process.argv[3], contentType: 'text/javascript' }));
  await context.route('https://fonts.googleapis.com/**', route => route.abort());
  await context.route('https://fonts.gstatic.com/**', route => route.abort());
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.clock.install({ time: new Date('2026-10-26T00:00:00Z') });
  await page.goto(pathToFileURL(path.join(process.argv[2], 'sar.html')).href);
  await page.waitForFunction(() => typeof sats !== 'undefined' && sats.some(s => s.name === 'SENTINEL-1C'));
  const actual = await page.evaluate(() => sarEdge(sats.find(s => s.name === 'SENTINEL-1C'), new Date('2026-10-26T00:00:00Z')));
  const references = JSON.parse(fs.readFileSync(path.join(process.argv[2], 'sar_expected.json'), 'utf8'));
  const expected = references[0].edges;
  assert.equal(actual.length, 2);
  for (let i = 0; i < 2; i++) for (let j = 0; j < 2; j++) assert.ok(Math.abs(actual[i][j] - expected[i][j]) < 0.01, `SAR geometry mismatch ${i}/${j}`);
  // Equal ECEF states isolate footprint math from the two propagators' frames.
  for (const reference of references){
    const exact = await page.evaluate(({state, config}) => sarEdge(
      {...sats.find(s => s.name === 'SENTINEL-1C'), sensor_config: config},
      new Date('2026-10-26T00:00:00Z'), state
    ), reference);
    for (let i = 0; i < 2; i++) for (let j = 0; j < 2; j++){
      assert.ok(Math.abs(exact[i][j] - reference.edges[i][j]) < 1e-7, `Frozen ${reference.config.mode} geometry mismatch ${i}/${j}`);
    }
  }
  assert.ok(await page.locator('.pass').count() > 0);
  assert.match(await page.locator('.pass').first().textContent(), /Radar imaging/);
  await page.locator('.pass').first().click();
  assert.match(await page.locator('#passDetails').textContent(), /incidence angle/);
  assert.match(await page.locator('#passDetails').textContent(), /relative orbit/);
  assert.equal(errors.length, 0, errors.join('\n'));
  await browser.close();
  console.log('Sentinel-1 browser geometry and presentation checks passed.');
})().catch(error => { console.error(error); process.exit(1); });
