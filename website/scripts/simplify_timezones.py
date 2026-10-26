"""Build a labelled, simplified timezone database for this website only.

Retains every zone, polygon and hole; reduces vertices with GEOS coverage
simplification, then uses the pinned upstream compiler to rebuild all indexes.
Neither installed packages nor the original cached wheel are modified.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Polygon, mapping

SITE = Path(__file__).resolve().parents[1]


def simplified_wheel(original: Path, spec: dict, download) -> Path:
    source_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    key = hashlib.sha256(
        json.dumps([source_hash, spec, shapely.__version__], sort_keys=True).encode()
        + Path(__file__).read_bytes()
    ).hexdigest()[:16]
    work = SITE / ".cache" / "simplified-timezones" / key
    report_path = work / "provenance.json"
    version = original.name.split("-")[1] + "+" + spec["version_suffix"]
    target = work / f"timezonefinder_data-{version}-py3-none-any.whl"
    if target.exists() and report_path.exists():
        report = json.loads(report_path.read_text())
        if hashlib.sha256(target.read_bytes()).hexdigest() == report["wheel_sha256"]:
            return target
    work.mkdir(parents=True, exist_ok=True)
    compiler = SITE / ".cache" / "timezonefinder-9.0.0-build.tar.gz"
    download(spec["compiler_url"], compiler, spec["compiler_sha256"])
    compiler_root = work / "compiler"
    with tarfile.open(compiler) as archive:
        for member in archive.getmembers():
            relative = Path(*Path(member.name).parts[1:])
            if relative.parts and (
                relative.parts[0] == "scripts" or str(relative) in {"LICENSE", "DATA_VERSION"}
            ):
                archive.extract(member, compiler_root, filter="data")
    build_deps = SITE / ".cache" / "build-deps"
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(build_deps),
            *spec["build_dependencies"],
        ],
        check=True,
    )
    sys.path.insert(0, str(build_deps))
    sys.path.insert(0, str(compiler_root / "timezonefinder-9.0.0"))
    from scripts.file_converter import compile_data_files
    from scripts.helper_classes import GeoJSON
    from scripts.shortcuts import compile_shortcuts
    from scripts.timezone_data import TimezoneData
    from timezonefinder import TimezoneFinder
    from timezonefinder._data_integrity import validate_data_dir

    source_dir = work / "original"
    with zipfile.ZipFile(original) as archive:
        archive.extractall(source_dir)
    finder = TimezoneFinder(bin_file_location=source_dir / "timezonefinder_data/data")
    polygons, zone_ids = [], []
    for zone_id in range(finder.nr_of_zones):
        for rings in finder.get_geometry(tz_id=zone_id, use_id=True, coords_as_pairs=True):
            polygons.append(Polygon(rings[0], rings[1:]))
            zone_ids.append(zone_id)
    tolerance = spec["tolerance_degrees"]
    reduced = shapely.coverage_simplify(polygons, tolerance)
    if not np.all(shapely.is_valid(reduced)) or np.any(shapely.is_empty(reduced)):
        raise ValueError("Timezone simplification produced invalid or empty geometry")
    if sum(len(p.interiors) for p in polygons) != sum(len(p.interiors) for p in reduced):
        raise ValueError("Timezone simplification removed holes")
    before = int(sum(shapely.get_num_coordinates(polygons)))
    after = int(sum(shapely.get_num_coordinates(reduced)))
    print(f"Timezone vertices: {before:,} → {after:,}", flush=True)
    features = []
    for zone_id, name in enumerate(finder.timezone_names):
        geometry = MultiPolygon([p for i, p in zip(zone_ids, reduced) if i == zone_id])
        features.append(
            {"type": "Feature", "properties": {"tzid": name}, "geometry": mapping(geometry)}
        )

    # Existing vertices are retained, rather than newly interpolated coordinates.
    # Round the decoded floats back to the upstream six-decimal source grid.
    def source_precision(value):
        if isinstance(value, float):
            return round(value, 6)
        if isinstance(value, (tuple, list)):
            return [source_precision(item) for item in value]
        if isinstance(value, dict):
            return {key: source_precision(item) for key, item in value.items()}
        return value

    geojson = GeoJSON.model_validate(
        source_precision({"type": "FeatureCollection", "features": features})
    )
    data = TimezoneData.from_geojson(geojson)
    output = work / "data"
    output.mkdir(exist_ok=True)
    compile_data_files(data, output, finder.data_version + f"-overpass-simplified-{tolerance}deg")
    compile_shortcuts(output, data)
    validate_data_dir(output)
    compact = TimezoneFinder(bin_file_location=output)
    # A deterministic global sample measures changed timezone assignments. It is
    # a diagnostic, not an accuracy probability or a claim of exhaustive parity.
    generator = np.random.default_rng(20261026)
    changed = 0
    samples = 2000
    for lng, lat in zip(generator.uniform(-180, 180, samples), generator.uniform(-89, 89, samples)):
        changed += finder.timezone_at(lng=lng, lat=lat) != compact.timezone_at(lng=lng, lat=lat)
    report = {
        "source": "https://pypi.org/project/timezonefinder-data/" + original.name.split("-")[1],
        "source_sha256": source_hash,
        "compiler_sha256": spec["compiler_sha256"],
        "algorithm": "GEOS coverage_simplify (Visvalingam–Whyatt, shared boundaries)",
        "shapely_version": shapely.__version__,
        "tolerance_degrees": tolerance,
        "note": "Tolerance is an algorithm parameter, not a maximum distance error bound. "
        "Automatic timezone may differ near borders; manual IANA timezone overrides remain exact.",
        "zones": finder.nr_of_zones,
        "polygons": len(polygons),
        "holes": sum(len(p.interiors) for p in polygons),
        "vertices_before": before,
        "vertices_after": after,
        "global_sample_count": samples,
        "global_sample_changed": changed,
        "license": "ODbL-1.0; original DATA_LICENSE retained. Derived database under same terms.",
    }
    finder.cleanup()
    compact.cleanup()
    entries = {}
    new_info = f"timezonefinder_data-{version}.dist-info"
    with zipfile.ZipFile(original) as archive:
        for name in archive.namelist():
            if name.startswith("timezonefinder_data/data/") or name.endswith("/RECORD"):
                continue
            renamed = name
            if ".dist-info/" in name:
                renamed = new_info + "/" + name.split(".dist-info/", 1)[1]
            content = archive.read(name)
            if name.endswith("/METADATA"):
                content = content.replace(
                    f"Version: {original.name.split('-')[1]}\n".encode(),
                    f"Version: {version}\n".encode(),
                )
            entries[renamed] = content
    for path in sorted(output.rglob("*")):
        if path.is_file():
            entries["timezonefinder_data/data/" + path.relative_to(output).as_posix()] = (
                path.read_bytes()
            )
    entries["timezonefinder_data/OVERPASS_DATA_PROVENANCE.json"] = (
        json.dumps(report, indent=2) + "\n"
    ).encode()
    records = io.StringIO()
    writer = csv.writer(records, lineterminator="\n")
    for name, content in sorted(entries.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        writer.writerow([name, "sha256=" + digest, len(content)])
    writer.writerow([new_info + "/RECORD", "", ""])
    entries[new_info + "/RECORD"] = records.getvalue().encode()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            item = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, content)
    if target.stat().st_size >= 10 * 1024 * 1024:
        raise ValueError("Simplified timezone wheel exceeds the website's 10 MiB budget")
    report["wheel_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    shutil.rmtree(source_dir)
    print(f"Simplified timezone wheel: {target.stat().st_size:,} bytes", flush=True)
    return target
