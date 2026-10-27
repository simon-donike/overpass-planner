import { fetchOrbitalInputs } from './orbital-data';
import { trackLines, trackPositions, validTracks } from './tracks';
import type { createMap } from './map';
import type { BrowserRuntime } from './runtime';
import type { PositionsRequest, SatelliteInfo, TracksResult } from './types';

/** Precompute once per ten-minute window; animation never calls Python. */
export function liveSatellites(runtime: BrowserRuntime, map: ReturnType<typeof createMap>, inputs: () => {satellites: string[]; catalogue: Record<string, SatelliteInfo>; tle: string}) {
  const toggle = document.getElementById('live-satellites') as HTMLInputElement;
  const panel = document.getElementById('live-panel')!;
  const status = document.getElementById('live-status')!;
  const warning = document.getElementById('live-warning')!;
  const retry = document.getElementById('retry-live')!;
  const list = document.getElementById('live-list')!;
  let generation = 0, paused = false, inFlight = false, timer: ReturnType<typeof setTimeout> | undefined;
  let retrieval: AbortController | undefined;
  let orbital: Omit<PositionsRequest, 'when_utc'> | undefined;
  let batch: TracksResult | undefined;
  let orbitalWarning = '', fetchedAt = 0, detailsAt = 0;
  const active = () => toggle.checked && !paused && !document.hidden;
  function clear() {
    generation++; clearTimeout(timer); retrieval?.abort(); retrieval = undefined;
    map.positions([], {}); map.tracks({type: 'FeatureCollection', features: []});
    list.replaceChildren(); retry.hidden = true; warning.hidden = true; detailsAt = 0;
  }
  function failed(error: unknown) {
    map.positions([], {}); map.tracks({type: 'FeatureCollection', features: []}); list.replaceChildren();
    status.textContent = `Live positions unavailable: ${(error as Error).message}`;
    retry.hidden = false;
  }
  function draw(id: number) {
    if (!active() || id !== generation) return;
    if (!batch || !validTracks(batch, Date.now())) { resume(); return; }
    try {
      const time = Date.now(), catalogue = inputs().catalogue;
      const rows = trackPositions(batch, time), details = time - detailsAt >= 1000;
      // Four small marker updates per second; paths, labels and metadata once per
      // second. These are interpolation/display operations, never propagation.
      map.positions(rows, catalogue, details);
      if (details) {
        detailsAt = time;
        map.tracks(trackLines(batch, time, catalogue));
        status.textContent = `Live ${new Date(time).toISOString().replace('T', ' ')} · precomputed until ${batch.valid_until_utc.replace('T', ' ')}`;
        const old = rows.some(row => row.freshness !== 'fresh');
        warning.textContent = [orbitalWarning, old ? 'Some elements are aged or stale. Refresh or supply newer TLEs; these labels are reminders, not accuracy probabilities.' : ''].filter(Boolean).join(' ');
        warning.hidden = !warning.textContent;
        for (const position of rows) {
          let button = list.querySelector<HTMLButtonElement>(`[data-satellite="${position.satellite}"]`);
          if (!button) { button = document.createElement('button'); button.type = 'button'; button.dataset.satellite = position.satellite; list.append(button); }
          button.textContent = `${position.satellite} · ${position.alt_km.toFixed(0)} km · ${position.direction} · ${position.freshness}`;
          button.setAttribute('aria-label', `Locate ${position.satellite}`);
          button.title = `${position.lat.toFixed(3)}°, ${position.lon.toFixed(3)}° · ground speed ${position.ground_speed_kms.toFixed(2)} km/s\nTLE epoch ${position.epoch_utc} · ${Math.abs(position.tle_age_days).toFixed(1)} days away\n${position.source}`;
          button.onclick = () => {
            const current = batch && validTracks(batch, Date.now()) ? trackPositions(batch, Date.now()).find(row => row.satellite === position.satellite) : undefined;
            if (current) { map.locate(current); document.getElementById('map-hint')!.textContent = button!.title.replaceAll('\n', ' · '); }
          };
        }
      }
      timer = setTimeout(() => draw(id), 250);
    } catch (error) { failed(error); }
  }
  async function prepare(id: number) {
    if (!active() || id !== generation || inFlight) return;
    inFlight = true;
    try {
      const {satellites, catalogue, tle} = inputs();
      if (!satellites.length) throw new Error('Select at least one satellite to show its live position.');
      if (!orbital || Date.now() - fetchedAt >= 10 * 60_000) {
        status.textContent = tle ? 'Preparing supplied orbital elements…' : 'Downloading orbital elements in your browser…';
        retrieval = new AbortController();
        const data = tle ? {tle_text: tle, orbital_source: 'User-supplied TLE', orbital_warnings: []}
          : await fetchOrbitalInputs(satellites, catalogue, retrieval.signal, (name, complete, total) => {
            if (id === generation) status.textContent = `${name} · ${complete} of ${total} elements ready`;
          });
        if (id !== generation) return;
        retrieval = undefined; orbital = {satellites, ...data};
        orbitalWarning = data.orbital_warnings.map(w => w.message).join(' ');
        fetchedAt = Date.now();
      }
      status.textContent = 'Precomputing satellite tracks for the next ten minutes…';
      const calculated = await runtime.tracks({...orbital, when_utc: 'now'}, event => {
        if (id === generation) status.textContent = event.label;
      });
      if (!active() || id !== generation) return;
      if (!validTracks(calculated, Date.now())) throw new Error('The precomputed window expired or the device clock changed. Retry live positions.');
      batch = calculated; draw(id);
    } catch (error) {
      if (id === generation && active()) failed(error);
    } finally {
      inFlight = false;
      // Coalesce input edits/pause changes while a batch was being calculated.
      if (id !== generation && active()) resume();
    }
  }
  function resume() {
    clear(); panel.hidden = !toggle.checked;
    if (!toggle.checked) return;
    if (paused || document.hidden) { status.textContent = paused ? 'Live positions paused during prediction.' : 'Live positions paused while this tab is hidden.'; return; }
    if (batch && validTracks(batch, Date.now())) draw(generation);
    else void prepare(generation);
  }
  toggle.onchange = resume;
  retry.onclick = () => { orbital = undefined; batch = undefined; resume(); };
  document.addEventListener('visibilitychange', resume);
  return {
    pause(on: boolean) { if (paused === on) return; paused = on; resume(); },
    refresh() { orbital = undefined; batch = undefined; resume(); },
  };
}
