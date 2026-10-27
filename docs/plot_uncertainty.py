"""Render documentation figures from a saved benchmark summary, without network I/O.

Default: reproduce from the committed documentation snapshot. --run refreshes
that snapshot from an existing completed analysis; it never executes a benchmark.
Julian Manning's orbital implementation and the saved statistical gates are unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

DOCS = Path(__file__).resolve().parent
DATA = DOCS / "data" / "uncertainty-20261026.csv"
PROVENANCE = DOCS / "data" / "uncertainty-20261026.provenance.json"
STUDY_ID = "22ff17e444ad53302328d99a523e5af4c1e31dca6d20585d524e7838217454f8"
SATELLITES = ["LANDSAT 8", "LANDSAT 9", "SENTINEL-2A", "SENTINEL-2B"]
AGES = ["0-1d", "1-3d", "3-7d", "7-14d", "14-30d"]
LEADS = ["0-1d", "1-3d", "3-7d", "7-14d", "14-30d", "30-45d", "45-60d"]
STRATUM = {"surface": "land", "latitude_band": "temperate", "edge_band": "interior"}
KEYS = ["satellite", "horizon_bin", "initial_tle_age_band", *STRATUM]
COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#6A51A3", "#555555"]
VALID = "validated_research_candidate"


def export_snapshot(run: Path) -> None:
    source = {name: (run / name).read_bytes() for name in ("analysis.json", "manifest.json")}
    analysis, manifest = (json.loads(source[name]) for name in source)
    if analysis["study_id"] != manifest["study_id"]:
        raise ValueError("Analysis and manifest belong to different studies.")
    if analysis["study_id"] != STUDY_ID:
        raise ValueError("These documentation figures require the recorded October 2026 study.")
    groups = {tuple(g[k] for k in KEYS): g for g in analysis["groups"]}
    rows = []
    for candidate in analysis["uncertainty_candidates"]:
        if any(candidate[k] != v for k, v in STRATUM.items()):
            continue
        group = groups[tuple(candidate[k] for k in KEYS)]
        age = candidate["applies_to"]["observed_initial_tle_age_days_range"]
        ci = candidate["holdout_interval_coverage_ci95"]
        row = {k: candidate[k] for k in KEYS}
        row.update(
            candidate_half_width_s=candidate["candidate_half_width_s"],
            user_facing_half_width_s=candidate["user_facing_half_width_s"],
            status=candidate["status"],
            withholding_reasons=";".join(candidate["reasons"]),
            observed_initial_tle_age_min_days=age[0],
            observed_initial_tle_age_max_days=age[1],
            nominal_conditional_coverage=candidate["nominal_conditional_coverage"],
            holdout_interval_coverage=candidate["holdout_interval_coverage"],
            holdout_coverage_ci95_low=ci[0] if ci is not None else None,
            holdout_coverage_ci95_high=ci[1] if ci is not None else None,
        )
        for split in ("calibration", "holdout"):
            for field in ("n_predictions", "n_matches", "n_origins", "n_spatial_blocks"):
                row[f"{split}_{field}"] = group[split][field]
        rows.append(row)
    frame = pd.DataFrame(rows).sort_values(KEYS).reset_index(drop=True)
    if frame.empty or set(frame.satellite) != set(SATELLITES):
        raise ValueError("The documentation snapshot requires all four optical platforms.")
    if frame.duplicated(KEYS).any():
        raise ValueError("Duplicate statistical strata.")
    DATA.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(DATA, index=False)
    metadata = {
        "purpose": "Documentation result snapshot; not a regression baseline or production calibration",
        "study_id": analysis["study_id"],
        "source_run": str(run),
        "source_sha256": {k: hashlib.sha256(v).hexdigest() for k, v in source.items()},
        "csv_sha256": hashlib.sha256(DATA.read_bytes()).hexdigest(),
        "scientific_attribution": manifest["scientific_attribution"],
        "stratum": STRATUM,
        "quantity": "Calibration candidate symmetric half-width enclosing the product-time proxy interval, conditional on a unique catalogue match",
        "study": manifest["study"],
        "split": analysis["split"],
        "full_study_case_counts": analysis["case_counts"],
        "full_study_groups": len(analysis["uncertainty_candidates"]),
        "full_study_validated_groups": sum(
            c["status"] == VALID for c in analysis["uncertainty_candidates"]
        ),
        "plotted_groups": len(frame),
        "plotted_validated_groups": int(frame.status.eq(VALID).sum()),
        "limitations": analysis["limitations"],
    }
    PROVENANCE.write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n")


def save_figure(fig, name: str, description: str) -> None:
    output = DOCS / "assets" / "uncertainty"
    output.mkdir(parents=True, exist_ok=True)
    fig.savefig(output / f"{name}.png", dpi=180, metadata={"Description": description})
    fig.savefig(
        output / f"{name}.svg",
        metadata={"Date": None, "Creator": "eo-overpass-predictor", "Description": description},
    )
    plt.close(fig)


def limits(frame: pd.DataFrame) -> dict[str, float]:
    return {
        mission: float(
            np.ceil(
                frame.loc[frame.satellite.str.startswith(mission), "candidate_half_width_s"].max()
                / 60
                * 1.1
            )
        )
        for mission in ("LANDSAT", "SENTINEL")
    }


def plot_slices(frame: pd.DataFrame, axis: str, level: float) -> None:
    by_lead = axis == "horizon_bin"
    x_bins, curve_bins = (LEADS, AGES) if by_lead else (AGES, LEADS)
    curve_field = "initial_tle_age_band" if by_lead else "horizon_bin"
    label = "Forecast lead" if by_lead else "TLE age at forecast issue"
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 9))
    fig.subplots_adjust(left=0.08, right=0.98, top=0.82, bottom=0.23, hspace=0.55, wspace=0.23)
    ceiling = limits(frame)
    for ax, satellite in zip(axes.flat, SATELLITES):
        subset = frame.loc[frame.satellite.eq(satellite)]
        for curve, color in zip(curve_bins, COLORS):
            points = subset.loc[subset[curve_field].eq(curve)].set_index(axis).reindex(x_bins)
            values = points.candidate_half_width_s.to_numpy(dtype=float) / 60
            x = np.arange(len(x_bins))
            ax.plot(x, values, color=color, linewidth=1.4, alpha=0.85)
            ax.scatter(x, values, facecolors="white", edgecolors=color, s=37, zorder=3)
            valid = points.status.eq(VALID).to_numpy()
            ax.scatter(
                x[valid], values[valid], c=color, edgecolors="black", linewidths=0.8, s=47, zorder=4
            )
        ax.set_title(satellite, fontweight="bold")
        ax.set_xticks(range(len(x_bins)), [x.removesuffix("d") for x in x_bins], rotation=25)
        ax.set_xlabel(f"{label} bin (days)")
        ax.set_ylabel("Candidate proxy half-width (minutes)")
        ax.set_ylim(0, ceiling["LANDSAT" if satellite.startswith("LANDSAT") else "SENTINEL"])
        ax.grid(alpha=0.2)
        ax.spines[["top", "right"]].set_visible(False)
    title = "forecast lead" if by_lead else "TLE age at forecast issue"
    fig.suptitle(f"Uncertainty candidates versus {title}", y=0.975, fontsize=17)
    fig.text(
        0.5,
        0.92,
        f"Nominal {level:.0%} product-time proxy intervals | Temperate land, swath interior (>5 km from edge)",
        ha="center",
        fontsize=10,
    )
    fig.legend(
        [Line2D([], [], color=c, linewidth=2) for c in COLORS[: len(curve_bins)]],
        curve_bins,
        title="TLE age at issue (days)" if by_lead else "Forecast lead (days)",
        loc="lower center",
        bbox_to_anchor=(0.5, 0.085),
        ncols=len(curve_bins),
        frameon=False,
    )
    fig.legend(
        [
            Line2D(
                [], [], marker="o", color="none", markerfacecolor="black", markeredgecolor="black"
            ),
            Line2D(
                [], [], marker="o", color="none", markerfacecolor="white", markeredgecolor="black"
            ),
        ],
        ["Passed held-out research checks", "Withheld"],
        loc="lower center",
        bbox_to_anchor=(0.5, 0.045),
        ncols=2,
        frameon=False,
    )
    fig.text(
        0.5,
        0.015,
        "Conditional proxy intervals; exact ROI timing and file access remain unvalidated. Mission rows use different y-scales.\nObserved initial TLE ages: 0.14–16.20 days. Lines are visual guides between categorical bins.",
        ha="center",
        fontsize=9,
    )
    save_figure(
        fig,
        "versus-forecast-lead" if by_lead else "versus-tle-age",
        "Candidate conditional proxy interval half-widths; filled circles passed the saved held-out checks and open circles are withheld. Categorical bins are connected only as visual guides.",
    )


def plot_joint(frame: pd.DataFrame, level: float) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.subplots_adjust(left=0.075, right=0.97, top=0.84, bottom=0.11, hspace=0.44, wspace=0.30)
    ceiling = limits(frame)
    for ax, satellite in zip(axes.flat, SATELLITES):
        subset = frame.loc[frame.satellite.eq(satellite)]
        grid = (
            subset.pivot(
                index="initial_tle_age_band", columns="horizon_bin", values="candidate_half_width_s"
            ).reindex(index=AGES, columns=LEADS)
            / 60
        )
        statuses = subset.pivot(
            index="initial_tle_age_band", columns="horizon_bin", values="status"
        ).reindex(index=AGES, columns=LEADS)
        maximum = ceiling["LANDSAT" if satellite.startswith("LANDSAT") else "SENTINEL"]
        im = ax.imshow(grid.to_numpy(), vmin=0, vmax=maximum, cmap="YlGnBu", aspect="auto")
        for i, j in np.ndindex(grid.shape):
            value = grid.iloc[i, j]
            valid = statuses.iloc[i, j] == VALID
            ax.add_patch(
                Rectangle(
                    (j - 0.5, i - 0.5),
                    1,
                    1,
                    fill=False,
                    edgecolor="black" if valid else "#666666",
                    linewidth=2 if valid else 0.3,
                    hatch=None if valid else "///",
                )
            )
            ax.text(
                j,
                i,
                f"{value:.1f}" if np.isfinite(value) else "—",
                ha="center",
                va="center",
                color="white" if value > maximum * 0.55 else "black",
                fontsize=10,
                fontweight="bold" if valid else "normal",
                bbox={
                    "facecolor": im.cmap(im.norm(value)) if np.isfinite(value) else "white",
                    "edgecolor": "none",
                    "pad": 1.8,
                },
            )
        ax.set_title(satellite, fontweight="bold")
        ax.set_xticks(range(len(LEADS)), [x.removesuffix("d") for x in LEADS], rotation=25)
        ax.set_yticks(range(len(AGES)), [x.removesuffix("d") for x in AGES])
        ax.set_xlabel("Forecast lead bin (days)")
        ax.set_ylabel("TLE age at issue bin (days)")
        fig.colorbar(im, ax=ax, fraction=0.035, pad=0.025, label="Half-width (minutes)")
    fig.suptitle("Joint dependence on TLE age and forecast lead", y=0.98, fontsize=18)
    fig.text(
        0.5,
        0.925,
        f"Nominal {level:.0%} product-time proxy candidates | Temperate land, swath interior (>5 km from edge)",
        ha="center",
        fontsize=10,
    )
    fig.text(
        0.5,
        0.89,
        "Bold borders: passed held-out research checks. Hatched cells: withheld. Values are minutes; mission rows use different colour scales.",
        ha="center",
        fontsize=10,
    )
    fig.text(
        0.5,
        0.025,
        "The [14, 30) day age bin contains observed ages only up to 16.2 days. Conditional proxy estimates; physical timing and file access remain unvalidated.",
        ha="center",
        fontsize=9,
    )
    save_figure(
        fig,
        "age-and-lead",
        "Calibration candidate half-widths in minutes by initial TLE age and forecast lead. Bold cells passed held-out checks; hatched cells are withheld. Colour scales differ by mission.",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        type=Path,
        help="Refresh the documentation snapshot from completed local analysis files.",
    )
    args = parser.parse_args()
    if args.run:
        export_snapshot(args.run)
    metadata = json.loads(PROVENANCE.read_text())
    if metadata["study_id"] != STUDY_ID:
        raise ValueError("Documentation snapshot belongs to another study.")
    if hashlib.sha256(DATA.read_bytes()).hexdigest() != metadata["csv_sha256"]:
        raise ValueError("Documentation snapshot differs from its recorded hash.")
    frame = pd.read_csv(DATA)
    if not frame.status.eq(VALID).equals(frame.user_facing_half_width_s.notna()):
        raise ValueError("Withheld and validated interval fields disagree.")
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "svg.fonttype": "none",
            "svg.hashsalt": metadata["study_id"],
        }
    )
    level = metadata["study"]["interval_level"]
    plot_slices(frame, "horizon_bin", level)
    plot_slices(frame, "initial_tle_age_band", level)
    plot_joint(frame, level)
    print(
        f"Saved three PNG/SVG figures from {len(frame)} recorded strata ({frame.status.eq(VALID).sum()} passed); no benchmark or network requests executed."
    )


if __name__ == "__main__":
    main()
