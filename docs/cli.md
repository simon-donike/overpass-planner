# CLI and automation

## Find upcoming passes

```bash
eo-overpass next --point 144.96 -37.82
eo-overpass next --bbox 144.35 -38.35 145.15 -37.80 --days 7 --min-coverage 80
eo-overpass next --roi area.geojson --satellite S2 --fetch-plans
eo-overpass next --point 144.96 -37.82 --output planner.html
```

Choose exactly one location input. Coordinates use WGS84 **longitude, latitude**;
bounding boxes use west, south, east, north. The default window starts at the
current UTC minute and lasts 14 days. `--start` fixes the start for reproducibility;
`--days` accepts fractional days, with a minimum window of one second. Defaults
select all seven supported satellites. Optical sensors default to daytime descending passes; Sentinel-1 is eligible day/night on ascending/descending passes. Use
`--all-passes` to include other optical geometric opportunities, `--satellite` to select
instruments, and `--timezone` to override automatic ROI-local time selection.

No output path is needed for a terminal report. Optional `--output` suffixes are
`.csv`, `.geojson` (or `.json`), `.gpkg`, and `.html` (or `.htm`). The existing
`predict` and `tracker` commands retain explicit ROI-file/start/end/output inputs;
both now support coverage, illumination and acquisition-plan options, and
`predict` also accepts HTML exports.

Orbital data are downloaded/cached by default. `--offline`, `--tle-file`,
`--cache-dir` and `--no-cache` work as on `predict`. ESA acquisition plans are
checked only with local `--plans` or explicit `--fetch-plans`. Plan-download
failure falls back to cached plans with a warning. Offline mode forbids fetching.
Use `--s1-mode IW|EW` with `next`, `predict` or `tracker` when Sentinel-1 is
selected; IW is the default and one mode applies to both Sentinel-1 units. The
nominal right-looking footprint reports mode, look side, incidence angle and
edge margin. Plan matches may add `esa_mode`, `esa_polarisation`, datatake and
relative-orbit fields.

## Read a pass status

Each terminal/JSON/HTML pass separates:

- **Geometry:** the model's nominal swath intersects the ROI; coverage is reported
  separately as a percentage.
- **Imaging:** expected or not expected under the mission rules. Optical sensors
  use direction, solar-elevation and latitude filters; Sentinel-1 is eligible day
  and night on either orbit direction. This is not an acquisition observation.
- **Acquisition evidence:** planned nominal, planned other mode, Sentinel-1
  `planned`, no matching datatake in a loaded plan, unknown because no plan covers
  that date, or not checked for Landsat. A missing plan never becomes a negative
  plan match.
- **Orbital data:** absolute distance between the supplied TLE epoch and the
  pass epoch, using the model's reported age. Fresh is <=7 days, aged is >7 to
  14 days, and stale is >14 days. These labels are refresh reminders, not
  statistical confidence levels. Historical calculations also flag elements
  far into the future relative to the requested pass.

Per-pass notes highlight aged elements, margins near the nominal swath edge,
unknown plans and non-nominal acquisition modes. Imagery availability and cloud
cover are not checked. Keep predictions near the element epoch.

## Use the HTML planner

Select a pass in the list or timeline to move the map and satellite telemetry to
its closest-approach time. The map highlights its exported display footprint
and frames the ROI and footprint. **Return to live** resumes real-time propagation;
the clocks always show current UTC and ROI-local time.

Filters select satellites, minimum coverage, acquisition evidence and imaging
eligibility. Sorting offers earliest closest approach or highest coverage, with
earlier time breaking coverage ties. The timeline remains chronological. Past
passes are hidden by default; expired reports automatically include past passes
for inspection. The prediction window and expired-report notice are visible
above the map. An active crossing is labelled **pass active**, without implying
an acquisition was observed.

These are view filters on a snapshot. To include passes excluded by the original
prediction options, generate a new report. Live positions do not refresh embedded
TLEs or extend the prediction window. As before, satellite.js and fonts load from
public CDNs; the HTML report is not a fully offline browser bundle.

## JSON response contract (2.0)

```bash
eo-overpass next --point 144.96 -37.82 --days 7 --json > passes.json
eo-overpass satellites --json
eo-overpass cache info --json
```

Place `--json` after the leaf command (`cache info --json`, for example).
`next`, `predict`, `tracker`, `satellites`, `cache info` and `cache clear` emit one
strict JSON response on stdout; progress, warnings and readable error diagnostics
go to stderr. `--help` retains ordinary help output. Shell/process failures and
unexpected programming errors are outside the response contract.

The authoritative [JSON Schema](cli-output.schema.json) describes all response
variants. Successful prediction responses contain:

| Field | Meaning |
|---|---|
| `schema_version`, `ok`, `command` | Contract version, success flag, invoked command. |
| `status`, `count`, `passes` | `ok` or `empty`, count, and pass records. Empty predictions are successful with `passes: []`. |
| `generated_at` | UTC time the response was generated. |
| `request` | Resolved UTC window, ROI geometry/CRS/name, timezone, canonical satellites, filters, sampling steps, offline mode and plan request. |
| `provenance` | Package version and each satellite's TLE source, epoch, NORAD ID and SHA-256 of `line1 + "\n" + line2` (UTF-8). |
| `assumptions` | Scientific and interpretation limitations. |
| `warnings` | Runtime/retrieval diagnostics, each with a `code` and `message`. Pass-specific warnings are in each pass's `status.warnings`. |
| `output` | Absolute requested export path, or `null` when no file was requested. |

Each pass includes existing scientific columns, a GeoJSON `geometry`, and a
`status` object. Machine codes are `geometry: intersects`,
`imaging: expected|not_expected`,
`acquisition: planned_nominal|planned_other|planned|not_planned|unknown|not_checked`,
`orbital_data: fresh|aged|stale`, and `imagery: not_checked`. Human label fields
are for display; use codes for decisions. Optional ESA fields are present when
plans were cross-checked. Missing values serialize as `null`, never NaN.

UTC timestamps end in `Z`; local timestamps include their date-specific UTC
offset, retaining subsecond model times. `.json` export paths continue to mean
GeoJSON footprints; `--json` controls the stdout response independently.

Expected errors have the form:

```json
{"schema_version":"2.0","ok":false,"command":"next","error":{"code":"invalid_input","message":"Choose exactly one location input."}}
```

Exit codes remain **0** for success (including empty results), **2** for invalid
inputs (including parsing errors), and **1** for operational failures, whose
error code is `operational_error`. Consumers should tolerate additional fields;
removing fields or changing meanings requires a schema version change.

## Optional browser verification

Python tests cover the CLI contract, serialization and labels. The browser check
also exercises actual planner selection, keyboard controls, sorting, all filters,
returning to live mode, expired windows, empty states and mobile overflow. It
uses synthetic status labels on frozen prediction geometry, not observational
acquisition evidence. With Node.js installed, a separate temporary tool directory
keeps browser dependencies outside the Python package:

```bash
npm install --prefix /tmp/eo-browser-tools playwright@1.58.2
/tmp/eo-browser-tools/node_modules/.bin/playwright install chromium
curl -fsSL https://cdn.jsdelivr.net/npm/satellite.js@5.0.0/dist/satellite.min.js \
  -o /tmp/eo-satellite.min.js
EO_BROWSER_TESTS=1 \
EO_PLAYWRIGHT_MODULE=/tmp/eo-browser-tools/node_modules/playwright \
EO_SATELLITE_JS=/tmp/eo-satellite.min.js \
uv run pytest tests/test_planner_browser.py -q
```

The test itself serves the local satellite.js file and blocks font requests; it
needs no network data. `EO_CHROMIUM_PATH` optionally selects an existing Chromium
executable. The ordinary Python suite skips this opt-in browser check.
