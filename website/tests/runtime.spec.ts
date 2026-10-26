import { test, expect } from '@playwright/test';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

const references = JSON.parse(readFileSync('public/test-data/references.json', 'utf8'));

test('unchanged package imports and predicts inside real Pyodide', async ({page}) => {
  await page.route('**/*', route => {
    if (new URL(route.request().url()).hostname !== '127.0.0.1') return route.abort();
    return route.continue();
  });
  const failures: string[] = [];
  page.on('pageerror', error => failures.push(error.message));
  await page.goto('./');
  const results = await page.evaluate(async (cases) => {
    const worker = (window as any).createPredictionWorker() as Worker;
    const results = [];
    for (let i = 0; i < cases.length; i++) {
      results.push(await new Promise<any>((resolve, reject) => {
      worker.onerror = event => reject(new Error(event.message));
      worker.onmessage = event => {
        if (event.data.type === 'error') reject(new Error(event.data.message));
        if (event.data.type === 'result') resolve(event.data.result);
      };
      worker.postMessage({type: 'predict', base: new URL('./', location.href).href, id: i + 1, request: cases[i].request});
      }));
    }
    worker.terminate();
    return results;
  }, references);
  expect(failures).toEqual([]);
  for (const [index, result] of results.entries()) {
    expect(result.document.schema_version).toBe('2.0');
    expect(result.csv).toContain('overpass_utc');
    const lookup = result.document.provenance.timezone_lookup;
    expect(lookup.method).toBe(references[index].request.timezone ? 'manual IANA override' : 'simplified boundaries');
    expect(lookup.dataset.vertices_after).toBeLessThan(lookup.dataset.vertices_before * 0.1);
  }
  mkdirSync('test-results', {recursive: true});
  writeFileSync('test-results/browser-results.json', JSON.stringify(results));
  console.log(execFileSync(process.env.WEBSITE_PYTHON || resolve('../.venv/bin/python'), ['tests/compare_results.py', 'test-results/browser-results.json'], {encoding: 'utf8'}));
});

test('the actual planner form runs Python and renders downloadable map results', async ({page}) => {
  await page.route('**/*', route => {
    const host = new URL(route.request().url()).hostname;
    if (host === 'celestrak.org') return route.fulfill({headers: {'Access-Control-Allow-Origin': '*'}, contentType: 'text/plain', body: references[0].request.tle_text});
    return host === '127.0.0.1' ? route.continue() : route.abort();
  });
  await page.goto('./');
  await page.getByRole('button', {name: 'Coordinates', exact: true}).click();
  await page.getByLabel('Longitude', {exact: true}).fill('144.96');
  await page.getByLabel('Latitude', {exact: true}).fill('-37.82');
  await page.getByRole('button', {name: 'Use coordinates'}).click();
  await page.getByRole('button', {name: 'Clear selection', exact: true}).click();
  await page.getByLabel('S2B', {exact: true}).check();
  await page.getByLabel('Start time').fill('2026-10-26T00:00');
  await page.getByLabel('Duration').fill('1');
  await page.getByRole('button', {name: 'Calculate overpasses'}).click();
  await expect(page.locator('#status-title')).toHaveText('Calculation complete', {timeout: 120_000});
  await expect(page.locator('#error')).toBeHidden();
  await expect(page.locator('#results-body tr')).toHaveCount(1);
  await expect(page.locator('#results-body')).toContainText('SENTINEL-2B');
  await expect(page.locator('#status-detail')).toContainText('Australia/Melbourne');
  await expect(page.locator('#map path.leaflet-interactive').first()).toBeVisible();
  const pending = page.waitForEvent('download'); await page.locator('[data-download=csv]').click();
  const path = await (await pending).path();
  expect(readFileSync(path!, 'utf8')).toContain(references[1].expected.document.passes[0].overpass_utc.replace(/\.\d+Z$/, 'Z'));
  const documentPending = page.waitForEvent('download'); await page.locator('[data-download=json]').click();
  const documentPath = await (await documentPending).path();
  const document = JSON.parse(readFileSync(documentPath!, 'utf8'));
  expect(document.provenance.orbital_elements[0].source).toContain('CelesTrak');
  expect(document.provenance.orbital_elements[0].source).toContain('direct browser download');
});
