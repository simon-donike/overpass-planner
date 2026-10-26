"""Cache retention and CLI lifecycle tests use disposable roots, never the user's cache."""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from conftest import FIXTURES
from typer.testing import CliRunner

from eo_overpass_predictor import api
from eo_overpass_predictor.cli import app
from eo_overpass_predictor.io import cache

runner = CliRunner()
PLAN = next(FIXTURES.glob("*.kml"))


def cached_file(root, name, *, category="plans", age_days=0, size=10):
    path = root / category / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    modified = time.time() - age_days * 86400
    os.utime(path, (modified, modified))
    return path


def plan_name(index):
    return f"s2b_mp_acq__kml_202601{index:02d}t000000_20260201t000000.kml"


def test_age_size_retention_and_active_plan_protection(tmp_path):
    stale = cached_file(tmp_path, plan_name(1), age_days=31)
    oldest = cached_file(tmp_path, plan_name(2), age_days=3)
    recent = cached_file(tmp_path, plan_name(3), age_days=1)
    active = cached_file(tmp_path, plan_name(4), age_days=60)
    tle = cached_file(tmp_path, "42063.tle", category="tle", age_days=365)
    cache.prune_plan_cache(tmp_path, [active], max_bytes=20)
    assert not stale.exists() and not oldest.exists()
    assert recent.exists() and active.exists() and tle.exists()
    # Keeping an active plan takes precedence over the byte budget.
    cache.prune_plan_cache(tmp_path, [active], max_bytes=0)
    assert active.exists() and not recent.exists()


def test_cleanup_preserves_foreign_files_and_symlinks(tmp_path):
    download = cached_file(tmp_path, plan_name(1), age_days=60)
    manual = cached_file(tmp_path, "my-plan.kml", age_days=60)
    output = cached_file(tmp_path, "predictions.csv", category="tle")
    other = tmp_path / "outside.kml"
    other.write_text("user data")
    link = tmp_path / "plans" / plan_name(2)
    try:
        link.symlink_to(other)
    except OSError:
        pytest.skip("Symlink creation unavailable on this platform")
    cache.prune_plan_cache(tmp_path)
    assert not download.exists()
    assert manual.exists() and output.exists() and other.exists() and link.is_symlink()
    assert cache.clear_cache(tmp_path) == (0, 0)
    # A directory symlink must not redirect a cache clear to an external archive.
    external = tmp_path / "external"
    protected = cached_file(external, "42063.tle", category="tle")
    root = tmp_path / "root"
    root.mkdir()
    (root / "tle").symlink_to(external / "tle", target_is_directory=True)
    assert cache.clear_cache(root) == (0, 0)
    assert protected.exists()


def test_abandoned_temporary_writes_expire_but_recent_writes_survive(tmp_path):
    old = cached_file(tmp_path, ".eo-overpass-old.tmp", category="tle", age_days=2)
    active = cached_file(tmp_path, ".eo-overpass-active.tmp", category="tle")
    foreign = cached_file(tmp_path, ".another-program.tmp", category="tle", age_days=2)
    cache.prune_plan_cache(tmp_path)
    assert not old.exists() and active.exists() and foreign.exists()


def test_atomic_write_failure_preserves_previous_cache_and_removes_temporary(tmp_path, monkeypatch):
    target = cached_file(tmp_path, "42063.tle", category="tle")
    previous = target.read_bytes()

    def fail(*args):
        raise OSError("mock full disk")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="full disk"):
        cache.write_cache_file(target, b"new download")
    assert target.read_bytes() == previous
    assert list(target.parent.iterdir()) == [target]


def test_retention_failure_warns_without_losing_prediction_inputs(tmp_path, monkeypatch):
    stale = cached_file(tmp_path, plan_name(1), age_days=60)

    def fail(*args, **kwargs):
        raise PermissionError("mock locked file")

    monkeypatch.setattr(Path, "unlink", fail)
    with pytest.warns(UserWarning, match="Could not clean"):
        cache.prune_plan_cache(tmp_path)
    assert stale.exists()


def test_default_downloads_pruned_but_explicit_archives_and_offline_reads_preserved(
    tmp_path, monkeypatch
):
    root = tmp_path / "default"
    monkeypatch.setattr(cache, "user_cache_path", lambda app: root)
    stale = cached_file(root, plan_name(1), age_days=60)
    active = root / "plans" / PLAN.name
    active.write_bytes(PLAN.read_bytes())
    modified = time.time() - 60 * 86400
    os.utime(active, (modified, modified))
    # Reading locally (including CLI offline use) never runs retention.
    api.load_s2_acquisition_plans([active])
    assert stale.exists()
    explicit = tmp_path / "archive"
    archived = cached_file(explicit, plan_name(1), category="", age_days=60)

    def get(url, **kwargs):
        return SimpleNamespace(
            raise_for_status=lambda: None,
            text=f'<a href="/documents/d/sentinel/{PLAN.stem}">plan</a>',
            content=PLAN.read_bytes(),
        )

    monkeypatch.setattr(requests, "get", get)
    kwargs = dict(start_utc="2026-10-26", end_utc="2026-11-01", satellites="S2B", verbose=False)
    assert api.fetch_s2_acquisition_plans(**kwargs) == [active]
    assert not stale.exists() and active.exists()
    api.fetch_s2_acquisition_plans(plan_dir=explicit, **kwargs)
    assert archived.exists()
    assert not list(root.rglob("*.tmp")) and not list(explicit.rglob("*.tmp"))


def test_cache_info_and_clear_are_scoped_and_idempotent(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    monkeypatch.setattr(cache, "user_cache_path", lambda app: root)
    result = runner.invoke(app, ["cache", "info"])
    assert result.exit_code == 0 and str(root) in result.output
    assert "0 download" in result.output and not root.exists()
    first = cached_file(root, "42063.tle", category="tle")
    second = cached_file(root, plan_name(1))
    foreign = cached_file(root, "export.csv")
    override = tmp_path / "other-cache"
    untouched = cached_file(override, "49260.tle", category="tle")
    result = runner.invoke(app, ["cache", "clear", "--cache-dir", str(root)])
    assert result.exit_code == 0 and "Cleared 2" in result.output
    assert not first.exists() and not second.exists() and foreign.exists() and untouched.exists()
    assert "Cleared 0" in runner.invoke(app, ["cache", "clear"]).output


@pytest.mark.parametrize("command,suffix", [("predict", ".csv"), ("tracker", ".html")])
@pytest.mark.parametrize("fail", [False, True])
def test_cli_no_cache_cleans_temporary_downloads_on_success_and_failure(
    tmp_path, monkeypatch, command, suffix, fail
):
    root = tmp_path / "persistent"
    saved = cached_file(root, "42063.tle", category="tle")
    monkeypatch.setattr(cache, "user_cache_path", lambda app: root)
    original = api.fetch_tles
    observed = []

    def fetch(**kwargs):
        directory = kwargs["tle_dir"]
        observed.append(directory.parent)
        result = original(**kwargs)
        assert (directory / "42063.tle").exists()
        if fail:
            raise RuntimeError("mock calculation failure")
        return result

    monkeypatch.setattr(api, "fetch_tles", fetch)
    roi = tmp_path / "roi.geojson"
    roi.write_text(json.dumps({"type": "Point", "coordinates": [144.96, -37.82]}))
    output = tmp_path / f"result{suffix}"
    result = runner.invoke(
        app,
        [
            command,
            "--roi",
            str(roi),
            "--start",
            "2026-10-26",
            "--end",
            "2026-10-27",
            "--satellite",
            "S2B",
            "--tle-file",
            str(FIXTURES / "satellites.tle"),
            "--offline",
            "--no-cache",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == (1 if fail else 0), (result.output, result.exception)
    assert observed and all(not path.exists() for path in observed)
    assert saved.exists() and saved.read_bytes() == b"x" * 10
    assert output.exists() is not fail


def test_no_cache_and_directory_override_conflict(tmp_path):
    with pytest.raises(ValueError, match="--cache-dir"):
        with cache.cache_session(tmp_path, no_cache=True):
            pytest.fail("Mutually exclusive options were accepted")


def test_tle_cache_keeps_five_stable_filenames(tmp_path):
    text = (FIXTURES / "satellites.tle").read_text()
    for _ in range(2):
        api.fetch_tles(tle_dir=tmp_path, tle_text=text, offline=True, verbose=False)
    assert len(list(tmp_path.iterdir())) == 5
    assert all(path.suffix == ".tle" for path in tmp_path.iterdir())


def test_cli_temporary_plan_files_survive_until_export(tmp_path, monkeypatch):
    roots = []

    def fetch_plans(**kwargs):
        directory = kwargs["plan_dir"]
        roots.append(directory.parent)
        directory.mkdir()
        downloaded = directory / PLAN.name
        downloaded.write_bytes(PLAN.read_bytes())
        return [downloaded]

    monkeypatch.setattr(api, "fetch_s2_acquisition_plans", fetch_plans)
    roi = tmp_path / "roi.geojson"
    roi.write_text(json.dumps({"type": "Point", "coordinates": [144.96, -37.82]}))
    output = tmp_path / "checked.csv"
    args = [
        "predict",
        "--roi",
        str(roi),
        "--start",
        "2026-10-26",
        "--end",
        "2026-10-27",
        "--satellite",
        "S2B",
        "--tle-file",
        str(FIXTURES / "satellites.tle"),
        "--no-cache",
        "--fetch-plans",
        "--output",
        str(output),
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, (result.output, result.exception)
    assert output.exists() and "planned (NOMINAL)" in output.read_text()
    assert roots and all(not root.exists() for root in roots)
    conflict = runner.invoke(app, [*args, "--cache-dir", str(tmp_path / "cache")])
    assert conflict.exit_code == 2 and "cannot be used with --cache-dir" in conflict.output


def test_cli_offline_temporary_mode_needs_supplied_tles(tmp_path, monkeypatch):
    root = tmp_path / "persistent"
    monkeypatch.setattr(cache, "user_cache_path", lambda app: root)
    api.fetch_tles(
        satellites="S2B",
        tle_text=(FIXTURES / "satellites.tle").read_text(),
        offline=True,
        verbose=False,
    )
    roi = tmp_path / "roi.geojson"
    roi.write_text(json.dumps({"type": "Point", "coordinates": [144.96, -37.82]}))
    output = tmp_path / "absent.csv"
    result = runner.invoke(
        app,
        [
            "predict",
            "--roi",
            str(roi),
            "--start",
            "2026-10-26",
            "--end",
            "2026-10-27",
            "--satellite",
            "S2B",
            "--offline",
            "--no-cache",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 1 and "No cached or supplied TLE" in result.output
    assert not output.exists() and (root / "tle" / "42063.tle").exists()
