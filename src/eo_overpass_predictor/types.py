"""Lightweight record types; runtime values remain ordinary dictionaries."""

from collections.abc import Iterable
from datetime import date, datetime
from typing import TypeAlias, TypedDict

import pandas as pd


class SatelliteInfo(TypedDict):
    """Nominal nadir-centred instrument characteristics, not an acquisition guarantee."""

    norad_id: int
    mission: str
    sensor: str
    swath_km: float
    lat_limits: tuple[float, float]
    colour: str
    note: str


class RawTLE(TypedDict):
    """Checksum-validated element lines parsed from text."""

    name: str | None
    line1: str
    line2: str


class TLERecord(TypedDict):
    """Orbital input returned by fetch_tles, keyed by canonical satellite name."""

    norad_id: int
    line1: str
    line2: str
    epoch_utc: pd.Timestamp
    source: str


DateInput: TypeAlias = str | date | datetime | pd.Timestamp
SatelliteSelection: TypeAlias = Iterable[str | int] | str | int | None
