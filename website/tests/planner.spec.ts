import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const cases = JSON.parse(readFileSync('public/test-data/references.json', 'utf8'));
const fixture = cases.find((c: {name: string}) => c.name === 'optical_multi').expected;
const tle = cases[0].request.tle_text;

async function mockWorker(page: Page) {
  await page.addInitScript(({fixture}) => {
    const state = window as any;
    state.__requests = []; state.__workerDelay = 50;
    state.Worker = class {
      onmessage: ((event: unknown) => void) | null = null;
      terminate() {} // Intentionally allow late responses to test stale-message rejection.
      postMessage(request: any) {
        state.__requests.push(request);
        const delay = state.__workerDelay;
        setTimeout(() => this.onmessage?.({data: {type: 'progress', id: request.id, stage: 'prediction', label: 'SENTINEL-2B', completed: 1, total: 3, elapsed: 1}}), 10);
        setTimeout(() => this.onmessage?.({data: state.__mockFailure ? {type: 'error', id: request.id, message: 'Test dependency download failed'} : {type: 'result', id: request.id, result: fixture}}), delay);
      }
    };
  }, {fixture});
}

test.beforeEach(async ({page}) => {
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.hostname === 'celestrak.org') return route.fulfill({headers: {'Access-Control-Allow-Origin': '*'}, contentType: 'text/plain', body: tle});
    if (url.hostname === 'tle.ivanstanojevic.me') {
      const norad = url.pathname.split('/').at(-1)!;
      const lines = tle.split(/\r?\n/);
      const index = lines.findIndex(line => line.startsWith(`1 ${norad}`));
      return route.fulfill({headers: {'Access-Control-Allow-Origin': '*'}, json: {line1: lines[index], line2: lines[index + 1]}});
    }
    if (url.hostname !== '127.0.0.1') return route.fulfill({status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="#e2e9df"/><path d="M0 128H256M128 0V256" stroke="#d3dfd1"/></svg>'});
    return route.continue();
  });
});

async function coordinates(page: Page) {
  if (await page.locator('#mobile-notice').isVisible()) await page.getByRole('button', {name: 'Continue on this device'}).click();
  await page.getByRole('button', {name: 'Coordinates', exact: true}).click();
  await page.getByLabel('Longitude', {exact: true}).fill('144.96');
  await page.getByLabel('Latitude', {exact: true}).fill('-37.82');
  await page.getByRole('button', {name: 'Use coordinates'}).click();
}

test('coordinates, options, results, filters and complete/filtered exports', async ({page}) => {
  await mockWorker(page); await page.goto('./');
  await expect(page.locator('input[name=satellite]')).toHaveCount(7);
  await coordinates(page);
  await page.getByLabel('Start time').fill('2026-10-26T00:00');
  await page.getByLabel('Duration').fill('3');
  await page.getByLabel('SAR mode').selectOption('EW');
  await page.getByLabel('Imaging-eligible passes only').uncheck();
  await page.getByRole('button', {name: 'Calculate overpasses'}).click();
  await expect(page.locator('#status-title')).toHaveText('Calculation complete');
  await expect(page.locator('#results-body tr')).toHaveCount(fixture.document.count);
  const request = await page.evaluate(() => (window as any).__requests[0].request);
  expect(request.roi.coordinates).toEqual([144.96, -37.82]);
  expect(request.s1_mode).toBe('EW'); expect(request.imaging_only).toBe(false);
  await page.locator('.pass-select').first().click();
  await expect(page.locator('#results-body tr.selected')).toHaveCount(1);
  await expect(page.locator('#map path.leaflet-interactive').first()).toBeVisible();
  await page.locator('#filter-satellite').selectOption('SENTINEL-2B');
  const filteredCount = fixture.document.passes.filter((p: any) => p.satellite === 'SENTINEL-2B').length;
  await expect(page.locator('#results-body tr')).toHaveCount(filteredCount);
  await page.locator('#sort').selectOption('coverage');
  await page.locator('#view').selectOption('csv');
  await expect(page.locator('#csv-preview')).toHaveValue(/satellite,mission/);
  for (const format of ['csv', 'geojson', 'json']) {
    const downloadEvent = page.waitForEvent('download');
    await page.locator(`[data-download=${format}]`).click();
    const download = await downloadEvent;
    expect(download.suggestedFilename()).toBe(`overpasses.${format}`);
    const path = await download.path();
    const text = readFileSync(path!, 'utf8');
    if (format === 'json') expect(JSON.parse(text).count).toBe(fixture.document.count);
    if (format === 'geojson') expect(JSON.parse(text).features).toHaveLength(fixture.document.count);
    if (format === 'csv') expect(text).toBe(fixture.csv);
  }
  await page.locator('#filtered-format').selectOption('json');
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', {name: 'Download filtered rows'}).click();
  const path = await (await downloadEvent).path();
  expect(JSON.parse(readFileSync(path!, 'utf8')).count).toBe(filteredCount);
  await page.getByLabel('Minimum coverage').fill('80');
  await expect(page.locator('#outdated')).toBeVisible();
  await page.screenshot({path: 'test-results/planner-desktop.png', fullPage: true});
});

test.describe('Start time in the user timezone', () => {
  test.use({timezoneId: 'Europe/Madrid'});

  test('UTC/local switches preserve the instant and do not make completed results stale', async ({page}) => {
    await mockWorker(page); await page.goto('./'); await coordinates(page);
    await page.getByLabel('Start time', {exact: true}).fill('2026-07-05T12:00');
    await page.getByRole('button', {name: 'Local time', exact: true}).click();
    await expect(page.getByLabel('Start time', {exact: true})).toHaveValue('2026-07-05T14:00');
    await expect(page.locator('#start-time-hint')).toContainText('Europe/Madrid · UTC+02:00');
    await page.getByRole('button', {name: 'Calculate overpasses'}).click();
    await expect(page.locator('#status-title')).toHaveText('Calculation complete');
    expect(await page.evaluate(() => (window as any).__requests[0].request.start_utc)).toBe('2026-07-05T12:00:00.000Z');
    await page.getByRole('button', {name: 'UTC', exact: true}).click();
    await expect(page.getByLabel('Start time', {exact: true})).toHaveValue('2026-07-05T12:00');
    await expect(page.locator('#outdated')).toBeHidden();
    await page.getByLabel('Start time', {exact: true}).fill('2026-01-15T23:30');
    await page.getByRole('button', {name: 'Local time', exact: true}).click();
    await expect(page.getByLabel('Start time', {exact: true})).toHaveValue('2026-01-16T00:30');
    await expect(page.locator('#start-time-hint')).toContainText('UTC+01:00');
    await expect(page.locator('#outdated')).toBeVisible();
  });

  test('daylight-saving gaps are rejected and repeated hours preserve an explicit UTC instant', async ({page}) => {
    await mockWorker(page); await page.goto('./'); await coordinates(page);
    // The later occurrence of 02:30 in Madrid must survive both display switches.
    await page.getByLabel('Start time', {exact: true}).fill('2026-10-25T01:30');
    await page.getByRole('button', {name: 'Local time', exact: true}).click();
    await expect(page.getByLabel('Start time', {exact: true})).toHaveValue('2026-10-25T02:30');
    await expect(page.locator('#start-time-hint')).toContainText('occurs twice');
    await expect(page.locator('#start-time-hint')).toContainText('UTC+01:00');
    await page.getByRole('button', {name: 'Calculate overpasses'}).click();
    await expect(page.locator('#status-title')).toHaveText('Calculation complete');
    expect(await page.evaluate(() => (window as any).__requests[0].request.start_utc)).toBe('2026-10-25T01:30:00.000Z');
    await page.getByRole('button', {name: 'UTC', exact: true}).click();
    await expect(page.getByLabel('Start time', {exact: true})).toHaveValue('2026-10-25T01:30');
    await page.getByRole('button', {name: 'Local time', exact: true}).click();
    await page.getByLabel('Start time', {exact: true}).fill('2026-03-29T02:30');
    await page.getByRole('button', {name: 'Calculate overpasses'}).click();
    await expect(page.locator('#error')).toContainText('This local time does not exist');
    expect(await page.evaluate(() => (window as any).__requests.length)).toBe(1);
    // UTC is still selectable when a local wall time is invalid.
    await page.getByRole('button', {name: 'UTC', exact: true}).click();
    await page.getByRole('button', {name: 'Calculate overpasses'}).click();
    await expect(page.locator('#status-title')).toHaveText('Calculation complete');
    expect(await page.evaluate(() => (window as any).__requests[1].request.start_utc)).toBe('2026-03-29T02:30:00.000Z');
  });
});

test('mobile usage notice supports click-away, Escape, explicit dismissal and per-tab persistence', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('./');
  const notice = page.getByRole('dialog', {name: 'High network and CPU usage'});
  await expect(notice).toBeVisible();
  await page.getByRole('heading', {name: 'High network and CPU usage'}).click();
  await expect(notice).toBeVisible();
  await page.mouse.click(8, 8);
  await expect(notice).toBeHidden();
  await page.reload();
  await expect(notice).toBeHidden();
  await page.evaluate(() => sessionStorage.removeItem('overpass.mobile-notice.seen'));
  await page.reload(); await expect(notice).toBeVisible();
  await page.keyboard.press('Escape'); await expect(notice).toBeHidden();
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('overpass.mobile-notice.seen'))).toBe('1');
  await page.evaluate(() => sessionStorage.removeItem('overpass.mobile-notice.seen'));
  await page.reload(); await expect(notice).toBeVisible();
  await page.getByRole('button', {name: 'Continue on this device'}).click();
  await expect(notice).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('original orbit logo loads in the header and is configured as the browser icon', async ({page}) => {
  await page.goto('./');
  await expect(page.locator('.brand-logo')).toBeVisible();
  await expect.poll(() => page.locator('.brand-logo').evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  await expect(page.locator('.brand-logo')).toHaveAttribute('src', /(?:^|\/)logo\.svg$/);
  await expect(page.locator('link[rel=icon]')).toHaveAttribute('href', './logo.svg');
  await expect(page.locator('#mobile-notice')).toBeHidden();
});

test('point, rectangle and polygon drawing, editing, deletion and keyboard controls', async ({page}) => {
  await page.goto('./');
  const map = page.locator('#map'); await map.scrollIntoViewIfNeeded();
  const bounds = await map.boundingBox(); expect(bounds).not.toBeNull();
  const x = bounds!.x + bounds!.width * .6, y = bounds!.y + bounds!.height * .5;
  await page.getByRole('button', {name: '⊙ Point', exact: true}).click();
  await page.mouse.click(x, y);
  await expect(page.locator('#roi-summary')).toHaveText('Drawn region · WGS84');
  await page.getByRole('button', {name: 'Edit shape', exact: true}).click();
  await page.mouse.move(x, y); await page.mouse.down(); await page.mouse.move(x + 20, y + 10); await page.mouse.up();
  await page.getByRole('button', {name: 'Save shape', exact: true}).click();
  await page.getByRole('button', {name: 'Clear region', exact: true}).click();
  await expect(page.locator('#roi-summary')).toHaveText('No region selected');
  await page.getByRole('button', {name: '▱ Rectangle', exact: true}).click();
  await page.mouse.move(x - 40, y - 30); await page.mouse.down(); await page.mouse.move(x + 40, y + 30, {steps: 5}); await page.mouse.up();
  await expect(page.locator('#roi-summary')).toHaveText('Drawn region · WGS84');
  await page.getByRole('button', {name: 'Clear region', exact: true}).click();
  await page.getByRole('button', {name: '⬡ Polygon', exact: true}).click();
  for (const [px, py] of [[x - 40, y], [x + 40, y], [x, y + 40], [x - 40, y]]) {
    await page.mouse.move(px, py, {steps: 3});
    await page.waitForTimeout(250); // Leaflet.draw suppresses rapid synthetic duplicate clicks.
    await page.mouse.click(px, py);
  }
  await expect(page.locator('#roi-summary')).toHaveText('Drawn region · WGS84');
  await page.getByRole('button', {name: 'Clear region', exact: true}).focus(); await page.keyboard.press('Enter');
  await expect(page.locator('#roi-summary')).toHaveText('No region selected');
  await page.getByRole('button', {name: '⬡ Polygon', exact: true}).click(); await page.keyboard.press('Escape');
  await expect(page.locator('.leaflet-draw-tooltip')).toHaveCount(0);
});

test('GeoJSON uploads, bbox validation, TLE upload, and missing selections', async ({page}) => {
  await mockWorker(page); await page.goto('./');
  await page.getByRole('button', {name: 'Upload', exact: true}).click();
  for (const geometry of [
    {type: 'Point', coordinates: [144.96, -37.82]},
    {type: 'Feature', properties: {}, geometry: {type: 'Point', coordinates: [144.96, -37.82]}},
    {type: 'FeatureCollection', features: [{type: 'Feature', properties: {}, geometry: {type: 'Point', coordinates: [144.96, -37.82]}}]},
  ]) {
    await page.locator('#roi-file').setInputFiles({name: 'region.geojson', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(geometry))});
    await expect(page.locator('#roi-summary')).toContainText('Uploaded');
  }
  await page.locator('#roi-file').setInputFiles({name: 'broken.json', mimeType: 'application/json', buffer: Buffer.from('{invalid')});
  await expect(page.locator('#error')).toContainText('Could not load GeoJSON');
  await page.getByRole('button', {name: 'Coordinates', exact: true}).click();
  await page.locator('#coordinate-kind').selectOption('bbox');
  await page.getByLabel('West', {exact: true}).fill('145'); await page.getByLabel('East', {exact: true}).fill('144');
  await page.getByRole('button', {name: 'Use coordinates'}).click();
  await expect(page.locator('#error')).toContainText('west < east');
  await page.getByLabel('West', {exact: true}).fill('144'); await page.getByLabel('East', {exact: true}).fill('145');
  await page.getByRole('button', {name: 'Use coordinates'}).click();
  await expect(page.locator('#roi-summary')).toContainText('Bounding box');
  await page.locator('.orbital-input summary').click();
  await page.locator('#tle-file').setInputFiles({name: 'historical.tle', mimeType: 'text/plain', buffer: Buffer.from(tle)});
  await expect(page.locator('#orbital-badge')).toHaveText('User supplied');
  await page.getByRole('button', {name: 'Clear selection', exact: true}).click();
  await page.getByRole('button', {name: 'Calculate overpasses'}).click();
  await expect(page.locator('#error')).toContainText('Select at least one satellite');
});

test('cancel rejects late messages; errors and repeated runs recover', async ({page}) => {
  await mockWorker(page); await page.goto('./'); await coordinates(page);
  await page.evaluate(() => (window as any).__workerDelay = 1600);
  await page.getByRole('button', {name: 'Calculate overpasses'}).click();
  await expect(page.locator('#status-detail')).toContainText('1 of 3');
  await expect(page.locator('#elapsed')).toContainText('remaining');
  await page.getByRole('button', {name: 'Cancel calculation'}).click();
  await expect(page.locator('#status-title')).toHaveText('Calculation cancelled');
  await page.evaluate(() => (window as any).__mockFailure = true);
  await page.evaluate(() => (window as any).__workerDelay = 30);
  await page.getByRole('button', {name: 'Calculate overpasses'}).click();
  await expect(page.locator('#error')).toContainText('Test dependency download failed');
  await page.evaluate(() => (window as any).__mockFailure = false);
  await page.getByRole('button', {name: 'Calculate overpasses'}).click();
  await expect(page.locator('#status-title')).toHaveText('Calculation complete');
  // The cancelled worker responds after a new calculation has already completed.
  await page.waitForTimeout(1650);
  await expect(page.locator('#status-title')).toHaveText('Calculation complete');
  await expect(page.locator('#error')).toBeHidden();
});

test('mobile layout, unavailable providers, and settings changed during calculation', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.route('https://celestrak.org/**', route => route.abort());
  await page.route('https://tle.ivanstanojevic.me/**', route => route.abort());
  await mockWorker(page); await page.goto('./'); await coordinates(page);
  await page.getByRole('button', {name: 'Calculate overpasses'}).click();
  await expect(page.locator('#error')).toContainText('Unable to download orbital elements');
  await page.locator('#tle-text').fill(tle);
  await page.evaluate(() => (window as any).__workerDelay = 1200);
  await page.getByRole('button', {name: 'Calculate overpasses'}).click();
  await page.getByLabel('Duration').fill('7');
  await expect(page.locator('#status-title')).toHaveText('Calculation complete');
  await expect(page.locator('#outdated')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: 'test-results/planner-mobile.png', fullPage: true});
});

test('direct retrieval fetches selected IDs only, caches across reloads and sends no region', async ({page}) => {
  const requests: string[] = [];
  page.on('request', request => { if (request.url().startsWith('https://celestrak.org/')) requests.push(request.url()); });
  await mockWorker(page); await page.goto('./');
  expect(requests).toHaveLength(0); // No harvesting on page load.
  await coordinates(page);
  await page.getByRole('button', {name: 'Clear selection', exact: true}).click();
  await page.getByLabel('S2B', {exact: true}).check();
  await page.getByRole('button', {name: 'Calculate overpasses'}).click();
  await expect(page.locator('#status-title')).toHaveText('Calculation complete');
  expect(requests).toHaveLength(1);
  expect(new URL(requests[0]).search).toBe('?CATNR=42063&FORMAT=TLE');
  const first = await page.evaluate(() => (window as any).__requests[0].request);
  expect(first.orbital_sources['SENTINEL-2B']).toContain('CelesTrak');
  expect(first.orbital_sources['SENTINEL-2B']).toContain('direct browser download');
  await page.reload(); await coordinates(page);
  await page.getByRole('button', {name: 'Clear selection', exact: true}).click();
  await page.getByLabel('S2B', {exact: true}).check();
  await page.getByRole('button', {name: 'Calculate overpasses'}).click();
  await expect(page.locator('#status-title')).toHaveText('Calculation complete');
  expect(requests).toHaveLength(1);
  const second = await page.evaluate(() => (window as any).__requests[0].request);
  expect(second.orbital_sources['SENTINEL-2B']).toContain('browser cache');
});

test('cancel during a download aborts retrieval and prevents a late worker start', async ({page}) => {
  let requested = false;
  await page.route('https://celestrak.org/**', async route => {
    requested = true;
    await new Promise(resolve => setTimeout(resolve, 800));
    await route.fulfill({headers: {'Access-Control-Allow-Origin': '*'}, contentType: 'text/plain', body: tle}).catch(() => {});
  });
  await mockWorker(page); await page.goto('./'); await coordinates(page);
  await page.getByRole('button', {name: 'Calculate overpasses'}).click();
  await expect.poll(() => requested).toBe(true);
  await expect(page.locator('#status-title')).toHaveText('Downloading orbital elements');
  await page.getByRole('button', {name: 'Cancel calculation'}).click();
  await page.waitForTimeout(1000);
  await expect(page.locator('#status-title')).toHaveText('Calculation cancelled');
  expect(await page.evaluate(() => (window as any).__requests)).toHaveLength(0);
  await expect(page.locator('#error')).toBeHidden();
});
