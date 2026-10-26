// Optional Playwright checks. Network dependencies are fulfilled from a local file.
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require(process.env.EO_PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.EO_CHROMIUM_PATH ? { executablePath: process.env.EO_CHROMIUM_PATH } : {}),
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.route('https://cdn.jsdelivr.net/**', route => route.fulfill({
    path: process.argv[3], contentType: 'text/javascript',
  }));
  await context.route('https://fonts.googleapis.com/**', route => route.abort());
  await context.route('https://fonts.gstatic.com/**', route => route.abort());
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.clock.install({ time: new Date('2026-10-26T00:00:00Z') });
  const open = name => page.goto(pathToFileURL(path.join(process.argv[2], name)).href);
  await open('planner.html');
  await page.waitForFunction(() => document.querySelectorAll('.pass').length > 0);
  const total = await page.locator('.pass').count();
  assert.ok(total > 10, 'all matching passes, beyond the former ten-pass limit');
  assert.equal(await page.locator('.timeline-pass').count(), total);
  assert.match(await page.locator('.statuses').first().textContent(), /Imaging not expected/);
  assert.ok(await page.locator('.chip.aged').count() > 0);
  assert.ok(await page.locator('.chip.stale').count() > 0);
  assert.ok(await page.locator('.chip.unknown').count() > 0);

  // Selection changes the propagated map time and highlights the exported footprint.
  await page.locator('.pass').first().click();
  assert.match(await page.locator('#mapTime').textContent(), /Planning/);
  assert.match(await page.locator('#passDetails').textContent(), /exported display footprint/);
  assert.equal(await page.locator('.pass[aria-pressed="true"]').count(), 1);
  assert.equal(await page.locator('.timeline-pass[aria-pressed="true"]').count(), 1);
  assert.equal(await page.evaluate(() => displayTime().toISOString()), await page.evaluate(() => selectedPass().tca.toISOString()));
  await page.locator('#liveMode').click();
  assert.equal(await page.locator('#mapTime').textContent(), 'Live map');
  assert.equal(await page.evaluate(() => selectedId), null);

  await page.locator('.timeline-pass').first().focus();
  await page.keyboard.press('Enter');
  assert.match(await page.locator('#mapTime').textContent(), /Planning/);
  await page.locator('#liveMode').click();

  await page.locator('#sortPasses').selectOption('coverage');
  const coverage = await page.locator('.pass').evaluateAll(nodes => nodes.map(el => Number(el.querySelector('.meta').textContent.split('%')[0])));
  assert.ok(coverage.every((value, index) => index === 0 || coverage[index - 1] >= value));
  await page.locator('#filterCoverage').fill('99');
  await page.locator('#filterCoverage').dispatchEvent('change');
  assert.ok(await page.locator('.pass').count() < total);
  assert.ok(await page.locator('.pass').evaluateAll(nodes => nodes.every(el => Number(el.querySelector('.meta').textContent.split('%')[0]) >= 99)));
  await page.locator('#filterCoverage').fill('0');
  await page.locator('#filterCoverage').dispatchEvent('change');

  for (const status of ['planned_nominal', 'planned_other', 'not_planned', 'unknown', 'not_checked']) {
    await page.locator('#filterPlan').selectOption(status);
    assert.ok(await page.locator('.pass').count() > 0, status);
    const codes = await page.evaluate(() => visiblePasses(new Date()).map(p => p.status.acquisition));
    assert.ok(codes.every(value => value === status));
  }
  await page.locator('#filterPlan').selectOption('');
  await page.locator('#filterImaging').selectOption('not_expected');
  assert.equal(await page.locator('.pass').count(), 1);
  await page.locator('#filterImaging').selectOption('');
  await page.locator('#filterSatellite').selectOption('SENTINEL-2B');
  assert.ok(await page.locator('.pass').count() > 0);
  assert.ok((await page.locator('.who').allTextContents()).every(text => text.includes('SENTINEL-2B')));
  await page.locator('#filterSatellite').selectOption('');

  // Satellite toggles affect both list and timeline, with a truthful empty state.
  for (const button of await page.locator('.sat').all()) await button.click();
  assert.equal(await page.locator('.pass').count(), 0);
  assert.match(await page.locator('#passes').textContent(), /view filters/);
  for (const button of await page.locator('.sat').all()) await button.click();
  assert.equal(await page.locator('.pass').count(), total);
  await page.locator('.pass').first().click();
  await page.screenshot({ path: path.join(process.argv[2], 'desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390);
  await page.screenshot({ path: path.join(process.argv[2], 'mobile.png'), fullPage: true });

  await page.clock.setFixedTime(new Date('2030-01-01T00:00:00Z'));
  await open('planner.html');
  assert.match(await page.locator('#windowStatus').textContent(), /expired/);
  assert.equal(await page.locator('#showPast').isChecked(), true);
  assert.equal(await page.locator('.pass').count(), total);
  await page.locator('#showPast').uncheck();
  assert.equal(await page.locator('.pass').count(), 0);
  await open('empty.html');
  assert.match(await page.locator('#passes').textContent(), /No passes met/);
  await open('positions.html');
  assert.match(await page.locator('#passes').textContent(), /no prediction list/);
  assert.equal(errors.length, 0, errors.join('\n'));
  await browser.close();
  console.log('Planner browser checks passed: selection, keyboard, live mode, sorting, filters, statuses, expiry, empty states and mobile layout.');
})().catch(error => { console.error(error); process.exitCode = 1; });
