# Command line

Use `eo-overpass satellites` to list instruments, `predict` to write CSV, GeoJSON,
GeoPackage or HTML reports, and `tracker` to export the standalone HTML tracker.
Both prediction commands require `--roi FILE`, `--start`, `--end`, and `--output`.
`--satellite S2` selects Sentinel-2A/B/C; `--satellite LS` selects Landsat 8/9.
Space-separated, comma-separated and repeated satellite values are accepted.
`--plans` reads local ESA KML/KMZ inputs; `--fetch-plans` requests retrieval.
`--offline` prevents orbital-data network access.

Use `--no-cache` for temporary downloads, or `--cache-dir` to choose a reusable
cache. `cache info` and `cache clear` inspect or remove recognised cached data.
Exit codes are 0 for success (including empty results), 2 for input errors and 1
for operational failures. See `--help` for each command and the README's cache policy.
