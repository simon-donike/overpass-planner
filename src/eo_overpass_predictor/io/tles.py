"""Extracted from Julian Manning's original scientific implementation (MIT, 2026)."""

from __future__ import annotations

import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from skyfield.api import EarthSatellite

from ..core.orbits import SATELLITE_CATALOGUE, _resolve_satellites, _ts
from ..types import RawTLE, SatelliteSelection, TLERecord
from .cache import cache_root, write_cache_file

CELESTRAK_GP_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=TLE"
TLE_MIRROR_URL = (
    "https://tle.ivanstanojevic.me/api/tle/{norad_id}"  # community CelesTrak mirror (fallback)
)


def _tle_checksum(line: str) -> int:
    """Modulo-10 TLE checksum over the first 68 characters ('-' counts as 1)."""
    total = 0
    for c in line[:68]:
        if c.isdigit():
            total += int(c)
        elif c == "-":
            total += 1
    return total % 10


def normalise_tle_line(line: str) -> str:
    """
    Validate a TLE data line and, if needed, rebuild its fixed-width column layout.

    TLEs copied from web pages (e.g. n2yo.com) often have their column spacing
    collapsed to single spaces, which breaks SGP4 parsers that rely on fixed
    column positions. This function re-pads each field to the NORAD two-line
    element specification and verifies the modulo-10 checksum, so a corrupted
    or mistyped line is rejected rather than silently producing a wrong orbit.

    Args:
        line (str): A TLE line starting with "1" or "2".

    Returns:
        str: A 69-character, checksum-verified TLE line.

    Raises:
        ValueError: If the line cannot be rebuilt or its checksum does not match.
    """

    line = line.strip()
    if len(line) == 69 and line[68].isdigit() and _tle_checksum(line) == int(line[68]):
        return line

    t = line.split()
    if t and t[0] == "1":
        if len(t) == 8:  # missing international designator
            t.insert(2, "")
        if len(t) != 9:
            raise ValueError(f"Cannot parse TLE line 1: {line!r}")
        rebuilt = (
            f"1 {t[1]:<6} {t[2]:<8} {t[3]:>14} {t[4]:>10} {t[5]:>8} {t[6]:>8} {t[7]:>1} {t[8]:>5}"
        )
    elif t and t[0] == "2":
        if len(t) == 8:
            tail = t[7]
        elif len(t) == 9:  # mean motion and revolution number split
            tail = f"{t[7]:>11}{t[8]:>6}"
        else:
            raise ValueError(f"Cannot parse TLE line 2: {line!r}")
        rebuilt = f"2 {t[1]:>5} {t[2]:>8} {t[3]:>8} {t[4]:>7} {t[5]:>8} {t[6]:>8} {tail:>17}"
    else:
        raise ValueError(f"Not a TLE data line: {line!r}")

    if len(rebuilt) != 69 or _tle_checksum(rebuilt) != int(rebuilt[68]):
        raise ValueError(f"TLE checksum failed after re-formatting: {line!r}")
    return rebuilt


def parse_tle_text(text: str) -> dict[int, RawTLE]:
    """
    Parse TLE text (2-line or 3-line format, any spacing) into a NORAD-keyed dictionary.

    Args:
        text (str): Raw TLE text, e.g. a CelesTrak response or a pasted block.

    Returns:
        dict: ``{norad_id: {"name": str | None, "line1": str, "line2": str}}``.
    """

    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    out: dict[int, RawTLE] = {}
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        name = None
        if not ln.startswith("1 "):
            name = ln.lstrip("0 ").strip() or None
            i += 1
            if i >= len(lines):
                break
            ln = lines[i].strip()
        if ln.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].strip().startswith("2 "):
            l1 = normalise_tle_line(ln)
            l2 = normalise_tle_line(lines[i + 1])
            if l1[2:7] != l2[2:7]:
                raise ValueError("TLE line identifiers do not match.")
            out[int(l1[2:7])] = {"name": name, "line1": l1, "line2": l2}
            i += 2
        else:
            i += 1
    if not out:
        raise ValueError("No complete TLE records found.")
    return out


def _download_tle(norad: int, timeout: int):
    """Download one TLE: CelesTrak GP first, then the tle.ivanstanojevic.me mirror."""
    errors = []
    try:
        r = requests.get(CELESTRAK_GP_URL.format(norad_id=norad), timeout=timeout)
        r.raise_for_status()
        rec = parse_tle_text(r.text).get(norad)
        if rec:
            return rec, "CelesTrak"
        errors.append(f"CelesTrak: {r.text.strip()[:80] or 'empty response'}")
    except Exception as exc:
        errors.append(f"CelesTrak: {type(exc).__name__}")
    try:
        r = requests.get(TLE_MIRROR_URL.format(norad_id=norad), timeout=timeout)
        r.raise_for_status()
        j = r.json()
        rec = parse_tle_text(f"{j['line1']}\n{j['line2']}").get(norad)
        if rec:
            return rec, "TLE API mirror"
        errors.append("mirror: unparseable")
    except Exception as exc:
        errors.append(f"mirror: {type(exc).__name__}")
    raise RuntimeError("; ".join(errors))


def fetch_tles(
    satellites: SatelliteSelection = None,
    tle_dir: str | Path | None = None,
    max_age_hours: float = 12.0,
    tle_text: str | None = None,
    timeout: int = 30,
    verbose: bool = True,
    *,
    offline: bool = False,
) -> dict[str, TLERecord]:
    """
    Retrieve current TLEs for the selected satellites, with caching and offline fallback.

    For each satellite the function first looks for a cached ``<norad>.tle`` file
    in ``tle_dir``. If the cache is younger than ``max_age_hours`` it is reused;
    otherwise a fresh element set is requested from the CelesTrak GP service
    (falling back to the tle.ivanstanojevic.me mirror) and written back to the cache. If the network request fails, a stale cache is
    used with a warning, so predictions still run offline. CelesTrak asks users
    not to poll faster than its ~2-hourly update cycle — the cache honours that.

    Args:
        satellites (SatelliteSelection, optional): Catalogue names, short codes
            ("S2B", "L9"), NORAD ids, or mission groups ("S2", "LS"). Accepts a
            single value, comma-separated string or iterable; duplicates are removed.
            Defaults to all entries in ``SATELLITE_CATALOGUE``.
        tle_dir (str | Path, optional): Cache directory. Defaults to the platform user cache directory.
        max_age_hours (float, optional): Cache lifetime before re-download. Defaults to 12.
        tle_text (str, optional): Manually supplied TLE text (e.g. pasted from n2yo
            or Space-Track). When given, it overrides the network for the satellites
            it contains and is written to the cache.
        timeout (int, optional): HTTP timeout in seconds. Defaults to 30.
        verbose (bool, optional): Print a per-satellite status line. Defaults to True.
        offline (bool): Use supplied elements or cache only; never access the network.

    Returns:
        dict: ``{name: {"norad_id", "line1", "line2", "epoch_utc", "source"}}``.

    Raises:
        RuntimeError: If no TLE can be obtained for a requested satellite.
    """

    names = _resolve_satellites(satellites)
    if not np.isfinite(max_age_hours) or max_age_hours < 0:
        raise ValueError("max_age_hours must be finite and nonnegative.")
    if timeout <= 0:
        raise ValueError("timeout must be positive.")
    tle_dir = Path(tle_dir) if tle_dir is not None else cache_root() / "tle"
    tle_dir.mkdir(parents=True, exist_ok=True)
    manual = parse_tle_text(tle_text) if tle_text else {}

    out = {}
    for name in names:
        norad = SATELLITE_CATALOGUE[name]["norad_id"]
        cache = tle_dir / f"{norad}.tle"
        rec, source, age_h = None, None, None

        if norad in manual:
            rec, source = manual[norad], "manual"
            _write_cached_tle(cache, f"{name}\n{rec['line1']}\n{rec['line2']}\n")
        else:
            age_h = (time.time() - cache.stat().st_mtime) / 3600 if cache.exists() else None
            if age_h is not None and (offline or age_h < max_age_hours):
                rec, source = (
                    _read_cached_tle(cache, norad),
                    f"cache ({age_h:.1f} h old)",
                )
            if offline and rec is None:
                raise RuntimeError(
                    f"No cached or supplied TLE for {name} ({norad}) in offline mode."
                )
            if rec is None:
                try:
                    rec, source = _download_tle(norad, timeout)
                    _write_cached_tle(cache, f"{name}\n{rec['line1']}\n{rec['line2']}\n")
                except Exception as exc:
                    if cache.exists():
                        rec = _read_cached_tle(cache, norad)
                        source = f"stale cache ({age_h:.1f} h old; download failed: {type(exc).__name__})"
                    if rec is None:
                        raise RuntimeError(f"No TLE available for {name} ({norad}): {exc}") from exc

        if source and (
            "stale" in source or (offline and age_h is not None and age_h >= max_age_hours)
        ):
            warnings.warn(f"Using stale TLE for {name}: {source}", stacklevel=2)
        sat = EarthSatellite(rec["line1"], rec["line2"], name, _ts())
        epoch = pd.Timestamp(sat.epoch.utc_datetime())
        out[name] = {
            "norad_id": norad,
            "line1": rec["line1"],
            "line2": rec["line2"],
            "epoch_utc": epoch,
            "source": source,
        }
        if verbose:
            age_d = (pd.Timestamp.now(tz="UTC") - epoch).total_seconds() / 86400
            flag = "\x1b[93m" if age_d > 7 else "\x1b[92m"
            print(
                f"{flag}✔ {name:<12} NORAD {norad:<6} epoch {epoch:%Y-%m-%d %H:%M} UTC "
                f"({age_d:4.1f} d old) — {source}\x1b[0m"
            )
    return out


def _read_cached_tle(path: Path, norad: int) -> RawTLE | None:
    """Reject corrupt cache entries without preventing a fresh download."""
    try:
        return parse_tle_text(path.read_text(encoding="utf-8")).get(norad)
    except (ValueError, UnicodeError) as exc:
        warnings.warn(f"Ignoring invalid TLE cache {path}: {exc}", stacklevel=2)
        return None


def _write_cached_tle(path: Path, text: str) -> None:
    """Publish a complete cache entry atomically, including for concurrent readers."""
    write_cache_file(path, text.encode("utf-8"))
