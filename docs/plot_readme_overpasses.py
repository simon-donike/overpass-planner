"""Generate the README showcase offline using Julian Manning's prediction engine.

Run with: uv run python docs/plot_readme_overpasses.py
The frozen October 2026 elements are demonstration inputs, never current data.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon

from eo_overpass_predictor import fetch_tles, load_roi, predict_overpasses

ROOT = Path(__file__).resolve().parents[1]
BG, PANEL, TEXT, MUTED = "#0b1220", "#111d30", "#eef4ff", "#a5b6cd"
COLORS = {
    "LANDSAT 8": "#edb175",
    "LANDSAT 9": "#f1d39c",
    "SENTINEL-2A": "#79afff",
    "SENTINEL-2B": "#65d5c4",
    "SENTINEL-2C": "#b5a1fa",
}


def main() -> None:
    roi = load_roi(ROOT / "examples/data/cabauw_netherlands.geojson")
    with tempfile.TemporaryDirectory() as cache:
        elements = fetch_tles(
            satellites=["LS", "S2"],
            tle_text=(ROOT / "tests/fixtures/satellites.tle").read_text(),
            tle_dir=cache,
            offline=True,
            verbose=False,
        )
        passes = predict_overpasses(
            roi,
            elements,
            start_utc="2026-10-26",
            days=7,
            timezone_name="Europe/Amsterdam",
            verbose=False,
        )
    if passes.empty:
        raise RuntimeError("The frozen showcase window unexpectedly has no passes.")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig = plt.figure(figsize=(16, 8.4), facecolor=BG)
    grid = fig.add_gridspec(1, 2, left=0.06, right=0.96, bottom=0.20, top=0.72, wspace=0.30)
    map_ax, time_ax = fig.add_subplot(grid[0]), fig.add_subplot(grid[1])
    for ax in (map_ax, time_ax):
        ax.set_facecolor(PANEL)
        ax.tick_params(colors=MUTED, length=0, pad=10)
        for spine in ax.spines.values():
            spine.set_visible(False)
    fig.text(0.06, 0.93, "OVERPASS / OBSERVATION PLANNING", color="#79afff", size=12)
    fig.text(0.06, 0.865, "Predicted overpasses at Cabauw", color=TEXT, size=30, weight="bold")
    fig.text(
        0.06,
        0.81,
        "Cabauw BSRN station, Netherlands   /   26 October – 1 November 2026   /   Historical demo",
        color=MUTED,
        size=12,
    )

    land = json.loads(
        (ROOT / "src/eo_overpass_predictor/resources/world_land_50m.json").read_text()
    )
    for ring in land["rings"]:
        original = np.array(ring).reshape(-1, 2) * land["scale"]
        # The bundled coastline rings can cross the date line in unwrapped longitudes.
        for shift in (-360, 0, 360):
            coords = original + np.array([shift, 0])
            if coords[:, 0].max() < 2 or coords[:, 0].min() > 8:
                continue
            if coords[:, 1].max() < 49.5 or coords[:, 1].min() > 54.5:
                continue
            map_ax.add_patch(Polygon(coords, facecolor="#26374b", edgecolor="#52657a", lw=0.8))
    for row in passes.itertuples():
        geometries = [row.geometry] if row.geometry.geom_type == "Polygon" else row.geometry.geoms
        for polygon in geometries:
            coords = np.asarray(polygon.exterior.coords)
            map_ax.add_patch(
                Polygon(coords, facecolor=COLORS[row.satellite], alpha=0.12, edgecolor="none")
            )
            map_ax.plot(*coords.T, color=COLORS[row.satellite], alpha=0.65, lw=1.1)
    point = roi.geometry.iloc[0]
    map_ax.scatter(point.x, point.y, s=110, facecolor="white", edgecolor=BG, lw=2, zorder=5)
    map_ax.annotate(
        "CABAUW BSRN STATION",
        (point.x, point.y),
        xytext=(5.4, 50.4),
        color=TEXT,
        fontsize=10,
        arrowprops={"arrowstyle": "-", "color": TEXT},
    )
    map_ax.set(xlim=(2, 8), ylim=(49.5, 54.5))
    map_ax.set_aspect(1 / np.cos(np.deg2rad(point.y)))
    map_ax.set_xticks([3, 5, 7], ["3°E", "5°E", "7°E"])
    map_ax.set_yticks([50, 52, 54], ["50°N", "52°N", "54°N"])
    map_ax.set_title("Where the swaths cross", color=TEXT, loc="left", pad=20, size=15)

    satellites = list(COLORS)
    for y, satellite in enumerate(satellites):
        time_ax.axhline(y, color="#29394f", lw=1)
        group = passes[passes.satellite == satellite]
        for row in group.itertuples():
            local = row.overpass_local.tz_localize(None)
            time_ax.scatter(local, y, s=150, color=COLORS[satellite], zorder=4)
            time_ax.annotate(
                f"{local:%H:%M}",
                (local, y),
                xytext=(0, 14),
                textcoords="offset points",
                ha="center",
                color=TEXT,
                size=9,
                linespacing=1.6,
            )
    time_ax.set_yticks(
        range(len(satellites)),
        [s.replace("SENTINEL", "Sentinel").replace("LANDSAT", "Landsat") for s in satellites],
    )
    time_ax.set_ylim(4.7, -0.9)
    time_ax.set_xlim(mdates.datestr2num("2026-10-26"), mdates.datestr2num("2026-11-02"))
    time_ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    time_ax.xaxis.set_major_formatter(mdates.DateFormatter("%d\n%b"))
    time_ax.set_title("When to look", color=TEXT, loc="left", pad=20, size=15)
    time_ax.set_xlabel("Cabauw local time · CET (UTC+1)", color=MUTED, labelpad=15)
    fig.text(
        0.06,
        0.09,
        "Predicted swath coverage · Acquisition and imagery availability are not verified",
        color=TEXT,
        size=11,
    )
    fig.text(
        0.06,
        0.045,
        "Generated with eo-overpass-predictor using frozen 25 October 2026 TLEs. "
        "Land outlines: Natural Earth (public domain).",
        color=MUTED,
        size=9,
    )
    target = ROOT / "docs/assets/predicted_overpasses.png"
    fig.savefig(target, dpi=150, facecolor=BG)
    plt.close(fig)
    print(f"Rendered {len(passes)} predicted passes to {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
