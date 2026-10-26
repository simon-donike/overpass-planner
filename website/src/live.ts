import { fetchOrbitalInputs } from './orbital-data';
import type { createMap } from './map';
import type { BrowserRuntime } from './runtime';
import type { PositionsRequest, SatelliteInfo } from './types';

/** Opt-in tracking, serialized with predictions through the same Python worker. */
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
  let orbitalWarning = '', fetchedAt = 0;
  const active = () => toggle.checked && !paused && !document.hidden;
  function invalidate() {
    generation++; clearTimeout(timer); retrieval?.abort(); retrieval = undefined;
    map.positions([], {}); list.replaceChildren(); retry.hidden = true;
  }
  async function tick(id: number) {
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
      const positions = await runtime.positions({...orbital, when_utc: new Date().toISOString()}, event => {
        if (id === generation) status.textContent = event.label;
      });
      if (!active() || id !== generation) return;
      // Do not present an old time as live after a slow first runtime load.
      const age = Date.now() - Date.parse(positions.when_utc);
      if (age > 10_000) { timer = setTimeout(() => void tick(id), 0); return; }
      map.positions(positions.positions, catalogue);
      status.textContent = `Updated ${positions.when_utc.replace('T', ' ')} · every 5 seconds`;
      const old = positions.positions.some(p => p.freshness !== 'fresh');
      warning.textContent = [orbitalWarning, old ? 'Some elements are aged or stale. Refresh or supply newer TLEs; these labels are reminders, not accuracy probabilities.' : ''].filter(Boolean).join(' ');
      warning.hidden = !warning.textContent;
      // Keep focused controls intact while their text and coordinates update.
      for (const position of positions.positions) {
        let button = list.querySelector<HTMLButtonElement>(`[data-satellite="${position.satellite}"]`);
        if (!button) { button = document.createElement('button'); button.type = 'button'; button.dataset.satellite = position.satellite; list.append(button); }
        button.textContent = `${position.satellite} · ${position.alt_km.toFixed(0)} km · ${position.direction} · ${position.freshness}`;
        button.setAttribute('aria-label', `Locate ${position.satellite}`);
        button.title = `${position.lat.toFixed(3)}°, ${position.lon.toFixed(3)}° · ground speed ${position.ground_speed_kms.toFixed(2)} km/s\nTLE epoch ${position.epoch_utc} · ${Math.abs(position.tle_age_days).toFixed(1)} days away\n${position.source}`;
        button.onclick = () => { map.locate(position); document.getElementById('map-hint')!.textContent = button!.title.replaceAll('\n', ' · '); };
      }
      timer = setTimeout(() => void tick(id), 5000);
    } catch (error) {
      if (id !== generation || !active()) return;
      map.positions([], {}); list.replaceChildren();
      status.textContent = `Live positions unavailable: ${(error as Error).message}`;
      retry.hidden = false;
    } finally {
      inFlight = false;
      // Coalesce rapid input edits and visibility changes into one new request.
      if (id !== generation && active()) void tick(generation);
    }
  }
  function resume() {
    invalidate(); panel.hidden = !toggle.checked;
    if (!toggle.checked) return;
    if (paused || document.hidden) { status.textContent = paused ? 'Live positions paused during prediction.' : 'Live positions paused while this tab is hidden.'; return; }
    void tick(generation);
  }
  toggle.onchange = resume;
  retry.onclick = () => { orbital = undefined; resume(); };
  document.addEventListener('visibilitychange', resume);
  return {
    pause(on: boolean) { if (paused === on) return; paused = on; resume(); },
    refresh() { orbital = undefined; resume(); },
  };
}
