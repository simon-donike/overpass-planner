"""Explicit STAC I/O and conservative acquisition evidence normalization.

Catalog presence, readable asset bytes and exact ROI sensing times are different
observations. No download is performed by the production predictor.
"""

from __future__ import annotations

import time
from threading import Lock
from urllib.parse import urljoin, urlsplit

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from shapely.geometry import MultiPolygon, Point, Polygon, shape
from urllib3.util.retry import Retry

from eo_overpass_predictor import SATELLITE_CATALOGUE

from .design import MAX_EVENT_SPAN_S, Study, digest, iso, utc

PROVIDERS = {
    "Landsat": ("https://landsatlook.usgs.gov/stac-server", "landsat-c2l1"),
    "Sentinel-2": ("https://stac.dataspace.copernicus.eu/v1", "sentinel-2-l1c"),
    "Sentinel-1": ("https://stac.dataspace.copernicus.eu/v1", "sentinel-1-grd"),
}
PLATFORMS = {name.lower().replace(" ", "-"): name for name in SATELLITE_CATALOGUE}
OTHER_PLATFORMS = {f"landsat-{i}" for i in range(1, 8)} | {"sentinel-1a", "sentinel-1b"}


class CatalogClient:
    _access_denials: dict[str, dict] = {}
    _access_denials_lock = Lock()

    def __init__(self, interval_s: float = 1.0, skip_repeated_access_denials: bool = False):
        self.interval_s = interval_s
        self.skip_repeated_access_denials = skip_repeated_access_denials
        self.session = requests.Session()
        retries = Retry(
            total=4,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.last_request = 0.0

    def request(self, method: str, url: str, **kwargs):
        time.sleep(max(0, self.last_request + self.interval_s - time.monotonic()))
        self.last_request = time.monotonic()
        return self.session.request(method, url, timeout=(15, 90), **kwargs)

    def close(self):
        self.session.close()

    def search(self, endpoint: str, body: dict, max_pages: int = 1000) -> dict:
        """Only an exhausted, valid pagination chain counts as a complete search."""
        method, url, payload = "POST", endpoint.rstrip("/") + "/search", body
        pages, seen, features = [], set(), []
        captured = iso(pd.Timestamp.now(tz="UTC"))
        try:
            for _ in range(max_pages):
                request_id = digest([method, url, payload])
                if request_id in seen:
                    raise ValueError("Pagination cycle.")
                seen.add(request_id)
                response = self.request(
                    method, url, **({"json": payload} if method == "POST" else {})
                )
                with response:
                    response.raise_for_status()
                    page = response.json()
                pages.append({"method": method, "url": url, "body": payload, "response": page})
                if (
                    not isinstance(page, dict)
                    or page.get("type") != "FeatureCollection"
                    or not isinstance(page.get("features"), list)
                ):
                    raise ValueError("Not a STAC FeatureCollection.")
                features.extend(page["features"])
                links = page.get("links", [])
                if not isinstance(links, list) or any(not isinstance(link, dict) for link in links):
                    raise ValueError("Invalid pagination links.")
                next_links = [link for link in links if link.get("rel") == "next"]
                if not next_links:
                    matched = pages[0]["response"].get("numberMatched")
                    if matched is None:
                        matched = pages[0]["response"].get("context", {}).get("matched")
                    if isinstance(matched, int) and matched > len(features):
                        raise ValueError("Truncated response: missing next link.")
                    return {
                        "status": "complete",
                        "captured_at_utc": captured,
                        "pages": pages,
                        "features": features,
                        "error": None,
                    }
                if len(next_links) != 1:
                    raise ValueError("Ambiguous next links.")
                link = next_links[0]
                next_url = urljoin(url, link["href"])
                if (
                    urlsplit(next_url).netloc != urlsplit(endpoint).netloc
                    or urlsplit(next_url).scheme != "https"
                ):
                    raise ValueError("Cross-origin or non-HTTPS pagination link.")
                method, url = link.get("method", "GET").upper(), next_url
                if method not in {"GET", "POST"}:
                    raise ValueError("Unsupported pagination method.")
                payload = (
                    {**payload, **link.get("body", {})}
                    if link.get("merge")
                    else link.get("body", {})
                )
            raise ValueError("Pagination page limit exceeded.")
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            return {
                "status": "unknown",
                "captured_at_utc": captured,
                "pages": pages,
                "features": features,
                "error": str(exc),
            }

    def probe(self, asset: dict) -> dict:
        """Read a byte from a data asset; never download the full raster."""
        href = asset.get("href", "")
        result = {"href": href, "checked_at_utc": iso(pd.Timestamp.now(tz="UTC"))}
        if urlsplit(href).scheme != "https":
            return {**result, "status": "unsupported_uri"}
        host = urlsplit(href).netloc
        if self.skip_repeated_access_denials:
            with self._access_denials_lock:
                previous = self._access_denials.get(host)
            if previous:
                return {
                    **result,
                    "status": "not_checked_after_access_denial",
                    "prior_challenge": previous,
                }

        def denied(reason):
            record = {**result, "status": "access_denied", "reason": reason}
            if self.skip_repeated_access_denials:
                with self._access_denials_lock:
                    self._access_denials[host] = {
                        "host": host,
                        "checked_at_utc": result["checked_at_utc"],
                        "http_status": result["http_status"],
                        "reason": reason,
                    }
            return record

        try:
            with self.request("GET", href, headers={"Range": "bytes=0-0"}, stream=True) as response:
                result["http_status"] = response.status_code
                if response.status_code in {401, 403}:
                    return denied("http_access_denied")
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                final = urlsplit(getattr(response, "url", href))
                if "html" in content_type and any(
                    word in (final.netloc + final.path).lower()
                    for word in ("login", "ers.cr.usgs.gov", "/auth/")
                ):
                    return {
                        **denied("authentication_redirect"),
                        "redirected_to": f"{final.scheme}://{final.netloc}{final.path}",
                    }
                binary = bool(content_type) and not any(
                    t in content_type for t in ("html", "json", "xml", "text/")
                )
                readable = (
                    response.status_code in {200, 206}
                    and binary
                    and bool(next(response.iter_content(1), b""))
                )
                return {
                    **result,
                    "status": "verified" if readable else "unknown",
                    "content_type": content_type,
                }
        except requests.RequestException as exc:
            return {**result, "status": "unknown", "error": str(exc)}


def covers_point(geometry: dict, lon: float, lat: float) -> bool:
    """Unwrap each ring before testing dateline-adjacent point coverage."""
    if not isinstance(geometry, dict):
        raise ValueError("Missing catalog footprint.")
    try:
        geom = shape(geometry)
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise ValueError("Malformed catalog footprint.") from exc
    if not isinstance(geom, Polygon | MultiPolygon) or geom.is_empty:
        raise ValueError("A nonempty polygon footprint is required.")

    def ring(coords):
        coords = np.asarray(coords)
        return np.column_stack([np.degrees(np.unwrap(np.radians(coords[:, 0]))), coords[:, 1]])

    polygons = [geom] if isinstance(geom, Polygon) else list(geom.geoms)
    for polygon in polygons:
        exterior = ring(polygon.exterior.coords)
        center = float(np.mean(exterior[:, 0]))
        holes = []
        for interior in polygon.interiors:
            hole = ring(interior.coords)
            hole[:, 0] += 360 * round((center - float(np.mean(hole[:, 0]))) / 360)
            holes.append(hole)
        unwrapped = Polygon(exterior, holes)
        if not unwrapped.is_valid:
            raise ValueError("Invalid or ambiguous catalog footprint.")
        x = lon + 360 * round((center - lon) / 360)
        if unwrapped.covers(Point(x, lat)):
            return True
    return False


def normalize_items(features: list[dict], case: dict, study: Study) -> list[dict]:
    """Collapse reprocessing/tiles into acquisition events at this point.

    Invalid relevant metadata makes the query unknown, never a false negative.
    Events retain sensing intervals; padded instants are explicitly proxies.
    """
    mission = SATELLITE_CATALOGUE[case["satellite"]]["mission"]
    records: list[dict] = []
    seen = set()
    for item in features:
        props = item["properties"]
        if not isinstance(props, dict):
            raise ValueError("Missing item properties.")
        platform = props.get("platform")
        if not isinstance(platform, str):
            raise ValueError("Missing platform: cannot exclude an unidentified item.")
        platform = platform.lower().replace("_", "-").replace(" ", "-")
        satellite = PLATFORMS.get(platform)
        if satellite is None and platform not in OTHER_PLATFORMS:
            raise ValueError(f"Unrecognized platform identifier: {platform}")
        if satellite != case["satellite"]:
            continue
        if not covers_point(item["geometry"], case["longitude"], case["latitude"]):
            continue
        if mission == "Sentinel-1":
            mode = props.get("sar:instrument_mode")
            if not isinstance(mode, str):
                raise ValueError("Missing Sentinel-1 instrument mode.")
            if mode.upper() != study.s1_mode:
                continue
        key = (item["collection"], item["id"])
        if key in seen:
            continue
        seen.add(key)
        start, end = props.get("start_datetime"), props.get("end_datetime")
        if (start is None) != (end is None):
            raise ValueError("Incomplete product sensing interval.")
        if start is not None and end is not None:
            start, end = utc(start), utc(end)
            basis = "product_sensing_interval"
            if start == end:
                padding = pd.Timedelta(seconds=study.timestamp_padding_s[mission])
                start, end = start - padding, end + padding
                basis = "padded_degenerate_product_interval_proxy"
        else:
            instant = utc(props["datetime"])
            padding = pd.Timedelta(seconds=study.timestamp_padding_s[mission])
            start, end = instant - padding, instant + padding
            basis = "padded_product_timestamp_proxy"
        if end < start or (end - start).total_seconds() > MAX_EVENT_SPAN_S:
            raise ValueError("Invalid or excessively broad sensing interval.")
        direction = props.get("sat:orbit_state")
        if direction is not None and (
            not isinstance(direction, str) or direction.lower() not in {"ascending", "descending"}
        ):
            raise ValueError("Invalid orbit direction.")
        assets = item.get("assets", {})
        if not isinstance(assets, dict) or any(not isinstance(a, dict) for a in assets.values()):
            raise ValueError("Invalid asset metadata.")
        for asset in assets.values():
            if not isinstance(asset.get("href"), str) or not isinstance(
                asset.get("roles", []), list
            ):
                raise ValueError("Invalid asset URI or roles.")
        records.append(
            {
                "start_utc": iso(start),
                "end_utc": iso(end),
                "time_basis": basis,
                "direction": direction.lower() if direction else None,
                "absolute_orbit": props.get("sat:absolute_orbit"),
                "items": [
                    {
                        "id": item["id"],
                        "collection": item["collection"],
                        "cloud_cover_pct": props.get("eo:cloud_cover"),
                        "assets": assets,
                    }
                ],
            }
        )
    records.sort(key=lambda r: (utc(r["start_utc"]), utc(r["end_utc"])))
    events: list[dict] = []
    for record in records:
        previous = events[-1] if events else None
        same_orbit = previous and not (
            record["absolute_orbit"] is not None
            and previous["absolute_orbit"] is not None
            and record["absolute_orbit"] != previous["absolute_orbit"]
        )
        same_direction = previous and not (
            record["direction"]
            and previous["direction"]
            and record["direction"] != previous["direction"]
        )
        gap = (
            (utc(record["start_utc"]) - utc(previous["end_utc"])).total_seconds()
            if previous
            else float("inf")
        )
        if (
            previous is not None
            and same_orbit
            and same_direction
            and gap <= study.event_merge_gap_s
        ):
            previous["end_utc"] = max(previous["end_utc"], record["end_utc"], key=utc)
            previous["items"].extend(record["items"])
            previous["direction"] = previous["direction"] or record["direction"]
            previous["absolute_orbit"] = previous["absolute_orbit"] or record["absolute_orbit"]
            if record["time_basis"] != previous["time_basis"]:
                previous["time_basis"] = "mixed_product_time_proxies"
        else:
            events.append(record)
    for event in events:
        if (utc(event["end_utc"]) - utc(event["start_utc"])).total_seconds() > MAX_EVENT_SPAN_S:
            raise ValueError("Merged event interval exceeds 40 minutes.")
        event["event_id"] = digest(
            [
                case["satellite"],
                event["start_utc"],
                event["end_utc"],
                sorted(i["id"] for i in event["items"]),
            ]
        )[:24]
    return events


def probe_event(client: CatalogClient, event: dict) -> dict:
    candidates = [
        asset
        for item in event["items"]
        for asset in item["assets"].values()
        if "data" in asset.get("roles", [])
    ]
    candidates.sort(
        key=lambda a: (
            urlsplit(a.get("href", "")).scheme != "https",
            "reflectance" not in a.get("roles", []),
        )
    )
    # One readable data asset proves limited file accessibility, not every band/pixel.
    probes = []
    for asset in candidates[:3]:
        probes.append(client.probe(asset))
        if probes[-1]["status"] == "verified":
            break
        if probes[-1]["status"] in {"access_denied", "not_checked_after_access_denial"}:
            break
    return {
        "status": "verified" if any(p["status"] == "verified" for p in probes) else "unknown",
        "probes": probes,
        "scope": "at_least_one_data_asset_byte",
    }


def query_spec(case: dict, study: Study) -> tuple[str, dict]:
    mission = SATELLITE_CATALOGUE[case["satellite"]]["mission"]
    endpoint, collection = PROVIDERS[mission]
    guard_s = study.match_gate_s + MAX_EVENT_SPAN_S
    start = utc(case["origin"]) - pd.Timedelta(seconds=guard_s)
    end = utc(case["origin"]) + pd.Timedelta(days=study.horizon_days, seconds=guard_s)
    body = {
        "collections": [collection],
        "intersects": {"type": "Point", "coordinates": [case["longitude"], case["latitude"]]},
        "datetime": f"{iso(start)}/{iso(end)}",
        "limit": 100,
    }
    return endpoint, body
