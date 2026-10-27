"""Resumable study stages. Only collect() may access imagery services."""

from __future__ import annotations

import contextlib
import hashlib
import io
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

from eo_overpass_predictor import load_roi, predict_overpasses
from eo_overpass_predictor.types import TLERecord

from .catalog import CatalogClient, normalize_items, probe_event, query_spec
from .design import cases, digest, iso, load_run, read_json, runtime_environment, utc, write_json
from .matching import match_case


def save_checkpoint(path: Path, payload: dict, study_id: str) -> None:
    if path.exists():
        previous = read_json(path)
        write_json(path.parent / "history" / f"{path.stem}-{digest(previous)[:16]}.json", previous)
    write_json(path, {**payload, "study_id": study_id})


def checkpoint(path: Path, study_id: str) -> dict:
    value = read_json(path)
    if value["study_id"] != study_id:
        raise ValueError(f"Checkpoint belongs to another study: {path}")
    return value


def input_fingerprint(directory: Path) -> str:
    """Invalidate analyses when evidence is retried or replaced after evaluation."""
    return digest(
        {
            str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("predictions", "evidence")
            for p in sorted((directory / folder).glob("*.json"))
        }
    )


def predict(directory: Path, limit: int | None = None, retry_errors: bool = False) -> dict:
    manifest, study = load_run(directory)
    return predict_manifest(directory, manifest, study, limit, retry_errors)


def predict_manifest(
    directory: Path, manifest: dict, study, limit: int | None = None, retry_errors: bool = False
) -> dict:
    completed = 0
    for case in cases(manifest):
        path = directory / "predictions" / f"{case['case_id']}.json"
        if path.exists():
            old = checkpoint(path, manifest["study_id"])
            if not retry_errors or old["status"] != "error":
                continue
        if limit is not None and completed >= limit:
            break
        element = case["element"]
        output: dict = {"case_id": case["case_id"], "predictions": [], "error": None}
        if element is None:
            output.update(status="missing_historical_tle")
        else:
            tle: TLERecord = {
                "line1": element["line1"],
                "line2": element["line2"],
                "norad_id": element["norad_id"],
                "source": element["source"],
                "epoch_utc": utc(element["epoch_utc"]),
            }
            try:
                # The engine is used unchanged, including Julian Manning's sampling.
                # Keep geometric and expected-imaging passes as separate facts.
                with contextlib.redirect_stdout(sys.stderr):
                    frame = predict_overpasses(
                        load_roi((case["longitude"], case["latitude"])),
                        {case["satellite"]: tle},
                        satellites=[case["satellite"]],
                        start_utc=case["origin"],
                        days=study.horizon_days,
                        timezone_name="UTC",
                        imaging_only=False,
                        coarse_step_s=30.0,
                        fine_step_s=1.0,
                        s1_mode=study.s1_mode,
                        verbose=False,
                    )
                end = utc(case["origin"]) + pd.Timedelta(days=study.horizon_days)
                for _, row in frame.iterrows():
                    if not utc(case["origin"]) <= utc(row["overpass_utc"]) < end:
                        continue
                    output["predictions"].append(
                        {
                            "overpass_utc": iso(row["overpass_utc"]),
                            "roi_entry_utc": iso(row["roi_entry_utc"]),
                            "roi_exit_utc": iso(row["roi_exit_utc"]),
                            "direction": str(row["direction"]),
                            "imaging_expected": bool(row["imaging_expected"]),
                            "edge_margin_km": float(row["edge_margin_km"]),
                            "tle_epoch_utc": element["epoch_utc"],
                            "tle_age_days": float(row["tle_age_days"]),
                            "initial_tle_age_days": (
                                utc(case["origin"]) - utc(element["epoch_utc"])
                            ).total_seconds()
                            / 86400,
                            "element_id": element["element_id"],
                            "orbital_input_id": element["orbital_input_id"],
                            "plan_status": "unknown",
                        }
                    )
                output["status"] = "complete"
            except Exception as exc:
                output.update(status="error", error=f"{type(exc).__name__}: {exc}")
        save_checkpoint(path, output, manifest["study_id"])
        completed += 1
    return {"processed": completed}


def collect(directory: Path, limit: int | None = None, retry_errors: bool = False) -> dict:
    manifest, study = load_run(directory)
    return collect_manifest(directory, manifest, study, limit, retry_errors)


def collect_manifest(
    directory: Path, manifest: dict, study, limit: int | None = None, retry_errors: bool = False
) -> dict:
    client = CatalogClient(study.request_interval_s, study.skip_repeated_access_denials)
    completed = 0
    try:
        for case in cases(manifest):
            path = directory / "evidence" / f"{case['case_id']}.json"
            if path.exists():
                old = checkpoint(path, manifest["study_id"])
                if not retry_errors or old["status"] == "complete":
                    continue
            if limit is not None and completed >= limit:
                break
            evidence = {
                "case_id": case["case_id"],
                "events": [],
                "status": "unknown",
                "error": None,
            }
            endpoint, body = query_spec(case, study)
            query_id = digest([endpoint, body])
            raw_path = directory / "catalog" / f"{query_id}.json"
            evidence["query_id"] = query_id
            end = utc(body["datetime"].split("/")[1])
            if pd.Timestamp.now(tz="UTC") < end + pd.Timedelta(days=study.maturity_days):
                evidence["error"] = "right_censored_immature_window"
            else:
                raw = checkpoint(raw_path, manifest["study_id"]) if raw_path.exists() else None
                if raw is None or (retry_errors and raw["status"] != "complete"):
                    raw = client.search(endpoint, body)
                    save_checkpoint(raw_path, raw, manifest["study_id"])
                evidence["captured_at_utc"] = raw["captured_at_utc"]
                evidence["raw_sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
                if raw["status"] == "complete":
                    try:
                        event_path = (
                            directory / "events" / f"{digest([query_id, case['satellite']])}.json"
                        )
                        if event_path.exists():
                            shared = checkpoint(event_path, manifest["study_id"])
                            if shared["raw_sha256"] != evidence["raw_sha256"]:
                                raise ValueError(
                                    "Shared event evidence does not match the raw query."
                                )
                            events = shared["events"]
                        else:
                            events = normalize_items(raw["features"], case, study)
                            for event in events:
                                event["asset_check"] = (
                                    probe_event(client, event)
                                    if study.probe_assets
                                    else {"status": "not_checked", "probes": []}
                                )
                            save_checkpoint(
                                event_path,
                                {"events": events, "raw_sha256": evidence["raw_sha256"]},
                                manifest["study_id"],
                            )
                        evidence.update(status="complete", events=events)
                    except (ValueError, KeyError, TypeError) as exc:
                        evidence["error"] = f"metadata_normalization: {exc}"
                else:
                    evidence["error"] = raw["error"]
            save_checkpoint(path, evidence, manifest["study_id"])
            completed += 1
    finally:
        client.close()
    return {"processed": completed}


def evaluate(directory: Path) -> dict:
    manifest, study = load_run(directory)
    rows, observations, case_statuses = [], [], []
    sensitivity: dict[float, dict[str, int]] = {}
    for factor in (0.25, 0.5, 1.0):
        sensitivity[study.match_gate_s * factor] = {}
    for case in cases(manifest):
        p = directory / "predictions" / f"{case['case_id']}.json"
        e = directory / "evidence" / f"{case['case_id']}.json"
        if not p.exists() or not e.exists():
            raise ValueError("Incomplete study: finish predict and collect before evaluate.")
        predictions, evidence = (
            checkpoint(p, manifest["study_id"]),
            checkpoint(e, manifest["study_id"]),
        )
        if predictions["status"] == "complete":
            matched = match_case(case, predictions["predictions"], evidence, study)
            rows.extend(matched["predictions"])
            observations.extend(matched["observations"])
            for gate, counts in sensitivity.items():
                alternative = (
                    matched
                    if gate == study.match_gate_s
                    else match_case(
                        case,
                        predictions["predictions"],
                        evidence,
                        replace(study, match_gate_s=gate),
                    )
                )
                for row in alternative["predictions"]:
                    if row["imaging_expected"]:
                        key = f"{row['satellite']}/{row['horizon_bin']}/{row['status']}"
                        counts[key] = counts.get(key, 0) + 1
        case_statuses.append(
            {
                "case_id": case["case_id"],
                "origin": case["origin"],
                "satellite": case["satellite"],
                "surface": case["surface"],
                "spatial_block": case["spatial_block"],
                "prediction_status": predictions["status"],
                "catalog_status": evidence["status"],
                "prediction_error": predictions["error"],
                "catalog_error": evidence["error"],
            }
        )
    result = {
        "schema_version": manifest["schema_version"],
        "study_id": manifest["study_id"],
        "input_fingerprint": input_fingerprint(directory),
        "match_gate_sensitivity": [
            {"gate_s": gate, "counts": counts} for gate, counts in sensitivity.items()
        ],
        "predictions": rows,
        "observations": observations,
        "cases": case_statuses,
        "environment": runtime_environment(),
    }
    write_json(directory / "evaluation.json", result)
    # CSV is convenient for independent inspection; JSON remains the complete record.
    for name in ("predictions", "observations", "cases"):
        buffer = io.StringIO()
        pd.DataFrame(result[name]).to_csv(buffer, index=False)
        (directory / f"{name}.csv").write_text(buffer.getvalue(), encoding="utf-8")
    return result
