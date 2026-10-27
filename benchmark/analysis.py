"""Blocked uncertainty estimates and a strictly later, untouched validation set."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .design import Study, digest, load_run, read_json, write_json

GROUP_FIELDS = (
    "satellite",
    "horizon_bin",
    "surface",
    "latitude_band",
    "edge_band",
    "initial_tle_age_band",
)


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    positive = weights > 0
    values, weights = values[positive], weights[positive]
    order = np.argsort(values, kind="stable")
    cumulative = np.cumsum(weights[order])
    index = min(int(np.searchsorted(cumulative, q * cumulative[-1], side="left")), len(order) - 1)
    return float(values[order[index]])


def metrics(rows: list[dict], weights: np.ndarray, radius: float | None = None) -> dict:
    known = np.array([r["status"] in {"matched", "not_catalogued_within_gate"} for r in rows])
    matched = np.array([r["status"] == "matched" for r in rows])
    total, denominator = float(weights.sum()), float(weights[known].sum())
    success = float(weights[matched].sum())
    result = {
        "catalogued_match_rate": success / denominator if denominator else None,
        "unknown_fraction": 1 - denominator / total if total else None,
        "match_rate_all_unknown_fail": success / total if total else None,
        "match_rate_all_unknown_succeed": (success + total - denominator) / total
        if total
        else None,
        "asset_verified_fraction_of_matches": (
            sum(
                w
                for w, r in zip(weights, rows)
                if r["status"] == "matched" and r["asset_status"] == "verified"
            )
            / success
            if success
            else None
        ),
    }
    if success and all("absolute_error_upper_s" in r for r in rows if r["status"] == "matched"):
        w = weights[matched]
        errors = np.array([r["absolute_error_upper_s"] for r in rows if r["status"] == "matched"])
        midpoints = np.array([r["midpoint_residual_s"] for r in rows if r["status"] == "matched"])
        distances = np.array([r["interval_distance_s"] for r in rows if r["status"] == "matched"])
        result.update(
            median_midpoint_residual_s=weighted_quantile(midpoints, w, 0.5),
            p90_interval_distance_s=weighted_quantile(distances, w, 0.9),
            p90_absolute_error_upper_s=weighted_quantile(errors, w, 0.9),
            p95_absolute_error_upper_s=weighted_quantile(errors, w, 0.95),
        )
        if radius is not None:
            result["conditional_interval_coverage"] = float(np.average(errors <= radius, weights=w))
    return result


def clustered_summary(
    rows: list[dict], study: Study, seed: int, radius: float | None = None
) -> dict:
    """Crossed/pigeonhole bootstrap over whole origins and spatial cells.

    Resampling individual scene rows would treat nearby sites and repeated passes
    as independent. Product weights retain both sources of shared variation.
    """
    if not rows:
        return {
            "n_predictions": 0,
            "n_matches": 0,
            "n_origins": 0,
            "n_spatial_blocks": 0,
            "estimates": {},
            "confidence_intervals_95": {},
        }
    origins, origin_codes = np.unique([r["origin"] for r in rows], return_inverse=True)
    blocks, block_codes = np.unique([r["spatial_block"] for r in rows], return_inverse=True)
    weights = np.array([r["sampling_weight"] for r in rows], dtype=float)
    point = metrics(rows, weights, radius)
    samples = defaultdict(list)
    rng = np.random.default_rng(seed)
    if len(origins) >= 2 and len(blocks) >= 2:
        for _ in range(study.bootstrap_replicates):
            a = rng.multinomial(len(origins), np.full(len(origins), 1 / len(origins)))
            b = rng.multinomial(len(blocks), np.full(len(blocks), 1 / len(blocks)))
            boot_weights = weights * a[origin_codes] * b[block_codes]
            if not boot_weights.sum():
                continue
            for key, value in metrics(rows, boot_weights, radius).items():
                if value is not None and math.isfinite(value):
                    samples[key].append(value)
    intervals = {}
    for key, values in samples.items():
        # Do not publish a CI based on a few successful bootstrap draws.
        if len(values) >= 0.95 * study.bootstrap_replicates:
            intervals[key] = [float(v) for v in np.quantile(values, [0.025, 0.975])]
    return {
        "n_predictions": len(rows),
        "n_matches": sum(r["status"] == "matched" for r in rows),
        "n_origins": len(origins),
        "n_spatial_blocks": len(blocks),
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "degenerate_bootstrap_metrics": [
            key for key, interval in intervals.items() if interval[0] == interval[1]
        ],
        "time_basis_counts": dict(Counter(r.get("time_basis", "unmatched") for r in rows)),
        "estimates": point,
        "confidence_intervals_95": intervals,
    }


def support(rows: list[dict], study: Study, minimum_origins: int) -> list[str]:
    matched = [r for r in rows if r["status"] == "matched"]
    reasons = []
    if len(matched) < study.min_matches:
        reasons.append("too_few_matched_predictions")
    if len({r["origin"] for r in matched}) < minimum_origins:
        reasons.append("too_few_origins_with_matches")
    if len({r["spatial_block"] for r in matched}) < study.min_spatial_blocks:
        reasons.append("too_few_spatial_blocks_with_matches")
    unknown = sum(r["status"] not in {"matched", "not_catalogued_within_gate"} for r in rows)
    if not rows or unknown / len(rows) > study.max_unknown_fraction:
        reasons.append("too_much_unknown_evidence")
    return reasons


def analyze_data(evaluation: dict, study: Study) -> dict:
    n_test = max(1, math.ceil(len(study.origins) * study.holdout_fraction))
    calibration_origins, holdout_origins = study.origins[:-n_test], study.origins[-n_test:]
    groups = defaultdict(list)
    seen_predictions = set()
    duplicate_predictions = 0
    for row in evaluation["predictions"]:
        if all(k in row for k in ("origin", "site_id", "satellite", "element_id", "overpass_utc")):
            identity = tuple(
                row[k] for k in ("origin", "site_id", "satellite", "element_id", "overpass_utc")
            )
            if "orbital_input_id" in row:
                identity = (
                    row["origin"],
                    row["site_id"],
                    row["satellite"],
                    row["orbital_input_id"],
                    row["overpass_utc"],
                )
            if identity in seen_predictions:
                duplicate_predictions += 1
                continue
            seen_predictions.add(identity)
        if row["imaging_expected"]:
            groups[tuple(row[key] for key in GROUP_FIELDS)].append(row)
    summaries, candidates = [], []
    all_cases = evaluation["cases"]
    # Missing forecasts otherwise disappear from pass-level denominators. Any
    # missing case for this satellite/domain blocks that domain's calibration.
    incomplete_domains = {
        (c["satellite"], c["surface"])
        for c in all_cases
        if c["prediction_status"] != "complete" or c["catalog_status"] != "complete"
    }
    for key, rows in sorted(groups.items()):
        labels = dict(zip(GROUP_FIELDS, key))
        seed = study.seed + int(digest(key)[:8], 16)
        calibration = [r for r in rows if r["origin"] in calibration_origins]
        holdout = [r for r in rows if r["origin"] in holdout_origins]
        errors = [r["absolute_error_upper_s"] for r in calibration if r["status"] == "matched"]
        radius = (
            float(np.quantile(errors, study.interval_level, method="higher")) if errors else None
        )
        cal_summary = clustered_summary(calibration, study, seed, radius)
        test_summary = clustered_summary(holdout, study, seed + 1, radius)
        summaries.append({**labels, "calibration": cal_summary, "holdout": test_summary})
        reasons = [
            "calibration_" + s for s in support(calibration, study, study.min_calibration_origins)
        ]
        reasons += ["holdout_" + s for s in support(holdout, study, study.min_holdout_origins)]
        if (key[0], key[2]) in incomplete_domains:
            reasons.append("incomplete_cases_in_satellite_surface_domain")
        ci = test_summary["confidence_intervals_95"].get("conditional_interval_coverage")
        if ci is None or ci[0] < study.interval_level - study.coverage_tolerance:
            reasons.append("heldout_coverage_lower_bound_below_target")
        if "conditional_interval_coverage" in test_summary.get("degenerate_bootstrap_metrics", []):
            reasons.append("degenerate_heldout_coverage_bootstrap")
        # Binary probabilities are a separate outcome from conditional timing.
        p = cal_summary["estimates"].get("catalogued_match_rate")
        known_test = [
            r for r in holdout if r["status"] in {"matched", "not_catalogued_within_gate"}
        ]
        brier = (
            float(np.mean([(int(r["status"] == "matched") - p) ** 2 for r in known_test]))
            if known_test and p is not None
            else None
        )
        candidates.append(
            {
                **labels,
                "status": "withheld" if reasons else "validated_research_candidate",
                "reasons": reasons,
                "nominal_conditional_coverage": study.interval_level,
                "candidate_half_width_s": radius,
                "user_facing_half_width_s": None if reasons else radius,
                "calibration_catalogued_match_probability": p,
                "holdout_brier_score": brier,
                "holdout_interval_coverage": test_summary["estimates"].get(
                    "conditional_interval_coverage"
                ),
                "holdout_interval_coverage_ci95": ci,
                "meaning": "prediction ± half-width encloses the product sensing-time proxy interval, conditional on a unique catalog match",
                "applies_to": {
                    "initial_tle_age_days_design_range": [0, study.max_initial_tle_age_days],
                    "observed_initial_tle_age_days_range": [
                        min(r["initial_tle_age_days"] for r in rows),
                        max(r["initial_tle_age_days"] for r in rows),
                    ],
                    "sampling": "equal-area global point sample in the stated strata",
                    "plan_status": "unknown",
                    "s1_mode": study.s1_mode,
                },
            }
        )
    observed = evaluation["observations"]
    observed_groups = defaultdict(list)
    for row in observed:
        observed_groups[
            (
                row["satellite"],
                row["horizon_bin"],
                row["surface"],
                row.get("element_cutoff_lag_days", 0.0),
            )
        ].append(row)
    recall = []
    for key, events in sorted(observed_groups.items()):
        summary = {
            "satellite": key[0],
            "horizon_bin": key[1],
            "surface": key[2],
            "element_cutoff_lag_days": key[3],
            "n_events": len(events),
        }
        for label, expected_only in (("geometric", False), ("expected_imaging", True)):
            rate_rows = [
                {
                    **r,
                    "sampling_weight": 1.0,
                    "asset_status": "unknown",
                    "status": "unknown"
                    if r["status"] == "ambiguous"
                    else (
                        "matched"
                        if r["status"] == "matched"
                        and (r["matched_imaging_expected"] or not expected_only)
                        else "not_catalogued_within_gate"
                    ),
                }
                for r in events
            ]
            statistics = clustered_summary(
                rate_rows, study, study.seed + int(digest([key, label])[:8], 16)
            )
            summary[label + "_recall"] = statistics["estimates"].get("catalogued_match_rate")
            summary[label + "_recall_ci95"] = statistics["confidence_intervals_95"].get(
                "catalogued_match_rate"
            )
        recall.append(summary)
    return {
        "schema_version": evaluation["schema_version"],
        "study_id": evaluation["study_id"],
        "split": {"calibration_origins": calibration_origins, "holdout_origins": holdout_origins},
        "method": "prespecified strata; crossed origin/spatial-cell bootstrap; percentile 95% CIs",
        "production_integration": False,
        "case_counts": dict(
            Counter(f"{c['prediction_status']}/{c['catalog_status']}" for c in all_cases)
        ),
        "observation_counts": dict(Counter(r["status"] for r in observed)),
        "recall_by_satellite_horizon_surface": recall,
        "match_gate_sensitivity": evaluation.get("match_gate_sensitivity", []),
        "observations_without_expected_imaging_prediction": sum(
            not r["matched_imaging_expected"] for r in observed
        ),
        "n_geometric_predictions": len(evaluation["predictions"]),
        "n_repeated_prediction_rows_deduplicated_for_calibration": duplicate_predictions,
        "n_unique_observed_site_events": len(
            {(r["origin"], r["site_id"], r["satellite"], r["event_id"]) for r in observed}
        ),
        "tle_age_definitions": {
            "initial_tle_age_days": "forecast issue UTC minus TLE epoch UTC",
            "tle_age_at_pass_days": "predicted pass UTC minus TLE epoch UTC",
            "element_cutoff_lag_days": "intentional delay before forecast issue at which elements were frozen",
            "age_is_not_download_cache_age": True,
        },
        "n_expected_imaging_predictions": sum(
            r["imaging_expected"] for r in evaluation["predictions"]
        ),
        "groups": summaries,
        "uncertainty_candidates": candidates,
        "limitations": [
            "Timing is conditional on matching; unobserved acquisitions have no measured timing residual.",
            "Catalog absence within a finite gate is not proof of non-acquisition or an orbital error.",
            "Product sensing intervals/padded timestamps are proxies, not exact ROI sensing times.",
            "Asset verification establishes one readable byte in at least one data asset, not valid or cloud-free pixels.",
            "Bootstrap CIs assume separated origin blocks are sufficiently independent; they are not distribution-free guarantees.",
            "Heldout dates test temporal transfer at the sampled sites, not transfer to unseen geographic populations.",
            "Intervals are pointwise, not simultaneous across all groups; multiplicity and future regime changes remain.",
            "No estimate of position error or kilometre accuracy is made from these timestamps.",
        ],
    }


def analyze(directory: Path) -> dict:
    from .pipeline import input_fingerprint

    manifest, study = load_run(directory)
    evaluation = read_json(directory / "evaluation.json")
    if evaluation["study_id"] != manifest["study_id"]:
        raise ValueError("Evaluation belongs to another study.")
    if evaluation["input_fingerprint"] != input_fingerprint(directory):
        raise ValueError("Evidence changed since evaluation; run evaluate again.")
    report = analyze_data(evaluation, study)
    write_json(directory / "analysis.json", report)
    write_json(
        directory / "uncertainty.json",
        {
            "schema_version": report["schema_version"],
            "study_id": report["study_id"],
            "production_integration": False,
            "candidates": report["uncertainty_candidates"],
            "limitations": report["limitations"],
        },
    )
    lines = [
        "# Historical prediction benchmark",
        "",
        f"Study: `{manifest['study_id']}`",
        "",
        "Research results; no production uncertainty labels were changed.",
        "",
        f"Cases by prediction/catalog status: `{report['case_counts']}`.",
        "",
        f"Observed events: `{report['observation_counts']}`. "
        f"Without an expected-imaging match: {report['observations_without_expected_imaging_prediction']}.",
        "",
        "Half-widths below are product-time proxy discrepancies conditional on matching. "
        "Match rates concern catalog presence, and do not imply verified file access.",
        "",
        "| Satellite | Lead | Surface / latitude / edge | Initial TLE age | Cal / test matches | Cal / test match rate | Candidate half-width (s) | Test coverage | Status |",
        "|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for group, candidate in zip(report["groups"], report["uncertainty_candidates"]):
        coverage = candidate["holdout_interval_coverage"]
        coverage_label = f"{coverage:.3f}" if coverage is not None else "unknown"
        rate_labels = []
        for split in ("calibration", "holdout"):
            rate = group[split]["estimates"].get("catalogued_match_rate")
            rate_labels.append(f"{rate:.3f}" if rate is not None else "unknown")
        radius = candidate["candidate_half_width_s"]
        radius_label = f"{radius:.1f}" if radius is not None else "unknown"
        lines.append(
            f"| {group['satellite']} | {group['horizon_bin']} | {group['surface']} / {group['latitude_band']} / {group['edge_band']} | "
            f"{group['initial_tle_age_band']} | "
            f"{group['calibration']['n_matches']} / {group['holdout']['n_matches']} | "
            f"{' / '.join(rate_labels)} | {radius_label} | "
            f"{coverage_label} | {candidate['status']} |"
        )
    lines += ["", *[f"- {s}" for s in report["limitations"]], ""]
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return report
