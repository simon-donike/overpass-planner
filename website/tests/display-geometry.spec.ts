import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

const references = JSON.parse(readFileSync('public/test-data/positions.json', 'utf8'));
const solar = JSON.parse(readFileSync('public/test-data/solar.json', 'utf8'));

test.beforeEach(async ({page}) => {
  await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
  await page.goto('./');
});

test('interpolated tracks agree with direct SGP4 between samples throughout each window', async ({page}) => {
  const actual = await page.evaluate(async cases => {
    const {trackPositions, trackLines} = await import('/src/tracks.ts');
    return cases.map((entry: any) => entry.interpolated.map((instant: any) => ({
      rows: trackPositions(entry.batch, Date.parse(instant.when_utc)),
      lines: trackLines(entry.batch, Date.parse(instant.when_utc), {}),
    })));
  }, references);
  for (const [i, reference] of references.entries()) for (const [j, instant] of reference.interpolated.entries()) {
    const {rows, lines} = actual[i][j];
    expect(lines.features).toHaveLength(14);
    for (const [k, row] of rows.entries()) {
      const expected = instant.positions[k], rad = Math.PI / 180;
      const dlat = (row.lat - expected.lat) * rad, dlon = (row.lon - expected.lon) * rad;
      const haversine = Math.sin(dlat / 2) ** 2 + Math.cos(row.lat * rad) * Math.cos(expected.lat * rad) * Math.sin(dlon / 2) ** 2;
      const distance = 2 * 6_400_000 * Math.asin(Math.min(1, Math.sqrt(haversine)));
      expect(distance).toBeLessThan(25);
      expect(Math.abs(row.alt_km - expected.alt_km)).toBeLessThan(0.01);
      expect(Math.abs(row.ground_speed_kms - expected.ground_speed_kms)).toBeLessThan(0.0001);
      expect(row.direction).toBe(expected.direction);
    }
    for (const feature of lines.features) for (const segment of feature.geometry.coordinates) for (let k = 1; k < segment.length; k++) {
      expect(Math.abs(segment[k][0] - segment[k - 1][0])).toBeLessThanOrEqual(180);
    }
  }
});

test('date-line crossing stays continuous and expired batches cannot extrapolate', async ({page}) => {
  const actual = await page.evaluate(async () => {
    const {splitDateLine, trackPositions, trackLines} = await import('/src/tracks.ts');
    const anchor = Date.parse('2026-10-29T00:00:00Z');
    const batch = {anchor_utc: new Date(anchor).toISOString(), start_utc: new Date(anchor - 600_000).toISOString(), valid_until_utc: new Date(anchor + 600_000).toISOString(), step_s: 10,
      tracks: [{satellite: 'SENTINEL-2B', epoch_utc: new Date(anchor).toISOString(), source: 'Synthetic display test', samples: Array.from({length: 181}, (_, i) => [((170 + i * 0.1 + 180) % 360) - 180, 80, 800, 6.7])}]};
    return {
      east: splitDateLine([[179, 20], [-179, 22]]), west: splitDateLine([[-179, -20], [179, -22]]),
      before: trackPositions(batch, anchor - 1), after: trackPositions(batch, anchor + 600_000),
      linesAfter: trackLines(batch, anchor + 600_000, {}),
      atCrossing: trackPositions(batch, anchor + 400_000),
      lines: trackLines(batch, anchor + 300_000, {}),
    };
  });
  expect(actual.east).toEqual([[[179, 20], [180, 21]], [[-180, 21], [-179, 22]]]);
  expect(actual.west).toEqual([[[-179, -20], [-180, -21]], [[180, -21], [179, -22]]]);
  expect(actual.before).toEqual([]); expect(actual.after).toEqual([]); expect(actual.linesAfter.features).toEqual([]);
  expect(Math.abs(actual.atCrossing[0].lon)).toBeCloseTo(180);
  for (const feature of actual.lines.features) for (const segment of feature.geometry.coordinates) for (let i = 1; i < segment.length; i++) {
    expect(Math.abs(segment[i][0] - segment[i - 1][0])).toBeLessThanOrEqual(180);
  }
});

test('solar model matches the package and shades the correct hemisphere in every season', async ({page}) => {
  const actual = await page.evaluate(async cases => {
    const {subsolar, nightGeometry} = await import('/src/day-night.ts');
    return cases.map((entry: any) => ({sun: subsolar(Date.parse(entry.when_utc)), night: nightGeometry(Date.parse(entry.when_utc))}));
  }, solar);
  const inside = (ring: number[][], point: number[]) => {
    let hit = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [x, y] = ring[i], [a, b] = ring[j];
      if ((y > point[1]) !== (b > point[1]) && point[0] < (a - x) * (point[1] - y) / (b - y) + x) hit = !hit;
    }
    return hit;
  };
  for (const [i, frame] of actual.entries()) {
    expect(Math.abs(frame.sun.lat - solar[i].expected[0])).toBeLessThan(1e-7);
    expect(Math.abs(frame.sun.lon - solar[i].expected[1])).toBeLessThan(1e-7);
    const ring = frame.night.features[0].geometry.coordinates[0];
    expect(ring[0]).toEqual(ring.at(-1));
    expect(inside(ring, [frame.sun.lon, frame.sun.lat])).toBe(false);
    for (let lat = -89; lat <= 89; lat += 10) for (let lon = -179; lon <= 179; lon += 10) {
      const rad = Math.PI / 180;
      const dot = Math.sin(lat * rad) * Math.sin(frame.sun.lat * rad) + Math.cos(lat * rad) * Math.cos(frame.sun.lat * rad) * Math.cos((lon - frame.sun.lon) * rad);
      if (Math.abs(dot) > 0.03) expect(inside(ring, [lon, lat])).toBe(dot < 0);
    }
  }
});
