import type { SatelliteInfo } from './types';

export const CACHE_TTL_MS = 2 * 60 * 60 * 1000;
const PREFIX = 'orbit-window.tle.v1.';
const memory = new Map<string, unknown>();

interface CachedTLE {
  norad_id: number;
  line1: string;
  line2: string;
  source: string;
  retrieved_at: string;
}

export interface OrbitalInputs {
  tle_text: string;
  orbital_source: string;
  orbital_sources: Record<string, string>;
  orbital_warnings: {code: string; message: string}[];
  description: string;
}

function read(key: string): unknown {
  try {
    const stored = localStorage.getItem(PREFIX + key);
    if (stored) return JSON.parse(stored);
  } catch { /* Storage may be unavailable in private browsing or full. */ }
  return memory.get(key);
}

function write(key: string, entry: unknown) {
  memory.set(key, entry);
  try { localStorage.setItem(PREFIX + key, JSON.stringify(entry)); } catch { /* Memory cache still works. */ }
}

function validLine(line: string, number: number, norad: number): boolean {
  if (line.length !== 69 || !line.startsWith(`${number} `) || Number(line.slice(2, 7)) !== norad) return false;
  const checksum = [...line.slice(0, 68)].reduce((total, char) => total + (/\d/.test(char) ? Number(char) : char === '-' ? 1 : 0), 0);
  return /\d/.test(line[68]) && checksum % 10 === Number(line[68]);
}

function parseTLE(text: string, norad: number): {line1: string; line2: string} {
  const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  for (let i = 0; i < lines.length - 1; i++) {
    if (validLine(lines[i], 1, norad) && validLine(lines[i + 1], 2, norad)) return {line1: lines[i], line2: lines[i + 1]};
  }
  throw new Error(`No checksum-valid TLE pair for NORAD ${norad}.`);
}

function cachedTLE(norad: number, now: number): CachedTLE | undefined {
  const entry = read(String(norad)) as Partial<CachedTLE> | undefined;
  if (!entry || entry.norad_id !== norad || typeof entry.line1 !== 'string' || typeof entry.line2 !== 'string' || typeof entry.source !== 'string' || typeof entry.retrieved_at !== 'string') return;
  const time = Date.parse(entry.retrieved_at);
  if (!Number.isFinite(time) || time > now || !validLine(entry.line1, 1, norad) || !validLine(entry.line2, 2, norad)) return;
  return entry as CachedTLE;
}

function aborted(signal: AbortSignal) {
  if (signal.aborted) throw new DOMException('Orbital data download cancelled.', 'AbortError');
}

async function request(url: string, signal: AbortSignal): Promise<string> {
  aborted(signal);
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, {once: true});
  const timeout = setTimeout(() => controller.abort(), 20_000);
  try {
    const response = await fetch(url, {mode: 'cors', credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer', signal: controller.signal});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.text();
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener('abort', abort);
  }
}

const providers = [
  {name: 'CelesTrak', url: (norad: number) => `https://celestrak.org/NORAD/elements/gp.php?CATNR=${norad}&FORMAT=TLE`, json: false},
  {name: 'TLE API community mirror', url: (norad: number) => `https://tle.ivanstanojevic.me/api/tle/${norad}`, json: true},
];

/** Fetch on use, respecting CelesTrak's two-hour update cycle and error backoff. */
export async function fetchOrbitalInputs(
  names: string[], catalogue: Record<string, SatelliteInfo>,
  signal: AbortSignal, progress: (name: string, completed: number, total: number) => void,
): Promise<OrbitalInputs> {
  const records: {name: string; entry: CachedTLE; cached: boolean; stale: boolean}[] = [];
  const warnings: OrbitalInputs['orbital_warnings'] = [];
  for (const name of names) {
    aborted(signal);
    progress(name, records.length, names.length);
    const norad = catalogue[name]?.norad_id;
    if (!norad) throw new Error(`Unknown satellite: ${name}`);
    const now = Date.now();
    const cached = cachedTLE(norad, now);
    if (cached && now - Date.parse(cached.retrieved_at) < CACHE_TTL_MS) {
      records.push({name, entry: cached, cached: true, stale: false});
      continue;
    }
    let entry: CachedTLE | undefined;
    const failures: string[] = [];
    for (const provider of providers) {
      aborted(signal);
      const backoff = read(`failure.${provider.name}`) as {at?: number} | undefined;
      if (typeof backoff?.at === 'number' && backoff.at <= now && now - backoff.at < CACHE_TTL_MS) {
        failures.push(`${provider.name}: paused after a previous download failure`);
        continue;
      }
      try {
        const response = await request(provider.url(norad), signal);
        let text: string;
        if (provider.json) {
          const data = JSON.parse(response);
          if (typeof data.line1 !== 'string' || typeof data.line2 !== 'string') throw new Error('Invalid mirror response');
          text = `${data.line1}\n${data.line2}`;
        } else text = response;
        aborted(signal);
        entry = {norad_id: norad, ...parseTLE(text, norad), source: provider.name, retrieved_at: new Date().toISOString()};
        write(String(norad), entry);
        break;
      } catch (error) {
        aborted(signal); // Cancelling must never become a provider failure or stale fallback.
        write(`failure.${provider.name}`, {at: Date.now()});
        failures.push(`${provider.name}: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
    if (entry) records.push({name, entry, cached: false, stale: false});
    else if (cached) {
      records.push({name, entry: cached, cached: true, stale: true});
      warnings.push({code: 'orbital_cache_fallback', message: `${name}: download failed; using browser-cached elements retrieved ${cached.retrieved_at}. Check per-pass element freshness or supply newer TLEs. ${failures.join('; ')}.`});
    } else throw new Error(`Unable to download orbital elements for ${name}. ${failures.join('; ')}. Paste or upload TLEs to continue. Providers may be offline or block cross-origin requests; no proxy is used.`);
  }
  aborted(signal);
  progress('Orbital elements ready', records.length, names.length);
  const sources = Object.fromEntries(records.map(({name, entry, cached, stale}) => [name, `${entry.source}; retrieved ${entry.retrieved_at}${stale ? '; stale browser cache (download failed)' : cached ? '; browser cache' : '; direct browser download'}`]));
  return {
    tle_text: records.map(({name, entry}) => `${name}\n${entry.line1}\n${entry.line2}`).join('\n') + '\n',
    orbital_source: 'Direct browser retrieval', orbital_sources: sources, orbital_warnings: warnings,
    description: records.map(({name}) => `${name}: ${sources[name]}`).join('\n'),
  };
}
