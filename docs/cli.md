# CLI

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
select all five supported satellites and daytime descending passes. Use
`--all-passes` to include other geometric opportunities, `--satellite` to select
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

## Read a pass status

Each terminal/JSON/HTML pass separates:

- **Geometry:** the model's nominal swath intersects the ROI; coverage is reported
  separately as a percentage.
- **Imaging:** expected or not expected under the direction, solar-elevation and
  mission-latitude rules. This is not an acquisition observation.
- **Acquisition evidence:** planned nominal, planned other mode, no matching
  datatake in a loaded plan, unknown because no plan covers that date, or not
  checked for Landsat. A missing plan never becomes a negative plan match.
- **Orbital data:** absolute distance between the supplied TLE epoch and the
  pass epoch, using the model's reported age. Fresh is <=7 days, aged is >7 to
  14 days, and stale is >14 days. These labels are refresh reminders, not
  statistical confidence levels. Historical calculations also flag elements
  far into the future relative to the requested pass.

Per-pass notes highlight aged elements, margins near the nominal swath edge,
unknown plans and non-nominal acquisition modes. Imagery availability and cloud
cover are not checked. Keep predictions near the element epoch.
