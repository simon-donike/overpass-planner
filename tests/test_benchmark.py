"""Offline harness validation; synthetic observations are not benchmark results."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import requests

from benchmark import analysis, design, pipeline
from benchmark.__main__ import main
from benchmark.analysis import analyze_data, clustered_summary, weighted_quantile
from benchmark.catalog import CatalogClient, covers_point, normalize_items, query_spec
from benchmark.design import Study, cases, horizon_bin, load_archive, select_element
from benchmark.matching import discrepancy, match_case

FIXTURES = Path(__file__).parent / "fixtures"


def study(**kwargs):
    defaults = {
        "origins": ["2022-01-01T00:00:00Z", "2022-04-01T00:00:00Z"],
        "satellites": ["LANDSAT 8"],
        "bootstrap_replicates": 100,
    }
    return Study(**(defaults | kwargs)).validate()


def case():
    return {
        "case_id": "case",
        "origin": "2022-01-01T00:00:00Z",
        "satellite": "LANDSAT 8",
        "site_id": "s1",
        "longitude": 0.0,
        "latitude": 0.0,
        "spatial_block": "a",
        "surface": "land",
        "latitude_band": "tropical",
        "sampling_weight": 1.0,
    }


def prediction(time="2022-01-02T12:00:00Z", **kwargs):
    return {
        "overpass_utc": time,
        "direction": "Descending",
        "imaging_expected": True,
        "edge_margin_km": 30.0,
        "initial_tle_age_days": 0.5,
        **kwargs,
    }


def item(identifier="a", **props):
    return {
        "type": "Feature",
        "id": identifier,
        "collection": "landsat-c2l1",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]],
        },
        "properties": {
            "platform": "landsat-8",
            "datetime": "2022-01-02T12:00:05Z",
            "sat:orbit_state": "descending",
            **props,
        },
        "assets": {"red": {"href": "https://example.test/red.tif", "roles": ["data"]}},
    }


def evidence(items=None):
    return {"status": "complete", "events": normalize_items(items or [item()], case(), study())}


def archive_file(tmp_path):
    tle = design.parse_tle_text((FIXTURES / "satellites.tle").read_text())[39084]
    record = {
        "satellite": "LANDSAT 8",
        "line1": tle["line1"],
        "line2": tle["line2"],
        "published_at_utc": "2026-10-26T00:00:00Z",
        "retrieved_at_utc": "2026-10-26T10:06:51Z",
        "source": "offline test fixture",
        "publication_evidence": "synthetic publication metadata, harness test only",
    }
    path = tmp_path / "archive.jsonl"
    path.write_text(json.dumps(record) + "\n")
    return path


def test_configuration_and_horizon_boundaries():
    config = study()
    assert horizon_bin(config, 0) == "0-1d"
    assert horizon_bin(config, 1) == "1-3d"
    assert horizon_bin(config, 60) == "45-60d"
    with pytest.raises(ValueError, match="outside"):
        horizon_bin(config, 60.1)
    with pytest.raises(ValueError, match="UTC offset"):
        study(origins=["2022-01-01", "2022-04-01"])
    with pytest.raises(ValueError, match="non-overlapping"):
        study(origins=["2022-01-01T00:00:00Z", "2022-02-01T00:00:00Z"])
    with pytest.raises(ValueError, match="horizon"):
        study(horizon_days=61)
    assert Study(**design.read_json(Path("benchmark/study.example.json"))).validate()


def test_sampling_equal_area_reproducible(monkeypatch):
    config = study(latitude_cells=3, longitude_cells=4, sites_per_cell=2)
    first, second = design.sample_sites(config), design.sample_sites(config)
    assert first == second
    assert len(first) == 24
    assert len({s["spatial_block"] for s in first}) == 12
    assert {s["surface"] for s in first} == {"land", "ocean"}
    for s in first:
        a, b = map(int, s["spatial_block"].split("-"))
        assert -1 + 2 * a / 3 <= np.sin(np.radians(s["latitude"])) <= -1 + 2 * (a + 1) / 3
        assert -180 + 360 * b / 4 <= s["longitude"] <= -180 + 360 * (b + 1) / 4
    assert first != design.sample_sites(replace(config, seed=config.seed + 1))


def test_historical_selection_uses_publication_and_epoch(tmp_path):
    records = load_archive(archive_file(tmp_path))
    original = records[0]
    assert select_element(records, "LANDSAT 8", "2026-10-26T01:00:00Z", 3) == original
    future_published = {**original, "published_at_utc": "2026-10-26T12:00:00Z"}
    assert select_element([future_published], "LANDSAT 8", "2026-10-26T01:00:00Z", 3) is None
    future_epoch = {**original, "epoch_utc": "2026-10-27T00:00:00Z"}
    assert select_element([future_epoch], "LANDSAT 8", "2026-10-26T01:00:00Z", 3) is None
    assert select_element(records, "LANDSAT 8", "2026-11-26T00:00:00Z", 3) is None
    assert select_element(records, "LANDSAT 9", "2026-10-26T01:00:00Z", 3) is None


def test_archive_requires_evidence_and_matching_identifier(tmp_path):
    path = archive_file(tmp_path)
    record = json.loads(path.read_text())
    record["publication_evidence"] = ""
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="evidence"):
        load_archive(path)
    record["publication_evidence"] = "test"
    record["satellite"] = "LANDSAT 9"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="identifiers"):
        load_archive(path)


def test_dateline_footprint_and_holes():
    polygon = {
        "type": "Polygon",
        "coordinates": [[[179, -1], [-179, -1], [-179, 1], [179, 1], [179, -1]]],
    }
    assert covers_point(polygon, -179.5, 0)
    assert covers_point(polygon, 179.5, 0)
    assert not covers_point(polygon, 0, 0)
    polygon["coordinates"].append(
        [[179.2, -0.5], [179.8, -0.5], [179.8, 0.5], [179.2, 0.5], [179.2, -0.5]]
    )
    assert not covers_point(polygon, 179.5, 0)
    with pytest.raises(ValueError, match="polygon"):
        covers_point({"type": "Point", "coordinates": [0, 0]}, 0, 0)


def test_normalization_deduplicates_tiles_without_cloud_filtering():
    a = item("a", **{"eo:cloud_cover": 100})
    b = item("b", datetime="2022-01-02T12:00:15Z")
    wrong_satellite = item("other", platform="landsat-9")
    events = normalize_items([a, a, b, wrong_satellite], case(), study())
    assert len(events) == 1
    assert [i["id"] for i in events[0]["items"]] == ["a", "b"]
    assert events[0]["items"][0]["cloud_cover_pct"] == 100
    assert events[0]["time_basis"] == "padded_product_timestamp_proxy"
    later_orbit = item("c", **{"sat:absolute_orbit": 2})
    a["properties"]["sat:absolute_orbit"] = 1
    assert len(normalize_items([a, later_orbit], case(), study())) == 2


def test_sensing_intervals_and_missing_platform_are_not_silently_dropped():
    a = item(start_datetime="2022-01-02T12:00:00Z", end_datetime="2022-01-02T12:00:10Z")
    events = normalize_items([a], case(), study())
    assert events[0]["start_utc"] == "2022-01-02T12:00:00Z"
    assert events[0]["time_basis"] == "product_sensing_interval"
    del a["properties"]["platform"]
    with pytest.raises(ValueError, match="platform"):
        normalize_items([a], case(), study())


def test_sentinel1_mode_platform_and_direction():
    radar_case = {**case(), "satellite": "SENTINEL-1C"}
    a = item(
        platform="sentinel-1c", **{"sar:instrument_mode": "EW", "sat:orbit_state": "ascending"}
    )
    assert normalize_items([a], radar_case, study()) == []
    events = normalize_items([a], radar_case, replace(study(), s1_mode="EW"))
    assert events[0]["direction"] == "ascending"
    del a["properties"]["sar:instrument_mode"]
    with pytest.raises(ValueError, match="mode"):
        normalize_items([a], radar_case, study())


def test_matching_interval_sign_unique_pair_and_misses():
    predictions = [prediction(), prediction("2022-01-04T12:00:00Z")]
    result = match_case(case(), predictions, evidence(), study())
    a, b = result["predictions"]
    assert a["status"] == "matched"
    assert a["residual_lower_s"] == -25
    assert a["residual_upper_s"] == 35
    assert a["interval_distance_s"] == 0
    assert a["absolute_error_upper_s"] == 35
    assert a["midpoint_residual_s"] == 5
    assert b["status"] == "not_catalogued_within_gate"
    shifted = discrepancy(prediction("2022-01-02T11:59:00Z"), evidence()["events"][0])
    assert shifted["residual_lower_s"] == 35
    assert shifted["interval_distance_s"] == 35
    assert result["observations"][0]["matched_imaging_expected"]
    observed_miss = match_case(case(), [], evidence(), study())
    assert observed_miss["observations"][0]["status"] == "unpredicted"


def test_query_guard_includes_proxy_interval_matches_at_window_boundary():
    # This item instant is outside the 30 min gate, but its sensing proxy is
    # inside it. Searching only +/- the matching gate would miss this evidence.
    a = item(datetime="2021-12-31T23:29:45Z")
    events = {"status": "complete", "events": normalize_items([a], case(), study())}
    result = match_case(case(), [prediction(case()["origin"])], events, study())
    assert result["predictions"][0]["status"] == "matched"
    _, body = query_spec(case(), study())
    start, end = map(design.utc, body["datetime"].split("/"))
    assert start <= design.utc(a["properties"]["datetime"]) <= end


def test_ambiguity_and_catalog_failure_are_unknown():
    result = match_case(
        case(), [prediction(), prediction("2022-01-02T12:01:00Z")], evidence(), study()
    )
    assert {r["status"] for r in result["predictions"]} == {"ambiguous"}
    assert result["observations"][0]["status"] == "ambiguous"
    failed = {"status": "unknown", "events": evidence()["events"]}
    result = match_case(case(), [prediction()], failed, study())
    assert result["predictions"][0]["status"] == "unknown"
    assert result["observations"] == []
    opposite = prediction(direction="Ascending")
    assert (
        match_case(case(), [opposite], evidence(), study())["predictions"][0]["status"]
        == "not_catalogued_within_gate"
    )


class Response:
    def __init__(self, body=None, status=200, content_type="image/tiff"):
        self.body, self.status_code = body, status
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, size):
        assert size == 1
        yield b"x"


def page(features, links=None, **kwargs):
    return {"type": "FeatureCollection", "features": features, "links": links or [], **kwargs}


def test_catalog_pagination_preserves_post_body_and_get_link(monkeypatch):
    client = CatalogClient(0)
    requests_seen = []
    responses = iter(
        [
            Response(
                page(
                    [item("a")],
                    [
                        {
                            "rel": "next",
                            "href": "/search",
                            "method": "POST",
                            "body": {"token": "abc"},
                            "merge": True,
                        }
                    ],
                    numberMatched=3,
                )
            ),
            Response(page([item("b")], [{"rel": "next", "href": "/search?token=def"}])),
            Response(page([item("c")])),
        ]
    )

    def request(method, url, **kwargs):
        requests_seen.append((method, url, kwargs))
        return next(responses)

    monkeypatch.setattr(client, "request", request)
    result = client.search("https://example.test", {"collections": ["test"]})
    assert result["status"] == "complete"
    assert len(result["features"]) == 3
    assert requests_seen[1][2]["json"] == {"collections": ["test"], "token": "abc"}
    assert requests_seen[2][0] == "GET"
    client.close()


@pytest.mark.parametrize(
    "body",
    [
        page([item()], numberMatched=2),
        page([], [{"rel": "next", "href": "https://other.test/search"}]),
        page([], [{"rel": "next", "href": "/search", "method": "POST"}]),
        {"message": "bad response"},
    ],
)
def test_incomplete_or_invalid_search_never_becomes_absence(monkeypatch, body):
    client = CatalogClient(0)
    monkeypatch.setattr(client, "request", lambda *a, **k: Response(body))
    result = client.search("https://example.test", {})
    assert result["status"] == "unknown"
    assert result["error"]
    client.close()


@pytest.mark.parametrize(
    ("status", "mime", "expected"),
    [
        (206, "image/tiff", "verified"),
        (403, "image/tiff", "access_denied"),
        (200, "text/html", "unknown"),
    ],
)
def test_asset_range_probes_close_stream_and_keep_denial_unknown(
    monkeypatch, status, mime, expected
):
    client = CatalogClient(0)
    response = Response(status=status, content_type=mime)

    def request(method, url, **kwargs):
        assert kwargs["headers"] == {"Range": "bytes=0-0"}
        assert kwargs["stream"]
        return response

    monkeypatch.setattr(client, "request", request)
    assert client.probe({"href": "https://example.test/data.tif"})["status"] == expected
    assert response.closed
    assert client.probe({"href": "s3://bucket/data.tif"})["status"] == "unsupported_uri"
    client.close()


def analysis_row(origin, block, error, status="matched"):
    return {
        **prediction(),
        "satellite": "LANDSAT 8",
        "horizon_bin": "1-3d",
        "surface": "land",
        "latitude_band": "tropical",
        "edge_band": "interior",
        "initial_tle_age_band": "0-1d",
        "origin": origin,
        "spatial_block": block,
        "sampling_weight": 1.0,
        "status": status,
        "asset_status": "unknown",
        "absolute_error_upper_s": error,
        "midpoint_residual_s": error,
        "interval_distance_s": error,
        "time_basis": "synthetic_reference",
    }


def test_two_way_bootstrap_does_not_gain_precision_from_duplicated_pass_rows():
    config = study()
    rows = [
        analysis_row(o, b, i + j, "matched" if i == j else "not_catalogued_within_gate")
        for i, o in enumerate(config.origins)
        for j, b in enumerate(["a", "b"])
    ]
    first = clustered_summary(rows, config, 100)
    duplicate = clustered_summary(rows * 50, config, 100)
    assert first["estimates"] == duplicate["estimates"]
    assert first["confidence_intervals_95"] == duplicate["confidence_intervals_95"]
    assert duplicate["n_origins"] == 2
    assert duplicate["n_spatial_blocks"] == 2
    assert weighted_quantile(np.array([0.0, 5.0]), np.array([0.0, 1.0]), 0) == 5


def test_holdout_does_not_train_radius_and_sparse_evidence_withholds():
    config = study()
    data = {
        "schema_version": "1.0",
        "study_id": "synthetic",
        "cases": [],
        "observations": [],
        "predictions": [
            analysis_row(config.origins[0], "a", 5),
            analysis_row(config.origins[1], "a", 10000),
        ],
    }
    result = analyze_data(data, config)
    candidate = result["uncertainty_candidates"][0]
    assert candidate["candidate_half_width_s"] == 5
    assert candidate["holdout_interval_coverage"] == 0
    assert candidate["user_facing_half_width_s"] is None
    assert candidate["status"] == "withheld"
    data["predictions"][1]["absolute_error_upper_s"] = 50000
    assert analyze_data(data, config)["uncertainty_candidates"][0]["candidate_half_width_s"] == 5


def test_unknown_evidence_bounds_and_asset_access_are_separate():
    rows = [
        analysis_row("o", "a", 5),
        analysis_row("o", "a", 5, "unknown"),
        analysis_row("o", "a", 5, "not_catalogued_within_gate"),
    ]
    values = clustered_summary(rows, study(), 1)["estimates"]
    assert values["catalogued_match_rate"] == 0.5
    assert values["match_rate_all_unknown_fail"] == pytest.approx(1 / 3)
    assert values["match_rate_all_unknown_succeed"] == pytest.approx(2 / 3)
    assert values["asset_verified_fraction_of_matches"] == 0


def test_plan_predict_resume_evaluate_and_stale_analysis(tmp_path, monkeypatch):
    config = study(
        origins=["2026-10-26T00:00:00Z", "2027-01-15T00:00:00Z"],
        latitude_cells=1,
        longitude_cells=1,
        sites_per_cell=1,
    )
    config_path = tmp_path / "config.json"
    design.write_json(config_path, design.asdict(config))
    output = tmp_path / "run"
    manifest = design.plan(config_path, archive_file(tmp_path), output)
    assert not (output / "predictions").exists()
    assert len(list(cases(manifest))) == 2
    assert sum(s["element"] is None for s in manifest["snapshots"]) == 1
    called = []

    def fake_predict(*args, **kwargs):
        called.append(kwargs)
        assert kwargs["imaging_only"] is False
        return gpd.GeoDataFrame(
            [
                {
                    **prediction("2026-10-27T12:00:00Z"),
                    "roi_entry_utc": "2026-10-27T11:59:59Z",
                    "roi_exit_utc": "2026-10-27T12:00:01Z",
                    "tle_age_days": 2.5,
                }
            ]
        )

    monkeypatch.setattr(pipeline, "predict_overpasses", fake_predict)
    assert pipeline.predict(output) == {"processed": 2}
    assert pipeline.predict(output) == {"processed": 0}
    assert len(called) == 1
    with pytest.raises(ValueError, match="Incomplete study"):
        pipeline.evaluate(output)
    for c in cases(manifest):
        pipeline.save_checkpoint(
            output / "evidence" / f"{c['case_id']}.json",
            {"status": "complete", "events": [], "error": None},
            manifest["study_id"],
        )
    evaluation = pipeline.evaluate(output)
    assert len(evaluation["cases"]) == 2
    assert len(evaluation["predictions"]) == 1
    assert evaluation["predictions"][0]["status"] == "not_catalogued_within_gate"
    report = analysis.analyze(output)
    assert report["production_integration"] is False
    assert (output / "report.md").exists()
    c = next(cases(manifest))
    evidence_path = output / "evidence" / f"{c['case_id']}.json"
    data = design.read_json(evidence_path)
    pipeline.save_checkpoint(evidence_path, {**data, "error": "changed"}, manifest["study_id"])
    assert list((output / "evidence/history").glob("*.json"))
    with pytest.raises(ValueError, match="changed since evaluation"):
        analysis.analyze(output)
    changed = deepcopy(manifest)
    changed["sites"][0]["longitude"] += 1
    design.write_json(output / "manifest.json", changed)
    with pytest.raises(ValueError, match="preregistration"):
        design.load_run(output)


def test_cli_requires_network_opt_in_and_no_work_on_help(capsys):
    assert main(["collect", "--run", "/does/not/exist"]) == 2
    assert "--allow-network" in json.loads(capsys.readouterr().out)["message"]
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "historical" in capsys.readouterr().out.lower()


def test_collect_resumes_shared_queries_and_preserves_failures(tmp_path, monkeypatch):
    config = study(satellites=["LANDSAT 8", "LANDSAT 9"], probe_assets=False)
    manifest = {
        "study_id": "synthetic",
        "sites": [case()],
        "snapshots": [
            {"origin": config.origins[0], "satellite": s, "element": None}
            for s in config.satellites
        ],
    }
    monkeypatch.setattr(pipeline, "load_run", lambda _: (manifest, config))
    calls = []

    class Client:
        def __init__(self, *args):
            pass

        def search(self, endpoint, body):
            calls.append(body)
            if len(calls) == 1:
                return {
                    "status": "unknown",
                    "error": "synthetic outage",
                    "features": [],
                    "pages": [],
                    "captured_at_utc": "2026-10-26T00:00:00Z",
                }
            return {
                "status": "complete",
                "error": None,
                "features": [item()],
                "pages": [],
                "captured_at_utc": "2026-10-26T00:00:00Z",
            }

        def close(self):
            pass

    monkeypatch.setattr(pipeline, "CatalogClient", Client)
    assert pipeline.collect(tmp_path) == {"processed": 2}
    assert len(calls) == 1
    for path in (tmp_path / "evidence").glob("*.json"):
        assert design.read_json(path)["status"] == "unknown"
    assert pipeline.collect(tmp_path) == {"processed": 0}
    assert pipeline.collect(tmp_path, retry_errors=True) == {"processed": 2}
    assert len(calls) == 2  # one shared, complete mission search on retry
    outputs = [design.read_json(p) for p in (tmp_path / "evidence").glob("*.json")]
    assert {v["status"] for v in outputs} == {"complete"}
    assert sorted(len(v["events"]) for v in outputs) == [0, 1]
    assert len(list((tmp_path / "evidence/history").glob("*.json"))) == 2


def test_collect_immature_windows_never_queries_network(tmp_path, monkeypatch):
    config = study(origins=["2100-01-01T00:00:00Z", "2100-04-01T00:00:00Z"])
    manifest = {
        "study_id": "synthetic",
        "sites": [case()],
        "snapshots": [{"origin": config.origins[0], "satellite": "LANDSAT 8", "element": None}],
    }
    monkeypatch.setattr(pipeline, "load_run", lambda _: (manifest, config))
    assert pipeline.collect(tmp_path) == {"processed": 1}
    output = design.read_json(next((tmp_path / "evidence").glob("*.json")))
    assert output["status"] == "unknown"
    assert output["error"] == "right_censored_immature_window"
    assert not (tmp_path / "catalog").exists()


def test_supported_candidate_still_requires_non_degenerate_holdout_coverage():
    config = study(
        origins=[
            t.isoformat() for t in pd.date_range("2020-01-01", periods=8, freq="100D", tz="UTC")
        ],
        min_calibration_origins=2,
        min_holdout_origins=2,
        min_spatial_blocks=2,
        min_matches=10,
    )
    rows = []
    for i, origin in enumerate(config.origins):
        for block in range(4):
            for j in range(100):
                error = j + 1 if i < 5 else (120 if j < block + 2 else 5)
                rows.append(analysis_row(origin, str(block), error))
    data = {
        "schema_version": "1.0",
        "study_id": "synthetic",
        "cases": [],
        "observations": [],
        "predictions": rows,
    }
    candidate = analyze_data(data, config)["uncertainty_candidates"][0]
    assert candidate["status"] == "validated_research_candidate"
    assert candidate["user_facing_half_width_s"] == 91
    assert candidate["holdout_interval_coverage_ci95"][0] >= 0.85
    data["cases"] = [
        {
            "satellite": "LANDSAT 8",
            "surface": "land",
            "prediction_status": "missing_historical_tle",
            "catalog_status": "complete",
        }
    ]
    candidate = analyze_data(data, config)["uncertainty_candidates"][0]
    assert candidate["status"] == "withheld"
    assert "incomplete_cases_in_satellite_surface_domain" in candidate["reasons"]
    data["cases"] = []
    for row in rows:
        if row["origin"] in config.origins[-3:]:
            row["absolute_error_upper_s"] = 5
    candidate = analyze_data(data, config)["uncertainty_candidates"][0]
    assert "degenerate_heldout_coverage_bootstrap" in candidate["reasons"]
    assert candidate["user_facing_half_width_s"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"platform": "unknown"},
        {"start_datetime": "2022-01-02T12:00:00Z"},
        {"sat:orbit_state": 123},
    ],
)
def test_unknown_metadata_never_silently_becomes_negative(changes):
    with pytest.raises(ValueError):
        normalize_items([item(**changes)], case(), study())


def test_explicit_stale_element_cutoff_preserves_forecast_date(tmp_path):
    records = load_archive(archive_file(tmp_path))
    recent = records[0]
    older = {
        **recent,
        "epoch_utc": "2026-10-20T00:00:00Z",
        "published_at_utc": "2026-10-20T12:00:00Z",
        "element_id": "older",
    }
    assert (
        select_element(
            [recent, older], "LANDSAT 8", "2026-10-26T01:00:00Z", 10, "2026-10-23T01:00:00Z"
        )
        == older
    )
    assert (
        select_element(
            [recent, older], "LANDSAT 8", "2026-10-26T01:00:00Z", 3, "2026-10-23T01:00:00Z"
        )
        is None
    )
    with pytest.raises(ValueError, match="cutoff lags"):
        study(element_cutoff_lags_days=[0, 7], max_initial_tle_age_days=3)
    manifest = {
        "study_id": "test",
        "sites": [case()],
        "snapshots": [
            {
                "satellite": "LANDSAT 8",
                "origin": case()["origin"],
                "element": None,
                "element_cutoff_lag_days": lag,
            }
            for lag in [0, 3, 14]
        ],
    }
    assert len({c["case_id"] for c in cases(manifest)}) == 3


def test_degenerate_product_interval_is_a_padded_proxy():
    instant = "2022-01-02T12:00:05Z"
    event = normalize_items([item(start_datetime=instant, end_datetime=instant)], case(), study())[
        0
    ]
    assert event["time_basis"] == "padded_degenerate_product_interval_proxy"
    assert (design.utc(event["end_utc"]) - design.utc(event["start_utc"])).total_seconds() == 60


def test_iau_import_retains_availability_evidence_and_rejects_corruption(tmp_path):
    import hashlib

    from benchmark.archive import convert_iau_archive

    element = load_archive(archive_file(tmp_path))[0]
    row = {
        "satellite_id": 39084,
        "date_collected": "2026-10-26 00:00:00 UTC",
        "tle_line1": element["line1"],
        "tle_line2": element["line2"],
    }
    corrupted = {
        **row,
        "tle_line2": row["tle_line2"][:-1] + str((int(row["tle_line2"][-1]) + 1) % 10),
    }
    path = tmp_path / "39084.json"
    design.write_json(path, {"count": 2, "data": [row, corrupted]})
    design.write_json(
        path.with_suffix(".provenance.json"),
        {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "url": "https://example.test/archive",
            "retrieved_at_utc": "2026-10-26T10:00:00Z",
        },
    )
    output = tmp_path / "validated.jsonl"
    result = convert_iau_archive(tmp_path, output)
    assert result["accepted_records"] == result["rejected_records"] == 1
    imported = load_archive(output)[0]
    assert imported["availability_time_basis"] == "archive_observed_by_utc"
    assert imported["published_at_utc"] == "2026-10-26T00:00:00Z"
    assert imported["line1"] == element["line1"]
    assert "sha256=" in imported["publication_evidence"]


def test_coarse_cache_reuses_exact_arrays_and_restores_engine(monkeypatch, tles):
    from benchmark.execution import coarse_propagation_cache
    from eo_overpass_predictor.core import prediction as engine
    from eo_overpass_predictor.core.orbits import _build_satellite

    satellite = _build_satellite("LANDSAT 8", tles["LANDSAT 8"])
    start = pd.Timestamp("2026-10-26T00:00:00Z")
    offsets = np.arange(10001) * 30.0
    original = engine._propagate
    expected = original(satellite, start, offsets)
    calls = []

    def counted(*args):
        calls.append(len(args[2]))
        return original(*args)

    monkeypatch.setattr(engine, "_propagate", counted)
    with coarse_propagation_cache():
        first = engine._propagate(satellite, start, offsets)
        second = engine._propagate(satellite, start, offsets.copy())
        for reference, actual, again in zip(expected, first, second):
            np.testing.assert_array_equal(reference, actual)
            assert actual is again
            assert not actual.flags.writeable
        engine._propagate(satellite, start, offsets[:10])
        engine._propagate(satellite, start, offsets[:10])
    assert engine._propagate is counted
    assert calls == [10001, 10, 10]


def test_repeated_access_denials_do_not_become_file_absence(monkeypatch):
    monkeypatch.setattr(CatalogClient, "_access_denials", {})
    client = CatalogClient(0, True)
    calls = []

    def request(*args, **kwargs):
        calls.append(args)
        return Response(status=401)

    monkeypatch.setattr(client, "request", request)
    first = client.probe({"href": "https://example.test/a.tif"})
    second = client.probe({"href": "https://example.test/b.tif"})
    assert first["status"] == "access_denied"
    assert second["status"] == "not_checked_after_access_denial"
    assert second["prior_challenge"]["http_status"] == 401
    assert len(calls) == 1
    client.close()


def test_calibration_deduplicates_identical_orbital_inputs_across_age_cohorts():
    config = study()
    rows = []
    for i, origin in enumerate(config.origins):
        for lag in [0, 1]:
            rows.append(
                {
                    **analysis_row(origin, "a", 5),
                    "site_id": "same_site",
                    "element_id": f"different_archive_record_{lag}",
                    "orbital_input_id": f"same_orbit_{i}",
                    "element_cutoff_lag_days": lag,
                }
            )
    data = {
        "schema_version": "1.0",
        "study_id": "synthetic",
        "predictions": rows,
        "observations": [],
        "cases": [],
    }
    report = analyze_data(data, config)
    assert report["n_repeated_prediction_rows_deduplicated_for_calibration"] == 2
    assert report["groups"][0]["calibration"]["n_predictions"] == 1


def test_parallel_driver_finishes_stages_and_saves_progress(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from benchmark import execution

    config = study()
    manifest = {
        "study_id": "synthetic",
        "sites": [case()],
        "snapshots": [{"origin": o} for o in config.origins],
    }
    monkeypatch.setattr(execution, "load_run", lambda _: (manifest, config))
    monkeypatch.setattr(
        execution,
        "ProcessPoolExecutor",
        lambda **kwargs: ThreadPoolExecutor(max_workers=kwargs["max_workers"]),
    )
    predicted = []
    collected = []
    monkeypatch.setattr(execution, "_predict_snapshot", lambda args: predicted.append(args[-1]))
    monkeypatch.setattr(execution, "_collect_site_window", lambda args: collected.append(args[-1]))
    monkeypatch.setattr(pipeline, "evaluate", lambda _: None)
    monkeypatch.setattr(analysis, "analyze", lambda _: {"groups": [], "uncertainty_candidates": []})
    result = execution.execute(tmp_path, 2, 2)
    assert result["status"] == "complete"
    assert len(predicted) == len(collected) == 2
    saved = design.read_json(tmp_path / "progress.json")
    assert saved["stages"]["predict"] == {"completed": 2, "total": 2}
    assert saved["stages"]["collect"] == {"completed": 2, "total": 2}


def test_land_tags_cover_continents_in_unwrapped_drawing_mask(monkeypatch):
    points = [(-3.7, 40.4), (20.0, 0.0), (80.0, 25.0), (130.0, -24.0), (-145.0, 0.0)]
    values = iter(
        value
        for lon, lat in points
        for value in ((lon + 180) / 360, (np.sin(np.radians(lat)) + 1) / 2)
    )

    class Rng:
        def random(self):
            return next(values)

    monkeypatch.setattr(design.np.random, "default_rng", lambda _: Rng())
    sites = design.sample_sites(
        study(latitude_cells=1, longitude_cells=1, sites_per_cell=len(points))
    )
    assert [s["surface"] for s in sites] == ["land", "land", "land", "land", "ocean"]
