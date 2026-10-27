import './style.css';
import { template } from './template';
import { createMap } from './map';
import { downloadResult, exportText } from './exports';
import { fetchOrbitalInputs } from './orbital-data';
import { startTimeControl } from './start-time';
import { showMobileNotice } from './mobile-notice';
import { BrowserRuntime } from './runtime';
import { liveSatellites } from './live';
import { dayNight } from './day-night';
import type { PredictionRequest, Progress, Result, SatelliteInfo } from './types';

const base = new URL(import.meta.env.BASE_URL, location.href).href;
const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const value = (id: string) => $<HTMLInputElement>(id).value;
const number = (id: string) => Number(value(id));
const escape = (text: unknown) => String(text ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]!));
$('app').innerHTML = template;
$<HTMLFormElement>('planner').noValidate = true;
let roi: unknown = null;
let catalogue: Record<string, SatelliteInfo> = {};
let orbitalDescription = '';
let retrieval: AbortController | undefined;
let result: Result | undefined;
let visible: number[] = [];
let selected: number | undefined;
let revision = 0, runRevision = 0;
let dirty = false, busy = false;
let requestId = 0, runStart = 0, stageStart = 0;
let lastProgress: Progress | undefined;
let inputMode = 'draw', sortDescending = false;
const startTime = startTimeControl($<HTMLInputElement>('start'), document.querySelectorAll<HTMLButtonElement>('[data-time-basis]'), $('start-time-hint'));
showMobileNotice();

function fail(message: string) { $('error').textContent = message; $('error').hidden = false; $('error').scrollIntoView({block: 'nearest'}); }
function clearError() { $('error').hidden = true; }
function changed() {
  revision++;
  if (!result) return;
  dirty = true; $('outdated').hidden = false;
  $('result-context').textContent = 'Showing the previous calculation. Recalculate to apply the changed region or prediction settings.';
}
const map = createMap((input, label) => {
  roi = input; $('roi-summary').textContent = label;
  $('map-hint').textContent = input ? 'Region selected · ready to calculate' : 'Draw or enter a region to begin'; changed();
});
dayNight(map);
function attempt(action: () => void) { try { clearError(); action(); } catch (error) { fail((error as Error).message); } }
for (const shape of ['point', 'rectangle', 'polygon']) $(`draw-${shape}`).onclick = () => {
  map.draw(shape); $('map-hint').textContent = 'Draw on the map · Escape cancels';
  if (matchMedia('(max-width: 760px)').matches) $('map').scrollIntoView({block: 'center'});
};
$('edit-roi').onclick = () => attempt(() => map.edit());
$('reset-roi').onclick = () => map.clear();
document.querySelectorAll<HTMLButtonElement>('[data-mode]').forEach(button => button.onclick = () => {
  inputMode = button.dataset.mode!;
  document.querySelectorAll<HTMLButtonElement>('[data-mode]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
  for (const mode of ['draw', 'coordinates', 'upload']) $(`${mode}-input`).hidden = mode !== inputMode;
});
$('coordinate-kind').onchange = () => { $('point-fields').hidden = value('coordinate-kind') !== 'point'; $('bbox-fields').hidden = value('coordinate-kind') === 'point'; };
function applyCoordinates() {
  const fields = value('coordinate-kind') === 'point' ? ['longitude', 'latitude'] : ['west', 'south', 'east', 'north'];
  if (fields.some(id => !value(id).trim() || !$<HTMLInputElement>(id).checkValidity())) throw new Error('Enter valid longitude (−180 to 180) and latitude (−90 to 90) coordinates.');
  if (fields.length === 2) map.show({type: 'Point', coordinates: fields.map(number)}, `Point · ${number('longitude').toFixed(4)}, ${number('latitude').toFixed(4)}`);
  else {
    const [west, south, east, north] = fields.map(number);
    if (west >= east || south >= north) throw new Error('Bounding box requires west < east and south < north.');
    map.show({type: 'Polygon', coordinates: [[[west, south], [east, south], [east, north], [west, north], [west, south]]]}, 'Bounding box · WGS84');
  }
}
$('apply-coordinates').onclick = () => attempt(applyCoordinates);
$('roi-file').onchange = async () => {
  const file = $<HTMLInputElement>('roi-file').files?.[0]; if (!file) return;
  try {
    clearError();
    if (file.size > 20 * 1024 * 1024) throw new Error('Choose a GeoJSON file smaller than 20 MiB to protect browser memory.');
    map.show(JSON.parse(await file.text()), `Uploaded · ${file.name}`);
    $<HTMLInputElement>('roi-name').value = file.name.replace(/\.(geojson|json)$/i, '');
  } catch (error) { fail(`Could not load GeoJSON: ${(error as Error).message}`); }
};
$('tle-file').onchange = async () => {
  const file = $<HTMLInputElement>('tle-file').files?.[0]; if (!file) return;
  if (file.size > 2 * 1024 * 1024) return fail('Choose a TLE file smaller than 2 MiB.');
  $<HTMLTextAreaElement>('tle-text').value = await file.text(); changed(); updateOrbitalBadge(); live.refresh();
};
function updateOrbitalBadge() { $('orbital-badge').textContent = value('tle-text').trim() ? 'User supplied' : orbitalDescription ? 'Browser download/cache' : 'Fetch in browser'; }
$('tle-text').addEventListener('input', () => { updateOrbitalBadge(); live.refresh(); });
$('planner').addEventListener('input', changed);
function setBusy(on: boolean) {
  busy = on; live.pause(on); $<HTMLButtonElement>('calculate').disabled = on; $('cancel').hidden = !on;
  $('status-symbol').classList.toggle('spinning', on);
}
const runtime = new BrowserRuntime(base, version => { $('version').textContent = `ENGINE ${version} · PYODIDE`; });
const selectedSatellites = () => [...document.querySelectorAll<HTMLInputElement>('input[name="satellite"]:checked')].map(input => input.value);
const live = liveSatellites(runtime, map, () => ({satellites: selectedSatellites(), catalogue, tle: value('tle-text').trim()}));
$('satellite-options').addEventListener('change', () => live.refresh());
function reportProgress(message: Progress) {
  if (lastProgress?.stage !== message.stage) stageStart = performance.now();
  lastProgress = message;
  $('status-title').textContent = ({runtime: 'Starting Python', dependencies: 'Loading scientific libraries', inputs: 'Preparing your inputs', prediction: 'Calculating overpasses', export: 'Preparing downloads'} as Record<string, string>)[message.stage] || message.stage;
  $('status-detail').textContent = message.stage === 'prediction' ? `${message.label} · ${message.completed} of ${message.total} satellites complete` : message.label;
  const progress = $<HTMLProgressElement>('progress'); progress.hidden = false;
  if (message.stage === 'prediction') { progress.max = message.total; progress.value = message.completed; } else progress.removeAttribute('value');
}
function showResult(calculated: Result) {
  result = calculated; selected = undefined;
  dirty = revision !== runRevision;
  if (!dirty) { roi = result.document.request.roi; map.show(roi, '', false, false); $('roi-summary').textContent = 'Normalized prediction region · WGS84'; }
  $('outdated').hidden = !dirty;
  setBusy(false); $('progress').hidden = true;
  $('status-title').textContent = 'Calculation complete';
  $('status-detail').textContent = `${result.document.count} overpasses · ${result.document.request.timezone} · calculated entirely on this device`;
  $('elapsed').textContent = duration((performance.now() - runStart) / 1000);
  $('assumption-list').innerHTML = result.document.assumptions.map(item => `<li>${escape(item)}</li>`).join('');
  document.querySelectorAll<HTMLButtonElement>('[data-download]').forEach(button => button.disabled = false);
  $<HTMLButtonElement>('download-filtered').disabled = false;
  if (result.document.warnings.length) fail(result.document.warnings.map(w => w.message).join(' '));
  updateFilters(); renderResults();
}
function duration(seconds: number) { return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s` : `${Math.floor(seconds)}s`; }
setInterval(() => {
  if (!busy) return;
  let text = `${duration((performance.now() - runStart) / 1000)} elapsed`;
  if (lastProgress?.stage === 'prediction' && lastProgress.completed > 0 && lastProgress.completed < lastProgress.total) {
    const observed = lastProgress.elapsed / lastProgress.completed;
    const remaining = Math.max(0, observed * lastProgress.total - (performance.now() - stageStart) / 1000);
    text += ` · ~${duration(remaining)} remaining`;
  }
  $('elapsed').textContent = text;
}, 500);
$('planner').onsubmit = async event => {
  event.preventDefault(); if (busy) return;
  clearError();
  let activeId: number | undefined;
  try {
    if (inputMode === 'coordinates') applyCoordinates();
    map.save();
    if (!roi) throw new Error('Draw a region, enter coordinates or upload a GeoJSON file first.');
    const satellites = [...document.querySelectorAll<HTMLInputElement>('input[name="satellite"]:checked')].map(input => input.value);
    if (!satellites.length) throw new Error('Select at least one satellite.');
    for (const id of ['days', 'coverage', 'sun']) if (!$<HTMLInputElement>(id).checkValidity() || !Number.isFinite(number(id))) throw new Error(`Enter a valid value for ${id}.`);
    const tle = value('tle-text').trim();
    const date = startTime.read();
    const request: PredictionRequest = {
      roi, roi_name: value('roi-name'), satellites, tle_text: tle,
      orbital_source: 'User-supplied TLE',
      start_utc: date.toISOString(), days: number('days'), timezone: value('timezone').trim(),
      imaging_only: $<HTMLInputElement>('imaging-only').checked, min_coverage: number('coverage'), min_sun_elevation: number('sun'), s1_mode: value('s1-mode') as 'IW' | 'EW',
    };
    requestId++; lastProgress = undefined; runStart = performance.now(); stageStart = runStart; runRevision = revision;
    activeId = requestId;
    setBusy(true); $('status-title').textContent = 'Preparing calculation'; $('status-detail').textContent = 'Your region and inputs remain on this device.';
    if (matchMedia('(max-width: 760px)').matches) $('status-panel').scrollIntoView({block: 'center'});
    if (!tle) {
      retrieval = new AbortController();
      $('status-title').textContent = 'Downloading orbital elements';
      const inputs = await fetchOrbitalInputs(satellites, catalogue, retrieval.signal, (name, completed, total) => {
        if (activeId !== requestId) return;
        $('status-detail').textContent = `${name} · ${completed} of ${total} elements ready`;
        const progress = $<HTMLProgressElement>('progress'); progress.hidden = false; progress.max = total; progress.value = completed;
      });
      if (activeId !== requestId) return;
      retrieval = undefined;
      Object.assign(request, inputs);
      orbitalDescription = inputs.description;
      $('orbital-info').textContent = orbitalDescription;
      updateOrbitalBadge();
    }
    if (activeId !== requestId) return;
    const calculated = await runtime.predict(request, message => { if (activeId === requestId) reportProgress(message); });
    if (activeId === requestId) showResult(calculated);
  } catch (error) {
    if (activeId !== undefined && activeId !== requestId) return;
    const wasCalculating = !retrieval && activeId !== undefined;
    retrieval = undefined;
    if (wasCalculating) runtime.terminate();
    fail((error as Error).message);
    setBusy(false); $('progress').hidden = true;
    $('status-title').textContent = activeId === undefined ? 'Check prediction settings' : wasCalculating ? 'Calculation could not finish' : 'Orbital download could not finish';
    $('status-detail').textContent = 'Check your inputs or retry. You can also paste or upload orbital elements.';
    if (activeId !== undefined) (document.querySelector('.orbital-input') as HTMLDetailsElement).open = true;
  }
};
$('cancel').onclick = () => {
  requestId++; retrieval?.abort(); retrieval = undefined; runtime.terminate(); setBusy(false); $('progress').hidden = true;
  $('status-title').textContent = 'Calculation cancelled'; $('status-detail').textContent = 'Your inputs are preserved. Calculate again whenever you are ready.';
};
function updateFilters() {
  $<HTMLSelectElement>('filter-satellite').innerHTML = '<option value="">All satellites</option>' + [...new Set(result!.document.passes.map(p => p.satellite))].map(name => `<option>${escape(name)}</option>`).join('');
}
function selectPass(index: number) { selected = index; map.focus(result!, index); renderResults(); }
function renderResults() {
  if (!result) return;
  visible = result.document.passes.map((_, i) => i).filter(i => {
    const pass = result!.document.passes[i];
    return (!value('filter-satellite') || pass.satellite === value('filter-satellite')) && (!value('filter-direction') || pass.direction.toLowerCase() === value('filter-direction'));
  });
  visible.sort((a, b) => {
    const p = result!.document.passes[a], q = result!.document.passes[b];
    const order = value('sort') === 'coverage' ? q.roi_coverage_pct - p.roi_coverage_pct : value('sort') === 'satellite' ? p.satellite.localeCompare(q.satellite) : p.overpass_utc.localeCompare(q.overpass_utc);
    return sortDescending ? -order : order;
  });
  $('result-count').textContent = `${visible.length} / ${result.document.count}`;
  if (!dirty) $('result-context').textContent = result.document.count === 0 ? 'No passes met the prediction settings. Try a longer window or different settings.' : `${visible.length} visible of ${result.document.count} calculated passes. Display filters cannot recover passes excluded by prediction settings.`;
  $('results-body').innerHTML = visible.length ? visible.map(index => {
    const pass = result!.document.passes[index], colour = catalogue[pass.satellite]?.colour || '#2563b9';
    const notes = pass.status.warnings.map(w => w.message).join(' ');
    return `<tr data-pass="${index}" class="${selected === index ? 'selected' : ''}"><td><button type="button" class="pass-select" aria-label="Select ${escape(pass.satellite)} pass at ${escape(pass.overpass_utc)}"><span class="sat-dot" style="background:${colour}"></span>${escape(pass.satellite)}</button><small>${escape(pass.sensor)}</small></td><td><strong>${escape(pass.overpass_local.replace('T', ' '))}</strong><small>${escape(pass.overpass_utc.replace('T', ' '))} · UTC</small></td><td><strong>${pass.roi_coverage_pct.toFixed(1)}%</strong></td><td>${escape(pass.direction)}</td><td>${pass.incidence_angle_deg == null ? `${pass.view_angle_deg.toFixed(2)}° view` : `${pass.incidence_angle_deg.toFixed(2)}° incidence`}<small>${escape(pass.status.imaging_label)}</small></td><td><span class="freshness ${escape(pass.status.orbital_data)}">${escape(pass.status.orbital_data)} · ${Math.abs(pass.status.tle_age_days).toFixed(1)} d</span><small>${escape(pass.status.acquisition_label)}</small>${notes ? `<details><summary>Notes</summary><p>${escape(notes)}</p></details>` : ''}</td></tr>`;
  }).join('') : '<tr><td colspan="6" class="empty-state">No passes match this view. Adjust display filters or run a new calculation.</td></tr>';
  document.querySelectorAll<HTMLTableRowElement>('[data-pass]').forEach(row => row.querySelector('button')!.onclick = () => selectPass(Number(row.dataset.pass)));
  map.results(result, visible, selected, catalogue, selectPass);
  $<HTMLTextAreaElement>('csv-preview').value = exportText(result, 'csv', visible).text;
}
for (const id of ['filter-satellite', 'filter-direction', 'sort']) $(id).onchange = () => {sortDescending = false; renderResults();};
$('sort-time').onclick = () => { $<HTMLSelectElement>('sort').value = 'time'; sortDescending = !sortDescending; renderResults(); };
$('sort-coverage').onclick = () => { $<HTMLSelectElement>('sort').value = 'coverage'; sortDescending = !sortDescending; renderResults(); };
$('view').onchange = () => { $('table-wrap').hidden = value('view') === 'csv'; $('csv-wrap').hidden = value('view') !== 'csv'; };
document.querySelectorAll<HTMLButtonElement>('[data-download]').forEach(button => button.onclick = () => result && downloadResult(result, button.dataset.download!));
$('download-filtered').onclick = () => result && downloadResult(result, value('filtered-format'), visible);
for (const [id, checked] of [['select-all', true], ['select-none', false]] as const) $(id).onclick = () => {document.querySelectorAll<HTMLInputElement>('input[name="satellite"]').forEach(input => input.checked = checked); changed(); live.refresh();};
async function loadStaticInputs() {
  try {
    const response = await fetch(new URL('catalogue.json', base));
    if (!response.ok) throw new Error('Build assets are missing. Run the website preparation script.');
    catalogue = await response.json();
    $('satellite-options').innerHTML = [...new Set(Object.values(catalogue).map(s => s.mission))].map(mission => `<div class="mission-group"><span>${escape(mission)}</span><div>${Object.entries(catalogue).filter(([, info]) => info.mission === mission).map(([name, info]) => `<label class="satellite-chip"><input type="checkbox" name="satellite" value="${escape(name)}" checked><span class="sat-dot" style="background:${info.colour}"></span>${escape(name.replace('SENTINEL-', 'S').replace('LANDSAT ', 'L'))}</label>`).join('')}</div></div>`).join('');
    const build = await (await fetch(new URL('build.json', base))).json(); $('version').textContent = `ENGINE ${build.package_version} · PYODIDE`;
  } catch (error) { fail((error as Error).message); }
  updateOrbitalBadge();
}
void loadStaticInputs();
// Tests exercise the exact worker factory used by the production application.
Object.assign(window, {createPredictionWorker: () => new Worker(new URL('./worker.ts', import.meta.url), {type: 'module'})});
