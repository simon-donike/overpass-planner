"""Local bounded parallel execution with the unchanged scientific engine."""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

import pandas as pd

from .design import iso, load_run, write_json
from .pipeline import collect_manifest, predict_manifest


@contextmanager
def coarse_propagation_cache():
    """Reuse identical large propagations across ROIs in ONE fixed TLE snapshot.

    Arrays are computed by the original engine and reused bit for bit. Fine
    propagation, geometry, sampling, filters and rounding remain unchanged.
    A snapshot must not contain different element sets for the same satellite.
    """
    from eo_overpass_predictor.core import prediction as engine

    original = engine._propagate
    cache: dict = {}

    def propagate(satellite, start, offsets):
        if len(offsets) < 10_000:
            return original(satellite, start, offsets)
        key = (
            tuple(
                getattr(satellite.model, field)
                for field in (
                    "satnum",
                    "jdsatepoch",
                    "jdsatepochF",
                    "inclo",
                    "nodeo",
                    "ecco",
                    "argpo",
                    "mo",
                    "no_kozai",
                    "bstar",
                    "ndot",
                    "nddot",
                )
            ),
            str(start),
            hashlib.sha256(offsets.tobytes()).digest(),
        )
        if key not in cache:
            if len(cache) >= 2:
                cache.clear()
            result = original(satellite, start, offsets)
            for array in result:
                array.setflags(write=False)
            cache[key] = result
        return cache[key]

    engine._propagate = propagate
    try:
        yield
    finally:
        engine._propagate = original


def _predict_snapshot(args):
    directory, manifest, study, snapshot = args
    selected = {**manifest, "snapshots": [snapshot]}
    with coarse_propagation_cache():
        return predict_manifest(directory, selected, study)


def _collect_site_window(args):
    directory, manifest, study, site, origin = args
    selected = {
        **manifest,
        "sites": [site],
        "snapshots": [s for s in manifest["snapshots"] if s["origin"] == origin],
    }
    return collect_manifest(directory, selected, study)


def execute(directory: Path, predict_workers: int = 8, collect_workers: int = 4) -> dict:
    """Save progress throughout; finish with evaluation and uncertainty artifacts."""
    from .analysis import analyze
    from .pipeline import evaluate

    manifest, study = load_run(directory)
    started = time.monotonic()
    status = {
        "study_id": manifest["study_id"],
        "pid": os.getpid(),
        "status": "running",
        "started_at_utc": iso(pd.Timestamp.now(tz="UTC")),
        "predict_workers": predict_workers,
        "collect_workers": collect_workers,
        "stages": {},
        "thread_limits": {
            k: os.environ.get(k) for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS")
        },
    }
    progress_lock = Lock()

    def progress(stage, completed, total):
        with progress_lock:
            status["stages"][stage] = {"completed": completed, "total": total}
            status.update(
                stage="predict_and_collect" if stage in {"predict", "collect"} else stage,
                updated_at_utc=iso(pd.Timestamp.now(tz="UTC")),
                elapsed_s=time.monotonic() - started,
            )
            write_json(directory / "progress.json", status)
            print(
                f"{stage}: {completed}/{total} jobs; {status['elapsed_s']:.1f} s",
                file=sys.stderr,
                flush=True,
            )

    def prediction_stage():
        snapshots = manifest["snapshots"]
        progress("predict", 0, len(snapshots))
        with ProcessPoolExecutor(
            max_workers=predict_workers, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            jobs = [
                pool.submit(_predict_snapshot, (directory, manifest, study, snapshot))
                for snapshot in snapshots
            ]
            for i, job in enumerate(as_completed(jobs), 1):
                job.result()
                progress("predict", i, len(jobs))

    def collection_stage():
        collection_args = [
            (directory, manifest, study, site, origin)
            for origin in study.origins
            for site in manifest["sites"]
        ]
        progress("collect", 0, len(collection_args))
        with ThreadPoolExecutor(max_workers=collect_workers) as collection_pool:
            jobs = [collection_pool.submit(_collect_site_window, args) for args in collection_args]
            for i, job in enumerate(as_completed(jobs), 1):
                job.result()
                progress("collect", i, len(jobs))

    try:
        with ThreadPoolExecutor(max_workers=2) as stages:
            running = [stages.submit(prediction_stage), stages.submit(collection_stage)]
            for job in as_completed(running):
                job.result()
        progress("evaluate", 0, 1)
        evaluate(directory)
        progress("analyze", 0, 1)
        report = analyze(directory)
        status.update(
            status="complete",
            n_groups=len(report["groups"]),
            n_validated_candidates=sum(
                c["status"] != "withheld" for c in report["uncertainty_candidates"]
            ),
        )
        progress("complete", 1, 1)
    except BaseException as exc:
        status.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        write_json(directory / "progress.json", status)
        raise
    return status
