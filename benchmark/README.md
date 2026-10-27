# Historical prediction and imagery benchmark

This is an opt-in research harness, not a change to production predictions or
uncertainty labels. Example designs and executed study configurations are separate;
saved local executions live in `benchmark/runs/`. The offline tests
use synthetic catalog observations and existing frozen orbital fixtures solely
to test the harness. The propagation and swath implementation remains Julian
Manning's, called through `predict_overpasses` without scientific changes.

The completed IAU study's [uncertainty plots](../docs/uncertainty.md) show the
dependence on initial TLE age and forecast lead, with passing and withheld
research candidates marked separately. The documentation includes a compact
result snapshot and an offline figure generator; full run evidence stays local.

## Questions and estimands

For each simulated issue date, freeze elements that were available at that date
and propagate once through 60 days without updating them. Measure separately:

1. **Catalogued match probability** among expected-imaging predictions, within a
   declared temporal gate, for a stated platform and product level. This combines
   acquisition policy, coverage, orbit error and catalog completeness. It is not
   a probability of orbital accuracy.
2. **Timing discrepancy conditional on a unique match.** Record the difference
   between prediction and the product sensing interval, including signed bounds,
   midpoint discrepancy, distance to the interval, and the largest absolute bound.
   Signed discrepancies are observed time minus predicted time: positive is late.
3. **File accessibility:** whether a range request reads a byte from at least one
   data asset. S3 URIs, authentication failures and unverified assets are unknown,
   not absent. This does not establish full-product integrity or usable ROI pixels.
4. **Recall:** which observed acquisition events lacked a geometric or an
   expected-imaging prediction. Query whole windows, not just predicted passes,
   to expose missed passes and avoid verification bias.

An absent catalog product is **not** a measured timing error. Clouds are retained;
cloud metadata are saved but do not imply cloud-free ROI imagery. ESA historical
plan evidence is not inferred: `plan_status` remains `unknown`. In particular,
Landsat acquisition is not checked by the production predictor. Operational
campaigns and non-nominal acquisition policies can change availability.

**Important limit:** scene/granule timestamps are not necessarily sensing times
at the target point. STAC sensing intervals are retained when present. Otherwise
an instantaneous timestamp is padded by a preregistered mission-specific amount
(30 s Landsat, 300 s Sentinel-2, 60 s Sentinel-1). Those padding assumptions are
explicit proxies, not measured error bars. A reported half-width encloses the
**product-time proxy interval**, conditional on matching; it must not be marketed
as a second-level estimate of actual ROI timing accuracy. Exact timing/position
validation needs independent per-pixel sensing times or precise orbital reference
products and a separate validation study. No conversion to kilometres is made.

## Preregistered design

`study.example.json` specifies 22 issue dates 75 days apart, February 2022 through
May 2026, and 288 point sites across 72 equal-area spatial cells. It creates
25,344 site/date/platform cases before input availability is known. It is a
substantial experiment; the planning command reports workload without propagating
or querying imagery. Predictions use the unchanged 30 s coarse / 1 s fine sampling.

- Draw longitude uniformly and sine(latitude) uniformly within each cell. Keep
  the seed, points and equal sampling weights in the immutable manifest. This
  targets a uniform-area global point population, including ocean and polar
  regions, rather than population centres or only areas routinely acquired.
- Use the bundled coarse land mask to tag land/ocean. This is an approximate
  geographic stratum, especially at coastlines and lakes, not an acquisition mask.
  Preserve tropical/temperate/polar latitude and the predicted 5 km edge stratum.
  Results for points do not validate large polygons, terrain or precise swath edges.
- Require issue windows plus both matching guards to be disjoint. Issue dates are
  the temporal blocks. Repeated sites are deliberate paired comparisons across
  horizon bins. Do not pretend their passes are independent trials.
- Bin forecast lead into `[0,1), [1,3), [3,7), [7,14), [14,30), [30,45), [45,60]`
  days. Also retain continuous lead, initial TLE age and pass-epoch TLE age. These
  are different quantities; production 7/14-day freshness reminders stay intact.
  Scoring uses `[issue, issue + horizon)` to avoid boundary duplication; the
  production engine's sampling and endpoint behavior are unchanged.
- The example uses Landsat 8/9 and Sentinel-2A/B. Use separate explicitly dated
  studies for Sentinel-2C or Sentinel-1C/D over their operational histories; do not
  fabricate pre-launch TLEs. Sentinel-1 GRD supports the configured IW/EW mode,
  both directions and night passes. Missing elements remain counted as missing
  cases. No current elements or regression fixtures fill historical gaps.
- Do not select issue dates or locations based on observed imagery. Keep seasonal
  and mission-regime differences visible; a result spanning an old mission regime
  is not automatically applicable to current operations. A frozen issue-date
  snapshot cannot anticipate future orbit manoeuvres.

The example is a starting design, not a promise of adequate power in every stratum.
Its 15 calibration and 7 test issue dates can still leave sparse strata, especially
ocean, polar regions, swath edges and short lead bins. Increasing sites improves
spatial support but does not create more independent issue dates. Before collecting
data, budget for at least the declared minimum number of dates, cells and matches
in every intended use stratum. As a rough independent-trial planning calculation,
`1.96² p(1-p)/e²` needs about 385 at p=0.5, e=0.05, or 139 at p=0.9; **these are independent
units, not raw pass counts**, and clustering increases requirements. Precision of
tail quantiles usually needs substantially more support. Never lower thresholds
after inspecting test results merely to get a published uncertainty number.

## Historical orbital input

Supply a private JSONL archive: one object per element set, including multiple
sets per platform. Each line has:

```json
{"satellite":"LANDSAT 8","line1":"<original checksum-valid line>","line2":"<original checksum-valid line>","published_at_utc":"2022-01-31T18:00:00Z","retrieved_at_utc":"2026-10-26T00:00:00Z","source":"<archive URL or provider record identifier>","publication_evidence":"<raw record/path and hash documenting its publication timestamp>"}
```

This is a schema illustration, not usable orbital data. Preserve the original
provider responses and their hashes alongside your private inputs. Publication
evidence is supplied provenance; the harness cannot authenticate its truth.
An epoch is not evidence of publication. Space-Track GP_HISTORY's `CREATION_DATE`
is a possible publication-time source. Follow its terms/rate limits; download and
cache history once. This harness deliberately does not automate authenticated
archive retrieval or invent publication dates from bare TLE files.

For each issue date/platform, select the newest epoch from the checksum-valid
sets **published no later than the issue date**, with epoch no later than issue
and initial age at most three days. Break ties by publication time and record hash.
Future-published sets with historical epochs are rejected for that issue date.
Retrieval today is acceptable for a documented historical publication; it cannot
establish that a bare archived element was available then. The chosen lines,
publication evidence, epochs and IDs are embedded in the manifest. The input file,
code, land mask and dependency lockfile are hashed. Changing the design/code
requires a new study directory. Run one writer per study directory.

## Matching, completeness and services

Default product levels are USGS `landsat-c2l1`, CDSE `sentinel-2-l1c`, and CDSE
`sentinel-1-grd`. Prefer primary catalogs and basic product levels to avoid mixing
atmospheric processing availability with acquisition. Only these selected
collections are evaluated; absence does not rule out another processing level.
Collection schemas and archive coverage need a small reviewed pilot before a
large future run. Platform names, polygons and time fields are validated; relevant
unknown metadata makes the whole case unknown rather than silently dropping it.

Fetch each point's whole issue window plus a guard on both sides equal to the
matching gate plus the maximum accepted 40 minute event span (70 minutes by default),
so padded/product intervals just outside the nominal gate are not lost at boundaries,
following GET/POST pagination, merge bodies and relative links. Save raw pages,
requests, capture times and hashes; cache shared mission queries across platforms.
Retries, bounded pagination, rate limiting and resumable case checkpoints are built
in. HTTP failures, truncated/cyclic pagination, invalid metadata and incomplete
windows never count as negative evidence. A 30 day post-window maturity requirement
limits right censoring; it cannot prove an archive is complete. Catalogues queried
today establish eventual presence at capture time, **not historical delivery latency**.

Test the actual point against each product polygon, with longitude unwrapping at
the dateline. Collapse reprocessings/tiles and close intervals for the same
platform, compatible absolute orbit/direction and mode into one local event.
Keep all product IDs and clouds/asset metadata. An event must be no longer than
40 minutes. An observed event belongs to a forecast window by interval midpoint;
guard events can still match a prediction at the window edge.

Match only same-platform, compatible-direction events within 30 minutes of the
sensing interval. Require exactly one candidate in **both** directions; do not
force nearest-neighbour assignments in ambiguous cases. Ambiguities are unknown.
Keep unmatched predictions and observations. Evaluate matching sensitivity at
one quarter, one half, and the full gate (7.5, 15 and 30 minutes by default), using
the same data. A finite gate truncates identifiable errors: conditional timing
quantiles cannot describe missing or larger-error passes. Gate sensitivity counts
are diagnostic; they do not select a better-looking gate after observing results.

Asset checks use HTTPS range GETs, stream at most a byte and close immediately;
never fall back to a full image download. Check up to three assets marked `data`.
S3/authenticated endpoints can require an additional credential-aware verifier.
Access-denied, unsupported and missing data assets remain unknown and are retained
in evidence. File accessibility may change after the recorded check time.

## Statistics and uncertainty artifacts

Prespecify groups by platform, horizon, surface, latitude band, edge band and
initial TLE age (`[0,1)`, `[1,3)`, `[3,7)`, `[7,14)`, `[14,30)`, `[30,60)`, or 60+ days). Artifacts also record the actual
age range observed in each group; a maximum inclusion age does not establish
support throughout that range.
Use only expected-imaging rows for calibration, retaining all geometric rows for
inspection. Use the first 70% of issue dates to estimate the 90th percentile of
the conservative absolute proxy-interval discrepancy. Freeze that symmetric
half-width before evaluating the last 30% of dates. This estimates a prediction
interval conditional on a unique catalog match; it is not a confidence interval
for a mean. There is no row-random split or test-set parameter selection.

Bootstrap whole issue dates and whole spatial cells independently, multiplying
their resampling counts (crossed/pigeonhole bootstrap). Report point estimates
and percentile 95% confidence intervals for match rates and timing summaries;
report recall CIs separately. This accounts for shared variation along both axes
under a crossed-block approximation. It is **not** a finite-sample conformal
guarantee, and assumptions are stronger for tail quantiles than for means. Check
temporal dependence, seasonal/regime stability and cell-size sensitivity before
deployment. Degenerate intervals (such as all matches succeeding) are flagged;
they must not be read as certainty. All intervals are pointwise, not simultaneous
across the many strata. The temporal holdout tests transfer at the sampled sites,
not performance in an unseen geographic population.

`analysis.json` includes denominators, status counts, unknown-as-failure/success
rate bounds, signed midpoint bias, p90 interval distance, p90/p95 upper proxy-error
bounds, bootstrap intervals and holdout Brier scores for the separate catalogued
match probability. That probability is descriptive; it is not automatically
approved as a calibrated user probability. Unchecked assets are not counted as
verified access. The report lists both calibration and holdout support.

`uncertainty.json` withholds its `user_facing_half_width_s` unless both splits
meet the preregistered matched-row, date, spatial-cell and unknown-evidence gates,
all cases for the satellite/surface domain are complete, and the lower bootstrap
coverage bound in the temporal holdout is at least 0.85 for a nominal 0.90 interval.
Degenerate holdout coverage bootstraps are also withheld. The raw candidate is
retained for audit even when withheld. Missing groups have no estimate; there is
no fallback to a better-sampled platform or horizon. No values are installed in
the app. A validated research candidate still requires review of the proxy timing,
deployment population, multiplicity and acquisition-policy changes before user
wording is chosen.

## Commands to run later

These commands are provided for the future study; adding the harness does not run
them. Work from the repository root after `uv sync --all-extras`:

```bash
uv run python -m benchmark plan --config benchmark/study.example.json \
  --tle-archive benchmark/inputs/history.jsonl --out benchmark/runs/global-v1

uv run python -m benchmark predict --run benchmark/runs/global-v1 --limit 10
uv run python -m benchmark collect --run benchmark/runs/global-v1 --allow-network --limit 10

# Review the pilot's raw metadata, task failures, access checks and costs first.
# Then resume the same frozen study to completion (remove --limit).
uv run python -m benchmark predict --run benchmark/runs/global-v1
uv run python -m benchmark collect --run benchmark/runs/global-v1 --allow-network
uv run python -m benchmark evaluate --run benchmark/runs/global-v1
uv run python -m benchmark analyze --run benchmark/runs/global-v1
```

Use `--retry-errors` on predict/collect to retry failed cases; previous records
are retained in `history/`. Rerun evaluate/analyze after retries. Evaluation
refuses missing checkpoint files; known failed/skipped cases remain explicit and
block uncertainty publication. Analyze refuses evidence changed since evaluation.
Successful empty results are valid checkpoints. Reports, CSV tables, JSON evidence
and private inputs stay in ignored `benchmark/runs/` and `benchmark/inputs/`.
Do not commit credentials, licensed input archives, generated results as baselines,
or any fabricated benchmark observations.

Offline harness checks: `uv run pytest tests/test_benchmark.py`. No catalog or
historical study runs in CI. The benchmark stays repository tooling, outside the
installable package and its production CLI.

## IAU archive study and TLE recency cohorts

`study.iau-age-v1.json` uses 17 mature issue windows, 288 global point sites and
four optical satellites. Five snapshots per issue/platform freeze elements at
issue time or 1, 3, 7 or 14 days earlier: 97,920 prediction cases. Their actual
TLE ages, including publication/collection lag, are recorded at both issue and
pass time. The maximum initial age is 30 days; this is an inclusion limit, not
a claim of support at every age. The first 11 issue dates calibrate; the last six
validate. The 12 × 24 grid provides 288 spatial blocks at the same total number
of sites. Spatial independence remains a bootstrap approximation.

Dates were selected using orbital-input availability alone, before collecting
imagery for these sample points: beginning 22 September 2023, choose the first
archive collection event at least 61 days after the previous issue, plus one
second, where all four newest elements are younger than one day and all older
cohorts have eligible elements. Stop at the last date with a mature full window.
This targets forecasts issued when fresh elements are available; it does not
estimate the frequency of TLE-service outages. Original 2022 example dates cannot
be validated as-of using this archive, whose collection evidence begins in 2023.

The IAU CPS SatChecker `date_collected` value establishes an element was observed
by that time. The importer stores this conservative availability upper bound in
the historical cutoff field with `availability_time_basis=archive_observed_by_utc`;
it does not assert the provider's exact publication time. Raw responses, request
URLs, retrieval times, hashes and rejected-row logs are retained. Corrupted lines
are rejected without checksum repair or orbital reconstruction. Backfilled old
epochs do not qualify as available before their collection dates.

```bash
uv run python -m benchmark import-iau --input benchmark/inputs/iau-history-v1 \
  --out benchmark/inputs/iau-history-v1/study-history.jsonl
uv run python -m benchmark plan --config benchmark/study.iau-age-v1.json \
  --tle-archive benchmark/inputs/iau-history-v1/study-history.jsonl \
  --out benchmark/runs/historical-20261026-age-v2
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python -m benchmark run \
  --run benchmark/runs/historical-20261026-age-v2 --allow-network \
  --predict-workers 10 --collect-workers 4
```

`run` executes prediction and collection concurrently, saves `progress.json`, then
writes evaluation, CSV, analysis, uncertainty and Markdown report artifacts. Each
worker owns disjoint snapshots or site/window queries. The coarse propagation
cache reuses the engine's exact arrays for identical orbital inputs; geometry
and refinement remain unchanged. Duplicate orbital-input forecasts across nominal
age cohorts are removed before calibration; recall is reported per cutoff cohort.
Catalog/asset evidence is shared across cohorts of the same site/platform/window.
Zero-duration product intervals receive timestamp padding, since they do not
establish exact ROI sensing times.

After an unauthenticated asset request receives a 401/403 or a login redirect,
optional access-denial backoff records subsequent same-host probes as
`not_checked_after_access_denial`, with the original challenge as provenance.
Those files stay unknown. This avoids repeating requests that need unavailable
credentials and never marks protected files missing or verified. HTTPS data
assets are prioritized ahead of unsupported S3 URIs.

## Primary references

- [Space-Track documentation](https://www.space-track.org/documentation): GP_HISTORY,
  publication metadata and archive retrieval policy.
- [IAU CPS SatChecker orbital data access](https://satchecker.readthedocs.io/en/latest/tools_data_access.html):
  original element lines and archive collection timestamps.
- [CDSE STAC API](https://documentation.dataspace.copernicus.eu/APIs/STAC.html):
  collection names and STAC search interface.
- [USGS Landsat STAC](https://www.usgs.gov/landsat-missions/spatiotemporal-asset-catalog-stac):
  Landsat product collections and catalog API.
- [STAC Item specification](https://github.com/radiantearth/stac-spec/blob/master/item-spec/item-spec.md):
  temporal and asset metadata semantics.
- [Owen and Eckles, 2012](https://arxiv.org/abs/1106.2125): product-weight bootstrap
  for crossed arrays; the implementation applies that dependence structure as an
  approximation, not a theorem guaranteeing these timing quantiles.
