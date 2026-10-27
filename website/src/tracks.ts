import type { FeatureCollection, Position } from 'geojson';
import type { SatelliteInfo, SatellitePosition, SatelliteTrack, TracksResult } from './types';

export const TRACK_WINDOW_MS = 10 * 60_000;
const radians = Math.PI / 180;
const degrees = 180 / Math.PI;

export function validTracks(batch: TracksResult, time: number): boolean {
  return time >= Date.parse(batch.anchor_utc) && time < Date.parse(batch.valid_until_utc);
}
function point(track: SatelliteTrack, batch: TracksResult, time: number): [number, number, number, number] {
  const offset = (time - Date.parse(batch.start_utc)) / (batch.step_s * 1000);
  if (offset < 0 || offset > track.samples.length - 1) throw new Error('Track samples do not cover the requested time.');
  const index = Math.min(Math.floor(offset), track.samples.length - 2), fraction = offset - index;
  // Interpolate geodetic unit normals, so ±180° and near-polar motion remain
  // continuous. Cubic tangents preserve the curve through the sampled positions.
  const vectors = [-1, 0, 1, 2].map(delta => {
    const [lon, lat] = track.samples[Math.max(0, Math.min(track.samples.length - 1, index + delta))];
    return [Math.cos(lat * radians) * Math.cos(lon * radians), Math.cos(lat * radians) * Math.sin(lon * radians), Math.sin(lat * radians)];
  });
  const [x, y, z] = [0, 1, 2].map(axis => {
    const [a, b, c, d] = vectors.map(v => v[axis]), t = fraction;
    return 0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t * t + (-a + 3 * b - 3 * c + d) * t * t * t);
  });
  const first = track.samples[index], next = track.samples[index + 1];
  return [Math.atan2(y, x) * degrees, Math.atan2(z, Math.hypot(x, y)) * degrees,
    first[2] + (next[2] - first[2]) * fraction, first[3] + (next[3] - first[3]) * fraction];
}
export function trackPositions(batch: TracksResult, time: number): SatellitePosition[] {
  if (!validTracks(batch, time)) return [];
  return batch.tracks.map(track => {
    const [lon, lat, alt_km, ground_speed_kms] = point(track, batch, time);
    const age = (time - Date.parse(track.epoch_utc)) / 86_400_000;
    return {satellite: track.satellite, epoch_utc: track.epoch_utc, source: track.source, lon, lat, alt_km, ground_speed_kms,
      direction: point(track, batch, time + 1000)[1] > lat ? 'Ascending' : 'Descending',
      tle_age_days: age, freshness: Math.abs(age) <= 7 ? 'fresh' : Math.abs(age) <= 14 ? 'aged' : 'stale'};
  });
}
/** Clip each crossing at the map edge; never draw a chord across the whole map. */
export function splitDateLine(points: Position[]): Position[][] {
  if (!points.length) return [];
  const segments: Position[][] = [], current: Position[] = [points[0]];
  let segment = current;
  for (const next of points.slice(1)) {
    const previous = segment.at(-1)!;
    if (Math.abs(next[0] - previous[0]) > 180) {
      const edge = previous[0] > 0 ? 180 : -180;
      const unwrapped = next[0] + (edge > 0 ? 360 : -360);
      if (unwrapped === previous[0]) { segment.push([previous[0], next[1]]); continue; }
      const t = (edge - previous[0]) / (unwrapped - previous[0]);
      const latitude = previous[1] + (next[1] - previous[1]) * t;
      segment.push([edge, latitude]); segments.push(segment);
      segment = [[-edge, latitude]];
    }
    segment.push(next);
  }
  segments.push(segment);
  return segments.filter(segment => segment.length > 1);
}
export function trackLines(batch: TracksResult, time: number, catalogue: Record<string, SatelliteInfo>): FeatureCollection {
  const features: FeatureCollection['features'] = [];
  if (!validTracks(batch, time)) return {type: 'FeatureCollection', features};
  const start = Date.parse(batch.start_utc), step = batch.step_s * 1000;
  for (const track of batch.tracks) for (const part of ['past', 'future'] as const) {
    const from = part === 'past' ? time - TRACK_WINDOW_MS : time;
    const to = part === 'past' ? time : time + TRACK_WINDOW_MS;
    const points: Position[] = [point(track, batch, from).slice(0, 2)];
    for (let index = 0; index < track.samples.length; index++) {
      const sampleTime = start + index * step;
      if (sampleTime > from && sampleTime < to) points.push(track.samples[index].slice(0, 2));
    }
    points.push(point(track, batch, to).slice(0, 2));
    features.push({type: 'Feature', geometry: {type: 'MultiLineString', coordinates: splitDateLine(points)}, properties: {
      satellite: track.satellite, part, colour: catalogue[track.satellite]?.colour || '#2563b9',
    }});
  }
  return {type: 'FeatureCollection', features};
}
