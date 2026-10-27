import type { FeatureCollection, Position } from 'geojson';
import type { createMap } from './map';

const rad = Math.PI / 180, deg = 180 / Math.PI;
/** Same low-order solar model as Julian Manning's package and HTML tracker.
 * This lightweight display overlay never changes illumination filters/predictions.
 */
export function subsolar(time: number): {lat: number; lon: number} {
  const n = time / 86_400_000 + 2440587.5 - 2451545;
  const longitude = ((280.460 + 0.9856474 * n) % 360) * rad;
  const anomaly = ((357.528 + 0.9856003 * n) % 360) * rad;
  const lambda = longitude + 1.915 * rad * Math.sin(anomaly) + 0.020 * rad * Math.sin(2 * anomaly);
  const epsilon = (23.439 - 0.0000004 * n) * rad;
  const ra = Math.atan2(Math.cos(epsilon) * Math.sin(lambda), Math.cos(lambda)) * deg;
  const lat = Math.asin(Math.sin(epsilon) * Math.sin(lambda)) * deg;
  const gmst = (280.46061837 + 360.98564736629 * n) % 360;
  return {lat, lon: ((ra - gmst + 540) % 360) - 180};
}
export function nightGeometry(time: number): FeatureCollection {
  const sun = subsolar(time);
  // At exact equinox use the limiting hemisphere, avoiding division by zero.
  const declination = (sun.lat >= 0 ? Math.max(sun.lat, 1e-10) : Math.min(sun.lat, -1e-10)) * rad;
  const pole = declination > 0 ? -90 : 90;
  const ring: Position[] = [[-180, pole]];
  for (let lon = -180; lon <= 180; lon++) ring.push([lon, Math.atan(-Math.cos((lon - sun.lon) * rad) / Math.tan(declination)) * deg]);
  ring.push([180, pole], [-180, pole]);
  if (pole < 0) ring.reverse();
  return {type: 'FeatureCollection', features: [{type: 'Feature', properties: {when_utc: new Date(time).toISOString()}, geometry: {type: 'Polygon', coordinates: [ring]}}]};
}
export function dayNight(map: ReturnType<typeof createMap>) {
  const toggle = document.getElementById('day-night') as HTMLInputElement;
  let timer: ReturnType<typeof setTimeout> | undefined;
  function update() {
    clearTimeout(timer);
    if (document.hidden) return;
    map.night(toggle.checked ? nightGeometry(Date.now()) : {type: 'FeatureCollection', features: []});
    if (toggle.checked) {
      toggle.title = `Geometric night at ${new Date().toISOString()}. Updated each minute; independent of the prediction date. Atmospheric twilight is not modelled.`;
      timer = setTimeout(update, 60_000 - Date.now() % 60_000 + 5);
    }
  }
  toggle.onchange = update;
  document.addEventListener('visibilitychange', update);
  update();
}
