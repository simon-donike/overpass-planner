# Working in this repository

The installable Python package is under `src/eo_overpass_predictor`. The notebooks
are clients of that package. Preserve existing workspace changes and keep the
scientific implementation attributed to Julian Manning.

## Development checks

Use `uv sync --all-extras` to prepare the environment. After relevant changes run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Tests use frozen orbital elements and mocked network responses. Do not make the
suite depend on live services. Notebook tests require local kernel/socket access.
For packaging changes also run `uv build`, `uv run twine check dist/*`, and
`uv run python tests/validate_distribution.py dist/*.whl`.

## Scientific invariants

- Preserve SGP4 propagation, projection/swath formulas, sampling conventions,
  filters and rounding unless the task explicitly requests scientific changes.
- Keep `predict_overpasses` free of downloads and file writes. Put retrieval and
  export in the I/O adapters; optional presentation dependencies stay optional.
- Regression references establish parity, not independent observational accuracy.
  Do not regenerate baselines merely to accept changed results.
- Geometric intersection, expected imaging, ESA plan evidence and available
  imagery are separate facts. A missing ESA plan means unknown acquisition.
  Landsat acquisition is not checked. Non-nominal plans may not produce imagery.
- Freshness labels are reminders based on distance from each pass epoch (7/14
  days), not accuracy probabilities. Retain swath-edge and geographic limitations.
- Use explicit historical dates with the frozen test TLEs. Never present those
  fixtures as current elements or imagery observations.

## Interfaces for automation

`eo-overpass next` accepts exactly one of `--point LON LAT`, `--bbox WEST SOUTH
EAST NORTH`, or `--roi FILE`. It defaults to now plus 14 days and needs no output
file. All leaf commands accept command-level `--json`; see `docs/cli.md` and
`docs/cli-output.schema.json` for the versioned response contract.

Keep stdout to one JSON response in JSON mode, including expected input and
operational errors. Send progress and warnings to stderr. Preserve UTC ISO-8601
and explicit local offsets, null missing values, empty-success results and exit
codes (0 success, 2 input error, 1 operational failure). Additional JSON fields may
be added compatibly; changing field meanings/removing fields requires a schema
version change. Human labels can evolve; machine status codes are the contract.

The HTML tracker uses `resources/live_tracker_template.html`, with payloads built
in `io/export.py` and status labels shared in `presentation.py`. Verify live and
selected-pass modes, filters, keyboard controls, expired windows, empty states,
and narrow layouts when changing the planner. Exported reports are snapshots;
filters cannot recover passes excluded at prediction time.
