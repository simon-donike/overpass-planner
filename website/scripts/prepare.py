"""Prepare self-hosted browser dependencies; never rewrite the Python package."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
ROOT = SITE.parent
PUBLIC = SITE / "public"


def download(url: str, target: Path, digest: str | None = None) -> None:
    if target.exists() and (digest is None or sha256(target.read_bytes()) == digest):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Download {target.name}", flush=True)
    request = urllib.request.Request(url, headers={"User-Agent": "eo-overpass-browser-build/1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    if digest and sha256(data) != digest:
        raise ValueError(f"Checksum mismatch: {target.name}")
    target.write_bytes(data)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def upstream_artifact(spec: dict, source: bool = False) -> Path:
    cache = SITE / ".cache"
    metadata = cache / f"{spec['name']}-{spec['version']}.json"
    download(f"https://pypi.org/pypi/{spec['name']}/{spec['version']}/json", metadata)
    candidates = json.loads(metadata.read_text())["urls"]
    for item in candidates:
        valid = (
            item["packagetype"] == "sdist" if source else item["filename"].endswith("none-any.whl")
        )
        if valid and (not spec.get("sha256") or item["digests"]["sha256"] == spec["sha256"]):
            destination = cache / item["filename"]
            download(item["url"], destination, item["digests"]["sha256"])
            return destination
    raise ValueError(f"No pinned artifact found for {spec['name']}")


def source_wheel(spec: dict, destination: Path) -> Path:
    """Package unmodified Python sources with the upstream optional accelerator absent.

    timezonefinder documents its pure-Python fallback. Preserve PKG-INFO metadata,
    Python modules, schemas and licences; supply a correctly tagged wheel/RECORD.
    """
    archive = upstream_artifact(spec, source=True)
    name = spec["name"].replace("-", "_")
    info = f"{name}-{spec['version']}.dist-info"
    entries: dict[str, bytes] = {}
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            parts = Path(member.name).parts[1:]
            if not member.isfile() or not parts:
                continue
            relative = "/".join(parts)
            if parts[0] == name or relative == "PKG-INFO" or relative.startswith("LICENSE"):
                stream = source.extractfile(member)
                assert stream is not None
                key = f"{info}/METADATA" if relative == "PKG-INFO" else relative
                if relative.startswith("LICENSE"):
                    key = f"{info}/licenses/{relative}"
                entries[key] = stream.read()
    entries[f"{info}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: eo-overpass-browser\nRoot-Is-Purelib: true\n"
        b"Tag: py3-none-any\n"
    )
    records = io.StringIO()
    writer = csv.writer(records, lineterminator="\n")
    for key, data in sorted(entries.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow([key, f"sha256={digest}", len(data)])
    writer.writerow([f"{info}/RECORD", "", ""])
    entries[f"{info}/RECORD"] = records.getvalue().encode()
    target = destination / f"{name}-{spec['version']}-py3-none-any.whl"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as wheel:
        for key, data in sorted(entries.items()):
            item = zipfile.ZipInfo(key, (2026, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            wheel.writestr(item, data)
    return target


def prepare(test_data: bool = False) -> None:
    manifest = json.loads((SITE / "python-dependencies.json").read_text())
    runtime = PUBLIC / "runtime"
    wheels = PUBLIC / "wheels"
    runtime.mkdir(parents=True, exist_ok=True)
    wheels.mkdir(parents=True, exist_ok=True)
    base = f"https://cdn.jsdelivr.net/pyodide/v{manifest['pyodide']}/full/"
    for file_name, digest in manifest["runtime_files"].items():
        download(base + file_name, runtime / file_name, digest)
    lock = json.loads((runtime / "pyodide-lock.json").read_text())
    seen: set[str] = set()

    def package(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        entry = lock["packages"][name]
        for dependency in entry["depends"]:
            package(dependency)
        download(base + entry["file_name"], runtime / entry["file_name"], entry["sha256"])

    for name in manifest["packages"]:
        package(name)
    from simplify_timezones import simplified_wheel

    # Never retain obsolete oversized wheels or split assets from earlier builds.
    for previous in wheels.glob("timezonefinder_data-*"):
        previous.unlink()
    artifacts = []
    for spec in manifest["wheels"]:
        source = upstream_artifact(spec)
        target = wheels / source.name
        if spec["name"] == "timezonefinder-data":
            source = simplified_wheel(source, manifest["timezone_dataset"], download)
            target = wheels / source.name
        shutil.copyfile(source, target)
        artifacts.append(target)
    for spec in manifest["source_wheels"]:
        artifacts.append(source_wheel(spec, wheels))
    package_dir = SITE / ".cache" / "package"
    package_dir.mkdir(parents=True, exist_ok=True)
    for previous in package_dir.glob("eo_overpass_predictor-*.whl"):
        previous.unlink()
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(package_dir)], cwd=ROOT, check=True)
    wheel = next(package_dir.glob("eo_overpass_predictor-*.whl"))
    for previous in wheels.glob("eo_overpass_predictor-*.whl"):
        previous.unlink()
    target = wheels / wheel.name
    shutil.copyfile(wheel, target)
    artifacts.append(target)
    with zipfile.ZipFile(wheel) as archive:
        metadata_path = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_path).decode()
        version = next(
            line.split(": ", 1)[1] for line in metadata.splitlines() if line.startswith("Version: ")
        )
    (PUBLIC / "build.json").write_text(
        json.dumps(
            {
                "pyodide": manifest["pyodide"],
                "package_version": version,
                "packages": manifest["packages"],
                "wheels": [
                    {"url": f"wheels/{p.name}", "sha256": sha256(p.read_bytes())} for p in artifacts
                ],
            },
            indent=2,
        )
        + "\n"
    )
    shutil.copyfile(SITE / "python" / "browser_adapter.py", PUBLIC / "browser_adapter.py")
    shutil.copyfile(ROOT / "docs" / "assets" / "eo_overpass_logo.png", PUBLIC / "logo.png")
    from eo_overpass_predictor import SATELLITE_CATALOGUE

    (PUBLIC / "catalogue.json").write_text(json.dumps(SATELLITE_CATALOGUE, indent=2) + "\n")
    if test_data:
        test_root = PUBLIC / "test-data"
        test_root.mkdir(exist_ok=True)
        for name in ["satellites.tle", "sentinel1.tle", "baseline.json"]:
            shutil.copyfile(ROOT / "tests" / "fixtures" / name, test_root / name)
    print("Browser assets ready", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-data", action="store_true")
    prepare(parser.parse_args().test_data)
