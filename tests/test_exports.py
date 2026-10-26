import json
import re

import geopandas as gpd
import pandas as pd
import pytest

from eo_overpass_predictor import (
    export_live_tracker_html,
    export_overpass_csv,
    export_overpass_footprints,
    load_roi,
)


@pytest.mark.parametrize("case", ["polygon", "empty"])
def test_csv_export_and_empty_schema(results, tmp_path, case):
    path = export_overpass_csv(results[case], tmp_path, file_name="report.csv")
    report = pd.read_csv(path)
    assert len(report) == len(results[case])
    assert {"local_date", "local_time", "local_weekday", "utc_offset"} <= set(report)
    assert "geometry" not in report
    if not report.empty:
        assert report["overpass_utc"].str.endswith("Z").all()
        assert report["overpass_local"].str.endswith("+11:00").all()


@pytest.mark.parametrize("suffix", [".geojson", ".gpkg"])
@pytest.mark.parametrize("case", ["polygon", "empty"])
def test_footprint_export_round_trip(results, tmp_path, suffix, case):
    path = export_overpass_footprints(results[case], tmp_path / f"footprints{suffix}")
    restored = gpd.read_file(path)
    assert restored.crs.to_epsg() == 4326
    assert len(restored) == len(results[case])
    if len(restored):
        assert restored.geometry.iloc[0].hausdorff_distance(results[case].geometry.iloc[0]) < 1e-9
        assert restored["roi_coverage_pct"].tolist() == results[case]["roi_coverage_pct"].tolist()


def test_tracker_contains_resources_and_structured_payload(results, tles, tmp_path):
    path = export_live_tracker_html(
        tles,
        load_roi((144.96, -37.82)),
        tmp_path / "tracker.html",
        passes=results["polygon"],
        title="A </title><script>bad</script>",
    )
    html = path.read_text()
    assert html.startswith("<!doctype html>")
    assert "/*__PAYLOAD__*/null" not in html
    assert "/*__LAND__*/null" not in html
    assert "<title>A </title>" not in html
    payload = json.loads(re.search(r"const P = (.*);", html).group(1))
    assert len(payload["sats"]) == 5
    assert len(payload["passes"]) == len(results["polygon"])
    assert payload["roi"]["features"][0]["geometry"]["type"] == "Point"
    land = json.loads(re.search(r"const LAND = (.*);", html).group(1))
    assert land


@pytest.mark.visualization
def test_optional_visualizations_and_empty_plot(results, tmp_path):
    pytest.importorskip("folium")
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    from eo_overpass_predictor import (
        plot_overpass_timeline,
        visualise_overpass_footprints,
        visualise_roi_on_map,
    )

    area = load_roi((144.96, -37.82))
    assert visualise_roi_on_map(area) is not None
    assert visualise_overpass_footprints(results["polygon"], area) is not None
    assert visualise_overpass_footprints(results["empty"], area) is not None
    import matplotlib.pyplot as plt

    for case in ["polygon", "empty"]:
        path = tmp_path / f"{case}.png"
        figure = plot_overpass_timeline(results[case], save_path=path)
        assert path.stat().st_size > 0
        plt.close(figure)


@pytest.mark.visualization
def test_live_widget_lifecycle(tles, results):
    # Construction and explicit async lifecycle work without rendering a browser map.
    pytest.importorskip("ipyleaflet")
    import asyncio

    from eo_overpass_predictor import live_tracker

    async def exercise():
        tracker = live_tracker(
            tles,
            load_roi((144.96, -37.82)),
            passes=results["polygon"],
            autostart=False,
            satellites=["S2B"],
        )
        assert not tracker._running
        tracker.start()
        await asyncio.sleep(0)
        assert tracker._running
        tracker.stop()
        assert tracker._task is None
        tracker.map.close()
        tracker.widget.close()

    asyncio.run(exercise())
