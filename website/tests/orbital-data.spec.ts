import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const references = JSON.parse(readFileSync('public/test-data/references.json', 'utf8'));
const text = references[0].request.tle_text;
const time = Date.parse('2026-10-26T12:00:00Z');
const prefix = 'orbit-window.tle.v1.';
const cors = {'Access-Control-Allow-Origin': '*'};
function pair(norad: number) {
  const lines = text.split(/\r?\n/).map((line: string) => line.trim());
  const index = lines.findIndex((line: string) => line.startsWith(`1 ${norad}`));
  return {line1: lines[index], line2: lines[index + 1]};
}

test.beforeEach(async ({page}) => {
  await page.clock.setFixedTime(new Date(time));
  await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
});

async function setup(page: Page) {
  await page.goto('./');
  await page.evaluate(async () => {
    const module = await import('/src/orbital-data.ts');
    const catalogue = await (await fetch('./catalogue.json')).json();
    (window as any).__download = (names: string[]) => module.fetchOrbitalInputs(names, catalogue, new AbortController().signal, () => {});
  });
}
async function download(page: Page, names = ['SENTINEL-2B']) {
  return page.evaluate(names => (window as any).__download(names), names);
}

test('HTTP failure switches to the community mirror and stops repeated primary queries', async ({page}) => {
  let primary = 0, mirror = 0;
  await page.route('https://celestrak.org/**', route => {primary++; return route.fulfill({status: 503, headers: cors, body: 'Unavailable'});});
  await page.route('https://tle.ivanstanojevic.me/**', route => {
    mirror++;
    const norad = Number(new URL(route.request().url()).pathname.split('/').at(-1));
    return route.fulfill({headers: cors, json: pair(norad)});
  });
  await setup(page);
  const inputs = await download(page, ['SENTINEL-2B', 'SENTINEL-2C']);
  expect(primary).toBe(1); expect(mirror).toBe(2);
  expect(inputs.orbital_sources['SENTINEL-2B']).toContain('community mirror');
  expect(inputs.orbital_sources['SENTINEL-2C']).toContain('community mirror');
  expect(inputs.orbital_warnings).toEqual([]);
});

for (const invalid of ['wrong NORAD', 'bad checksum']) test(`${invalid} from a provider is rejected before caching`, async ({page}) => {
  let record = pair(42063);
  if (invalid === 'wrong NORAD') record = pair(39084);
  else record.line1 = record.line1.slice(0, 68) + ((Number(record.line1[68]) + 1) % 10);
  await page.route('https://celestrak.org/**', route => route.fulfill({headers: cors, body: `${record.line1}\n${record.line2}`}));
  await page.route('https://tle.ivanstanojevic.me/**', route => route.fulfill({headers: cors, json: pair(42063)}));
  await setup(page);
  const inputs = await download(page);
  expect(inputs.tle_text).toContain(pair(42063).line1);
  expect(inputs.orbital_sources['SENTINEL-2B']).toContain('community mirror');
  const cached = await page.evaluate(key => JSON.parse(localStorage.getItem(key)!), prefix + '42063');
  expect(cached.line1).toBe(pair(42063).line1);
});

test('expired cache is refreshed after two hours and retains retrieval provenance', async ({page}) => {
  let calls = 0;
  await page.route('https://celestrak.org/**', route => {calls++; return route.fulfill({headers: cors, body: text});});
  await setup(page);
  const initial = await download(page);
  await page.clock.setFixedTime(new Date(time + 2 * 60 * 60 * 1000 - 1));
  const cached = await download(page);
  expect(calls).toBe(1);
  expect(cached.orbital_sources['SENTINEL-2B']).toContain('browser cache');
  expect(cached.orbital_sources['SENTINEL-2B']).toContain('2026-10-26T12:00:00.000Z');
  await page.clock.setFixedTime(new Date(time + 2 * 60 * 60 * 1000));
  const refreshed = await download(page);
  expect(calls).toBe(2);
  expect(refreshed.orbital_sources['SENTINEL-2B']).toContain('2026-10-26T14:00:00.000Z');
  expect(refreshed.tle_text).toBe(initial.tle_text);
});

test('network failure uses valid stale cache with an explicit warning and original retrieval time', async ({page}) => {
  await page.addInitScript(({key, entry}) => localStorage.setItem(key, JSON.stringify(entry)), {key: prefix + '42063', entry: {norad_id: 42063, ...pair(42063), source: 'CelesTrak', retrieved_at: '2026-10-25T00:00:00Z'}});
  await setup(page);
  const inputs = await download(page);
  expect(inputs.orbital_warnings[0].code).toBe('orbital_cache_fallback');
  expect(inputs.orbital_warnings[0].message).toContain('2026-10-25T00:00:00Z');
  expect(inputs.orbital_sources['SENTINEL-2B']).toContain('stale browser cache');
  expect(inputs.tle_text).toContain(pair(42063).line1);
});

test('corrupt cached pairs are ignored, and fresh downloads replace them', async ({page}) => {
  let calls = 0;
  await page.addInitScript(({key, entry}) => localStorage.setItem(key, JSON.stringify(entry)), {key: prefix + '42063', entry: {norad_id: 42063, ...pair(39084), source: 'CelesTrak', retrieved_at: '2026-10-26T11:00:00Z'}});
  await page.route('https://celestrak.org/**', route => {calls++; return route.fulfill({headers: cors, body: text});});
  await setup(page);
  const inputs = await download(page);
  expect(calls).toBe(1);
  expect(inputs.tle_text).toContain(pair(42063).line1);
  expect(inputs.orbital_sources['SENTINEL-2B']).toContain('direct browser download');
});

test('unavailable browser storage falls back to memory caching', async ({page}) => {
  let calls = 0;
  await page.addInitScript(() => Object.defineProperty(window, 'localStorage', {get() {throw new Error('Storage unavailable');}}));
  await page.route('https://celestrak.org/**', route => {calls++; return route.fulfill({headers: cors, body: text});});
  await setup(page);
  await download(page);
  const reused = await download(page);
  expect(calls).toBe(1);
  expect(reused.orbital_sources['SENTINEL-2B']).toContain('browser cache');
});
