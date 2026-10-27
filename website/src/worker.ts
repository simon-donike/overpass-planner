/// <reference lib="webworker" />
import type { PositionsRequest, PredictionRequest } from './types';

// The runtime is a static, pinned build asset rather than an npm-bundled interpreter.
interface Pyodide {
  loadPackage(names: string[]): Promise<void>;
  runPythonAsync(code: string): Promise<unknown>;
  runPython(code: string): unknown;
  globals: { set(name: string, value: unknown): void };
  FS: { writeFile(path: string, data: string): void };
}
let pyodide: Pyodide | undefined;
let initialized: Promise<void> | undefined;
let ready = false;

async function checkedFetch(url: URL): Promise<Response> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Unable to load ${url.pathname} (${response.status}). Build the website assets or retry the download.`);
  return response;
}

async function initialize(base: string, id: number) {
  const root = new URL(base);
  const build = await (await checkedFetch(new URL('build.json', root))).json();
  self.postMessage({type: 'progress', id, stage: 'runtime', label: 'Starting Python in your browser', completed: 0, total: 0, elapsed: 0});
  const runtimeUrl = new URL('runtime/pyodide.mjs', root).href;
  const runtime = await import(/* @vite-ignore */ runtimeUrl);
  pyodide = await runtime.loadPyodide({indexURL: new URL('runtime/', root).href});
  if (!pyodide) throw new Error('Python runtime did not initialize.');
  self.postMessage({type: 'progress', id, stage: 'dependencies', label: 'Loading scientific and geospatial libraries', completed: 0, total: 0, elapsed: 0});
  await pyodide.loadPackage(build.packages);
  pyodide.globals.set('wheel_urls', build.wheels.map((wheel: {url: string; sha256: string}) => new URL(wheel.url, root).href + '#sha256=' + wheel.sha256));
  await pyodide.runPythonAsync('import micropip\nawait micropip.install(wheel_urls.to_py(), deps=False)');
  const adapter = await (await checkedFetch(new URL('browser_adapter.py', root))).text();
  pyodide.FS.writeFile('/home/pyodide/browser_adapter.py', adapter);
  const catalogue = await pyodide.runPythonAsync('import browser_adapter\nbrowser_adapter.catalogue_json()');
  ready = true;
  self.postMessage({type: 'ready', catalogue: JSON.parse(String(catalogue)), version: build.package_version, id});
}

type Request = {type: string; id: number; base: string; request?: PredictionRequest | PositionsRequest};
// Initialization and Python calls must never overlap, even when live tracking is
// enabled just before a prediction starts. No second interpreter is created.
let queue = Promise.resolve();
self.onmessage = (event: MessageEvent<Request>) => { queue = queue.then(() => handle(event.data)); };
async function handle({type, id, base, request}: Request) {
  try {
    initialized ??= initialize(base, id);
    await initialized;
    if (request && pyodide) {
      // Timestamp a live batch after the first, potentially slow runtime load.
      if (type === 'tracks' && 'when_utc' in request && request.when_utc === 'now') request = {...request, when_utc: new Date().toISOString()};
      pyodide.globals.set('request_json', JSON.stringify(request));
      pyodide.globals.set('notify_progress', (stage: string, label: string, completed: number, total: number, elapsed: number) => {
        self.postMessage({type: 'progress', id, stage, label, completed, total, elapsed});
      });
      const code = type === 'positions' ? 'browser_adapter.positions_json(request_json)'
        : type === 'tracks' ? 'browser_adapter.tracks_json(request_json)'
        : 'browser_adapter.run_request_json(request_json, notify_progress)';
      const result = await pyodide.runPythonAsync(code);
      self.postMessage({type: type === 'positions' || type === 'tracks' ? type : 'result', id, result: JSON.parse(String(result))});
    }
  } catch (error) {
    console.error(error);
    const message = error instanceof Error ? error.message : String(error);
    self.postMessage({type: 'error', id, fatal: !ready, message: message.trim().split('\n').at(-1) || message});
  }
}
