import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const cases = JSON.parse(readFileSync('public/test-data/references.json', 'utf8'));
const fixture = cases.find((c: {name: string}) => c.name === 'optical_multi').expected;
const tle = cases[0].request.tle_text;

test.beforeEach(async ({page}) => {
  // Real, locally generated raster tiles; no network services or API keys.
  const tile = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 256;
    const context = canvas.getContext('2d')!; context.fillStyle = '#d6e6f7'; context.fillRect(0, 0, 256, 256);
    context.strokeStyle = '#a4c4eb'; context.strokeRect(0, 0, 255, 255);
    return canvas.toDataURL().split(',')[1];
  });
  await page.route('**/*', route => {
    const host = new URL(route.request().url()).hostname;
    if (host === 'celestrak.org') return route.fulfill({contentType: 'text/plain', headers: {'Access-Control-Allow-Origin': '*'}, body: tle});
    if (host !== '127.0.0.1') return route.fulfill({contentType: 'image/png', headers: {'Access-Control-Allow-Origin': '*'}, body: Buffer.from(tile, 'base64')});
    return route.continue();
  });
});

async function mockPython(page: Page) {
  await page.addInitScript(({fixture}) => {
    const state = window as any, NativeWorker = window.Worker;
    state.__jobs = []; state.__pythonWorkers = 0; state.__liveDelay = 30; state.__predictionDelay = 100;
    state.Worker = class {
      onmessage: ((event: unknown) => void) | null = null;
      constructor(url: string | URL, options: WorkerOptions) {
        // The actual MapLibre worker must run; only the Python worker is mocked.
        if (!String(url).includes('/src/worker.ts')) return new NativeWorker(url, options);
        state.__pythonWorkers++;
      }
      terminate() {} // Deliberately send late updates after termination.
      postMessage(job: any) {
        state.__jobs.push(job);
        const result = job.type === 'predict' ? fixture : {when_utc: job.request.when_utc, positions: job.request.satellites.map((satellite: string, index: number) => ({
          satellite, lat: -37.82 + index, lon: 144.96 + index, alt_km: 786, ground_speed_kms: 6.7, direction: 'Ascending',
          epoch_utc: '2026-10-01T00:00:00Z', tle_age_days: 25, freshness: 'stale', source: 'Frozen historical test elements',
        }))};
        const failure = state.__liveFailure && job.type === 'positions';
        setTimeout(() => this.onmessage?.({data: failure ? {type: 'error', id: job.id, message: 'Test dependency failure', fatal: true}
          : {type: job.type === 'predict' ? 'result' : 'positions', id: job.id, result}}), job.type === 'predict' ? state.__predictionDelay : state.__liveDelay);
      }
    };
  }, {fixture});
}
async function region(page: Page) {
  await page.getByRole('button', {name: 'Coordinates', exact: true}).click();
  await page.getByLabel('Longitude', {exact: true}).fill('144.96');
  await page.getByLabel('Latitude', {exact: true}).fill('-37.82');
  await page.getByRole('button', {name: 'Use coordinates'}).click();
}

test('lazy globe keeps region, results, selection, filters and drawing available', async ({page}) => {
  const errors: string[] = [], globeAssets: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (/globe\.ts|maplibre/.test(request.url())) globeAssets.push(request.url()); });
  await mockPython(page); await page.goto('./');
  expect(globeAssets).toHaveLength(0);
  await page.getByRole('button', {name: '3D globe', exact: true}).click();
  await expect(page.locator('#globe-status')).toContainText('Drag to rotate');
  await expect(page.locator('#globe canvas')).toBeVisible();
  expect(globeAssets.length).toBeGreaterThan(0);
  await region(page);
  await page.getByRole('button', {name: 'Calculate overpasses'}).click();
  await expect(page.locator('#status-title')).toHaveText('Calculation complete');
  await page.locator('.pass-select').first().click();
  await expect(page.locator('#results-body tr.selected')).toHaveCount(1);
  await page.locator('#filter-satellite').selectOption('SENTINEL-2B');
  await page.getByRole('button', {name: 'Whole Earth'}).click();
  await page.getByLabel('Live satellites', {exact: true}).check();
  await expect(page.locator('#live-list button')).toHaveCount(7);
  await expect(page.locator('#globe .live-marker')).toHaveCount(7);
  await page.getByRole('button', {name: 'Locate SENTINEL-2B', exact: true}).click();
  await page.getByRole('button', {name: 'Whole Earth'}).click();
  await page.screenshot({path: 'test-results/globe-desktop.png', fullPage: true});
  await page.getByRole('button', {name: 'Draw', exact: true}).click();
  await page.getByRole('button', {name: 'Edit shape', exact: true}).click();
  await expect(page.locator('#map')).toBeVisible();
  await expect(page.locator('#globe')).toBeHidden();
  await page.getByRole('button', {name: 'Save shape'}).click();
  await page.getByRole('button', {name: '3D globe', exact: true}).click();
  await page.getByRole('button', {name: 'Clear region'}).click();
  await page.getByRole('button', {name: '⊙ Point'}).click();
  await expect(page.locator('#map')).toBeVisible();
  await page.keyboard.press('Escape');
  expect(errors).toEqual([]);
});

test('live tracking is opt-in, shares Python, pauses for calculations, and rejects late/cancelled updates', async ({page}) => {
  await mockPython(page); await page.goto('./');
  await expect(page.locator('input[name=satellite]')).toHaveCount(7);
  expect(await page.evaluate(() => (window as any).__jobs)).toEqual([]);
  await page.getByLabel('Live satellites', {exact: true}).check();
  await expect(page.locator('#live-list button')).toHaveCount(7);
  await expect(page.locator('#live-warning')).toContainText('aged or stale');
  await expect(page.locator('#map .live-marker')).toHaveCount(7);
  await region(page);
  await page.evaluate(() => { (window as any).__predictionDelay = 1200; });
  await page.getByRole('button', {name: 'Calculate overpasses'}).click();
  await expect(page.locator('#live-status')).toContainText('paused during prediction');
  await expect(page.locator('#map .live-marker')).toHaveCount(0);
  await expect(page.locator('#status-title')).toHaveText('Calculation complete');
  await expect(page.locator('#live-list button')).toHaveCount(7);
  expect(await page.evaluate(() => (window as any).__pythonWorkers)).toBe(1);
  await page.getByRole('button', {name: 'Calculate overpasses'}).click();
  await page.getByRole('button', {name: 'Cancel calculation'}).click();
  await expect(page.locator('#status-title')).toHaveText('Calculation cancelled');
  await expect(page.locator('#live-list button')).toHaveCount(7);
  expect(await page.evaluate(() => (window as any).__pythonWorkers)).toBe(2);
  await page.getByLabel('Live satellites', {exact: true}).uncheck();
  await page.evaluate(() => { (window as any).__liveDelay = 700; });
  await page.getByLabel('Live satellites', {exact: true}).check();
  await page.getByLabel('Live satellites', {exact: true}).uncheck();
  await page.waitForTimeout(1400);
  await expect(page.locator('#live-panel')).toBeHidden();
  await expect(page.locator('#map .live-marker')).toHaveCount(0);
  await expect(page.locator('#status-title')).toHaveText('Calculation cancelled');
});

test('live dependency errors recover and selection replaces tracked satellites', async ({page}) => {
  await mockPython(page); await page.goto('./');
  await page.evaluate(() => { (window as any).__liveFailure = true; });
  await page.getByLabel('Live satellites', {exact: true}).check();
  await expect(page.locator('#live-status')).toContainText('Test dependency failure');
  await page.evaluate(() => { (window as any).__liveFailure = false; });
  await page.getByRole('button', {name: 'Retry live positions'}).click();
  await expect(page.locator('#live-list button')).toHaveCount(7);
  await page.getByRole('button', {name: 'Clear selection', exact: true}).click();
  await expect(page.locator('#live-status')).toContainText('Select at least one satellite');
  await page.getByLabel('S2B', {exact: true}).check();
  await expect(page.locator('#live-list button')).toHaveCount(1);
  await page.locator('.orbital-input summary').click();
  await page.locator('#tle-text').fill(tle);
  await expect.poll(() => page.evaluate(() => (window as any).__jobs.at(-1).request.orbital_source)).toBe('User-supplied TLE');
  await expect(page.getByRole('button', {name: 'Locate SENTINEL-2B', exact: true})).toBeVisible();
});

test('hidden tabs pause updates and rapid edits coalesce without stale markers', async ({page}) => {
  await page.addInitScript(() => Object.defineProperty(document, 'hidden', {get: () => Boolean((window as any).__testHidden)}));
  await mockPython(page); await page.goto('./');
  await page.getByLabel('Live satellites', {exact: true}).check();
  await expect(page.locator('#live-list button')).toHaveCount(7);
  await page.evaluate(() => { (window as any).__testHidden = true; document.dispatchEvent(new Event('visibilitychange')); });
  await expect(page.locator('#live-status')).toContainText('paused while this tab is hidden');
  await expect(page.locator('#live-list button')).toHaveCount(0);
  await page.evaluate(() => {
    (window as any).__testHidden = false; (window as any).__liveDelay = 500;
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.getByRole('button', {name: 'Clear selection', exact: true}).click();
  await page.getByLabel('S2B', {exact: true}).check();
  await expect(page.locator('#live-list button')).toHaveCount(1);
  await expect(page.locator('#map .live-marker')).toHaveCount(1);
  await expect(page.locator('#live-list')).toContainText('SENTINEL-2B');
});

test('WebGL failure leaves drawing usable and globe can be retried', async ({page}) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    (HTMLCanvasElement.prototype as any).getContext = function(type: string, ...args: any[]) {
      if ((window as any).__blockWebGL && type.startsWith('webgl')) return null;
      return original.apply(this, [type, ...args] as any);
    };
    (window as any).__blockWebGL = true;
  });
  await page.goto('./');
  await page.getByRole('button', {name: '3D globe', exact: true}).click();
  await expect(page.locator('#globe-status')).toContainText('3D globe unavailable');
  await expect(page.locator('#map')).toBeVisible();
  await region(page);
  await page.evaluate(() => { (window as any).__blockWebGL = false; });
  await page.getByRole('button', {name: '3D globe', exact: true}).click();
  await expect(page.locator('#globe canvas')).toBeVisible();
  await expect(page.locator('#globe-status')).toContainText('Drag to rotate');
  await page.locator('#globe canvas').evaluate(canvas => canvas.dispatchEvent(new Event('webglcontextlost')));
  await expect(page.locator('#globe-status')).toContainText('graphics context was lost');
  await expect(page.locator('#map')).toBeVisible();
});

test('mobile globe controls, keyboard navigation and live list fit the viewport', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await mockPython(page); await page.goto('./');
  await page.getByRole('button', {name: 'Continue on this device'}).click();
  await page.locator('#view-3d').focus(); await page.keyboard.press('Enter');
  await expect(page.locator('#globe-status')).toContainText('Drag to rotate');
  await page.locator('#globe canvas').focus(); await page.keyboard.press('ArrowLeft'); await page.keyboard.press('+');
  await page.getByLabel('Live satellites', {exact: true}).check();
  await expect(page.locator('#live-list button')).toHaveCount(7);
  await page.getByRole('button', {name: 'Locate SENTINEL-2B', exact: true}).focus(); await page.keyboard.press('Enter');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: 'test-results/globe-mobile.png', fullPage: true});
});
