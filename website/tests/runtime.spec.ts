import { test, expect } from '@playwright/test';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

const references = JSON.parse(readFileSync('public/test-data/references.json', 'utf8'));
const positions = JSON.parse(readFileSync('public/test-data/positions.json', 'utf8'));

test('unchanged package imports and predicts inside real Pyodide', async ({page}) => {
  await page.route('**/*', route => {
    if (new URL(route.request().url()).hostname !== '127.0.0.1') return route.abort();
    return route.continue();
  });
  const failures: string[] = [];
  page.on('pageerror', error => failures.push(error.message));
  await page.goto('./');
  const calculated = await page.evaluate(async ({cases, positions}) => {
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
    const tracked = [];
    const batches = [];
    for (let i = 0; i < positions.length; i++) {
      tracked.push(await new Promise<any>((resolve, reject) => {
        worker.onmessage = event => {
          if (event.data.type === 'error') reject(new Error(event.data.message));
          if (event.data.type === 'positions') resolve(event.data.result);
        };
        worker.postMessage({type: 'positions', base: new URL('./', location.href).href, id: 100 + i, request: positions[i].request});
      }));
      batches.push(await new Promise<any>((resolve, reject) => {
        worker.onmessage = event => {
          if (event.data.type === 'error') reject(new Error(event.data.message));
          if (event.data.type === 'tracks') resolve(event.data.result);
        };
        worker.postMessage({type: 'tracks', base: new URL('./', location.href).href, id: 200 + i, request: positions[i].request});
      }));
    }
    worker.terminate();
    return {results, tracked, batches};
  }, {cases: references, positions});
  const {results, tracked, batches} = calculated;
  for (let i = 0; i < positions.length; i++) {
    expect(batches[i].anchor_utc).toBe(positions[i].batch.anchor_utc);
    expect(batches[i].valid_until_utc).toBe(positions[i].batch.valid_until_utc);
    for (const [index, track] of batches[i].tracks.entries()) {
      const native = positions[i].batch.tracks[index];
      expect(track.samples).toHaveLength(native.samples.length);
      for (const [sampleIndex, sample] of track.samples.entries()) for (let axis = 0; axis < 4; axis++) {
        expect(Math.abs(sample[axis] - native.samples[sampleIndex][axis])).toBeLessThan(1e-7);
      }
    }
    for (const [j, row] of tracked[i].positions.entries()) {
      const expected = positions[i].expected[j];
      expect(row.satellite).toBe(expected.satellite);
      expect(row.direction).toBe(expected.direction);
      // Same SGP4 implementation as native Python; allow floating point roundoff.
      for (const key of ['lon', 'lat', 'alt_km', 'ground_speed_kms']) expect(Math.abs(row[key] - expected[key])).toBeLessThan(1e-7);
      expect(row.freshness).toBe(i === 1 ? 'stale' : 'fresh');
    }
  }
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
  // Reuse the actual prediction interpreter for live positions, then show them
  // in the real WebGL view. Tile failures are intentionally allowed offline.
  await page.getByRole('button', {name: 'Select all', exact: true}).click();
  await page.getByLabel('Live satellites', {exact: true}).check();
  await expect(page.locator('#live-list button')).toHaveCount(7, {timeout: 120_000});
  await expect(page.locator('#live-status')).toContainText('precomputed until');
  await page.getByRole('button', {name: '3D globe', exact: true}).click();
  await expect(page.locator('#globe-status')).toContainText('Drag to rotate');
  await expect(page.locator('#globe .live-marker')).toHaveCount(7);
  await page.getByRole('button', {name: 'Whole Earth'}).click();
  await page.getByLabel('Live satellites', {exact: true}).uncheck();
  await expect(page.locator('#globe .live-marker')).toHaveCount(0);
});
