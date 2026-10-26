# Overpass: browser-only overpass planner

A separate static website using Julian Manning's **eo-overpass-predictor** Python
package. The browser loads an unchanged wheel built from this checkout and runs
predictions in a Pyodide module worker. No application backend, database, Docker
service or hosted prediction computation is involved. The Python package and its configuration
are not modified by the website.

The map supports point, rectangle and polygon drawing, editing and deletion,
coordinate entry, and WGS84 GeoJSON uploads (Geometry, Feature or FeatureCollection).
The planner exposes satellite selection, UTC start time, duration, ROI timezone,
coverage/illumination filters and Sentinel-1 IW/EW. Results include selectable map
footprints, a sortable table, CSV preview and CSV/GeoJSON/JSON downloads. Complete
and filtered downloads are labelled separately.

## Run locally

From the repository root, with Python/uv and Node.js **22+** installed:

```bash
uv sync --all-extras
uv run python website/scripts/prepare.py
cd website
npm ci
npm run dev
```

Open the printed localhost URL. The development server only serves files; scientific
calculations and orbital-data retrieval run in the browser. Click Calculate to
download TLEs directly for the selected satellites, or paste/upload your own TLEs.
Never use the historical test TLEs as current elements.

`npm run prepare:python` is an equivalent preparation shortcut from `website/`.
First preparation needs internet access; pinned runtime assets and
downloads are subsequently reused. The first browser calculation loads Python and
the geospatial libraries. This is a sizeable download (roughly 45 MiB of static
assets); subsequent calculations reuse the worker. No speed or mobile-memory
guarantee is implied. Long calculations have no fixed deadline.

## Cloudflare deployment

The production address is **https://overpass.hyperalislabs.com**, deployed
as the `overpass` Worker using **Workers Static Assets**. There is no Worker script,
server-side calculation, database, R2 bucket, upload endpoint or scheduled job.
Cloudflare serves the website's static files; TLE requests go directly from the
browser to the orbital-data providers.

After preparing the Python assets above:

```bash
cd website
npm ci
npx wrangler login  # only needed if this machine is not already authenticated
npm run build
npm run deploy:check
npm run deploy
```

`wrangler.jsonc` declares the custom domain. The authenticated Cloudflare account
must own an active `hyperalislabs.com` zone; Wrangler provisions the subdomain and
TLS certificate. Change the route/name for another account. No GitHub integration
or Actions execution is needed for deployment or fresh TLEs. Publishing updates
is an explicit local command. Wrangler requires Node 22 or newer.

The website uses a **simplified timezone boundary dataset**, rather than splitting
large files or routing around the host's limits. `scripts/simplify_timezones.py`
decodes the pinned upstream database, reduces boundary vertices with GEOS coverage
simplification (tolerance parameter **0.01 degrees**), and rebuilds every polygon,
hole and spatial index with the checksummed upstream 9.0.0 compiler. It retains all
zones, polygons and holes. The tolerance is an algorithm parameter, not a guaranteed
maximum distance error. Near timezone borders or coastlines, automatic selection
can differ; enter a manual IANA timezone when that matters. UTC predictions,
propagation, coverage, illumination and footprint calculations are unchanged.

The derived data wheel is labelled `+overpass.1`, carries its source/compiler hashes,
vertex counts, simplification settings, sample diagnostics and original ODbL data
licence. Its build enforces a **10 MiB** size budget. The original source wheel and
Python package remain unchanged. No split-file loader or extra storage service is
used. The first preparation rebuilds the global spatial index and can take several
minutes; later builds reuse the checksummed result. Build-only Pydantic and Numba dependencies
are isolated under `website/.cache/build-deps/` and never shipped to browsers.

## Build and optional GitHub Pages

```bash
cd website
npm run build
npm run preview
```

Deploy **only `website/dist/`** to any static host. The relative Vite base supports
both `https://owner.github.io/repository/` and a custom-domain root. All Python
assets are self-hosted; the production site needs no cross-origin isolation,
SharedArrayBuffer or special response headers. Do not open `index.html` with
`file://`; browsers require HTTP(S) for module workers and resource loading.

GitHub Actions is **optional for building/publishing**, with no role in retrieving
orbital data or keeping a deployed site current. You can build locally and upload
`dist/` to any static host. The supplied `.github/workflows/website.yml` is a
convenient deployment option:

1. Merge the website into the repository's default branch.
2. Under **Settings → Pages → Build and deployment**, select **GitHub Actions**.
3. Run **Browser planner · GitHub Pages** manually with **publish_pages** enabled.
   The workflow builds and tests before publishing. Pushes and pull requests run
   validation only; they do not publish or change the Cloudflare deployment.
4. Optionally configure a custom domain in Pages settings.

The workflow runs on code changes or manual dispatch. It contains no schedule,
orbital-data download or data-publishing step. Existing deployments fetch their own
TLEs regardless of when the site was built or whether Actions is enabled.

## Direct browser orbital data

`src/orbital-data.ts` fetches only the selected NORAD IDs when a calculation needs
them, using ordinary CORS-enabled HTTPS GET requests to CelesTrak. There is no
download on page load, polling, proxy, credential or scheduled job. A failed primary
request tries the existing TLE API community mirror; if it is unavailable or its
CORS policy prevents access, the browser uses a previously downloaded valid cache
with an explicit warning, or asks for pasted/uploaded TLEs. It does not silently
drop unavailable satellites.

Successful downloads are cached in this browser's localStorage for two hours,
matching [CelesTrak's update/usage policy](https://celestrak.org/usage-policy.php).
If persistent storage is unavailable, an in-memory cache lasts for the page session.
Failures pause further requests to that provider for two hours, including across
reloads when storage is available, to avoid repeatedly querying a failing or
rate-limited service. Manually supplied elements bypass network requests and the
download cache. No hosting rebuild is needed to update orbital data.

The browser validates paired NORAD IDs and TLE checksums before caching, then the
unchanged Python package validates the text again. Source and original retrieval
time are recorded per satellite in downloaded JSON provenance. Reusing a cache
never changes its retrieval timestamp. Cache reuse time and per-pass orbital-epoch
freshness are distinct; the existing 7/14-day scientific labels are unchanged.

Cancel also aborts an in-progress orbital download. The providers receive satellite
IDs and the usual connection metadata, including the user's IP and browser Origin;
the region, settings, files and results are not sent to them. Direct requests depend
on providers being available and continuing to allow browser access. The manual TLE
input remains usable during outages, for historical elements and offline work once
the application's runtime assets have loaded.

There is no hosted TLE snapshot or automatic fallback to bundled historical inputs. Test data is excluded
from the production build even if present during development. No uploads or result
files are saved to the hosting service. Map tiles are fetched from OpenStreetMap;
the tile provider can infer the viewed map area. Change `VITE_TILE_URL` and
`VITE_TILE_ATTRIBUTION` together at build time to use another provider. Keep visible
attribution and follow the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/).

## Browser integration and dependency manifest

`python-dependencies.json` pins Pyodide **314.0.7** and additional Python dependencies.
`scripts/prepare.py` downloads the runtime and the transitive package set from its
checksummed lockfile, checks runtime and package hashes, builds the project's unchanged wheel with `uv
build`, and writes a generated `build.json` containing wheel hashes and the package
version. The worker uses those hashes when installing wheels. Build outputs,
caches and dependencies are ignored by Git.

Pyodide supplies NumPy, pandas, GeoPandas, Shapely, pyproj, H3 and CFFI. Its GeoPandas
distribution uses Fiona instead of pyogrio. Dependency resolution is disabled
when installing the separately pinned wheels because that browser distribution
already supplies its geospatial dependencies. Every calculation is tested through
the real package; no substitute propagation/geometry modules are installed.

SGP4's upstream pure-Python wheel uses its supported accelerator fallback.
timezonefinder 9.0.0 is packaged from its checksummed upstream source archive with
its Python modules, original metadata, schemas and licence; its optional C
accelerator is absent, using the upstream pure-Python implementation instead.
Timezone boundary data is a website-owned simplified derivative of the separately
pinned upstream data wheel, as described above. The upstream library code is unchanged.

`python/browser_adapter.py` contains the browser I/O orchestration. It calls the
package's ROI loader, offline TLE parser, full-window predictor, presentation
document builder and CSV exporter. Export files exist briefly in Pyodide's virtual
filesystem and are returned as download content. It never changes package files.

One scoped ABI compatibility bridge is required by timezonefinder 9.0: on wasm32,
NumPy `repeat`/`reduceat` require native-width indexes while timezonefinder constructs
explicit 64-bit counts. During automatic timezone lookup the adapter converts
bounded integer repeat counts and hole-index arrays to `np.intp`, then restores the
original NumPy functions in `finally`. Coordinates, polygon tests and orbital
calculations are unchanged. Browser/native parity tests cover automatic ROI lookup.

Progress reports runtime loading, dependencies, inputs, whole-satellite calculation
and export. Satellites are calculated individually over the **complete** requested
window, retaining the package's 30-second coarse / 1-second fine sampling, then
globally sorted. ETA is a rough estimate from completed satellites; one difficult
satellite can take longer. Cancel terminates the worker. A subsequent run creates
a new worker; previous completed downloads remain available. Request IDs prevent
late/cancelled responses from replacing results, and settings edited during a run
remain marked as requiring recalculation.

The main browser thread retrieves orbital data before starting the calculation
worker; its abort controller and request ID also prevent cancelled downloads from
starting obsolete calculations. Worker messages use `predict` with `{id, base, request}` and return `progress`,
`ready`, `result` or `error` with the same ID. `request` carries ROI GeoJSON, selected
catalogue names, UTC start, days, timezone override, filters, SAR mode and TLE text,
plus optional per-satellite orbital sources and retrieval warnings.
`result.document` retains the package's version 2.0 prediction response, alongside
CSV content/rows and elapsed time. This internal website protocol does not change
the package or CLI contracts.

## Validation

```bash
# From the repository root:
uv run python website/scripts/prepare.py --test-data
uv run python website/tests/prepare_reference.py
cd website
npm ci
npx playwright install chromium
npm run check
npm test
npm run build
```

Set `EO_CHROMIUM_PATH` to use an existing Chromium executable. Set `WEBSITE_PYTHON`
to an absolute Python executable if the root environment is not `.venv`. Native
Python expectations are generated into ignored **test artifacts**, not regression
baselines. Browser tests serve all interpreter/wheel assets locally and block or
mock external requests, including map tiles and orbital data.

Browser retrieval tests cover selected-satellite requests, CORS/network/HTTP failure,
community-mirror fallback, cache expiry/reuse, stale-cache warnings, corrupted
responses/cache entries, unavailable storage and cancellation during downloads.
All providers are mocked; the test suite does not depend on upstream services.
The suite exercises real Pyodide predictions against native Python for optical
point/polygon inputs, multi-satellite merging, empty results, Sentinel-1 IW/EW and
timezone/DST handling. It compares timestamps, rounded quantities, CSV output and
footprint geometry using the repository's 10 cm parity tolerance. This establishes
implementation parity, not independent observational accuracy. Separate UI tests
cover drawing/editing/deletion, uploads, validation, options, map/table selection,
complete and filtered downloads, cancellation, error recovery, stale responses,
settings changes, keyboard operation and narrow layouts.

Also run the repository's standard Python and distribution checks from `AGENTS.md`.
Existing package CI and release workflows remain separate.

## Scientific interpretation

Geometric intersection, expected imaging, ESA plan evidence and available imagery
are distinct. This release does not retrieve ESA plans or imagery: Sentinel
acquisition is unknown and Landsat acquisition is not checked. Freshness labels
at 7/14 days are refresh reminders, not calibrated accuracy probabilities. The
package's swath-edge, SAR nominal-geometry and geographic limitations apply.
Display filters cannot recover passes excluded at calculation time.

Scientific implementation and package resources: **Julian Manning**, MIT licence.
The built wheel retains the original licence and author metadata. Frontend mapping
uses Leaflet (BSD-2-Clause), Leaflet.draw (MIT) and attributed OpenStreetMap data.
Python dependency wheels retain their upstream licences. The derived timezone
dataset has an explicit local version and provenance; it is distributed under the
original ODbL terms.
