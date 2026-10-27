"""Convert IAU CPS archive observations into conservative as-of input evidence."""

from __future__ import annotations

import hashlib
from pathlib import Path

from eo_overpass_predictor import SATELLITE_CATALOGUE, parse_tle_text

from .design import iso, read_json, utc, write_json


def convert_iau_archive(directory: Path, output: Path) -> dict:
    """Use date_collected as an availability upper bound, never as TLE epoch.

    Original responses remain untouched. Reject corrupted lines rather than
    repairing their checksums or reconstructing rounded orbital elements.
    """
    import json

    names = {s["norad_id"]: name for name, s in SATELLITE_CATALOGUE.items()}
    records, rejected = [], []
    for path in sorted(directory.glob("[0-9]*.json")):
        if path.name.endswith(".provenance.json"):
            continue
        metadata = read_json(path.with_suffix(".provenance.json"))
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if metadata["sha256"] != actual_hash:
            raise ValueError(f"Archive response checksum mismatch: {path}")
        payload = read_json(path)
        if payload["count"] != len(payload["data"]):
            raise ValueError(f"Incomplete archive response: {path}")
        for index, row in enumerate(payload["data"]):
            try:
                norad = int(row["satellite_id"])
                parsed = parse_tle_text(row["tle_line1"] + "\n" + row["tle_line2"])
                if set(parsed) != {norad} or norad not in names:
                    raise ValueError("Satellite and orbital identifiers disagree.")
                available = utc(row["date_collected"].replace(" UTC", "+00:00"))
                if available > utc(metadata["retrieved_at_utc"]):
                    raise ValueError("Archive collection time follows retrieval.")
                records.append(
                    {
                        "satellite": names[norad],
                        "line1": row["tle_line1"],
                        "line2": row["tle_line2"],
                        "published_at_utc": iso(available),
                        "retrieved_at_utc": metadata["retrieved_at_utc"],
                        "source": metadata["url"],
                        "availability_time_basis": "archive_observed_by_utc",
                        "archive_collected_utc": iso(available),
                        "publication_evidence": f"{path} sha256={actual_hash} row={index} date_collected={row['date_collected']}",
                    }
                )
            except (ValueError, KeyError, TypeError) as exc:
                rejected.append({"raw_file": str(path), "row": index, "error": str(exc)})
    if not records:
        raise ValueError("No valid, evidenced historical elements.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8"
    )
    write_json(output.with_suffix(".rejections.json"), rejected)
    return {
        "accepted_records": len(records),
        "rejected_records": len(rejected),
        "path": str(output),
    }
