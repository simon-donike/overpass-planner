"""Extracted from Julian Manning's original scientific implementation (MIT, 2026)."""

from __future__ import annotations

import asyncio
import json
from zoneinfo import ZoneInfo

import geopandas as gpd
import numpy as np
import pandas as pd
from skyfield.framelib import itrs

from .core.geometry import _roi_anchor, resolve_roi_timezone
from .core.orbits import (
    GEOD,
    SATELLITE_CATALOGUE,
    _build_satellite,
    _propagate,
    _resolve_satellites,
    _ts,
    satellite_positions,
    subsolar_point,
)
from .core.reporting import next_overpasses
from .core.sensors import resolve_s1_mode, sentinel1_footprint_edges
from .types import SatelliteSelection, TLERecord


def _split_antimeridian(lats, lons) -> list:
    """Split a lat/lon sequence into [[lat, lon], ...] segments wherever it crosses ±180°."""
    segs, cur = [], []
    for i, (la, lo) in enumerate(zip(lats, lons)):
        if cur and abs(lo - lons[i - 1]) > 180:
            segs.append(cur)
            cur = []
        cur.append([float(la), float(lo)])
    if cur:
        segs.append(cur)
    return [s for s in segs if len(s) > 1]


def _night_polygon(when_utc, step_deg: float = 2.0) -> list:
    """[[lat, lon], ...] ring of the night hemisphere for a Leaflet overlay."""
    dec, slon = subsolar_point(when_utc)
    dec = dec if abs(dec) > 1e-3 else 1e-3
    lons = np.arange(-180.0, 180.0 + step_deg, step_deg)
    lat_t = np.rad2deg(np.arctan(-np.cos(np.deg2rad(lons - slon)) / np.tan(np.deg2rad(dec))))
    pole = -90.0 if dec > 0 else 90.0
    return [[pole, -180.0]] + [[float(a), float(b)] for a, b in zip(lat_t, lons)] + [[pole, 180.0]]


class LiveTracker:
    """
    Jupyter live tracker (ipyleaflet) — an n2yo-style view focused on an ROI.

    Shows each satellite's current position, past (dashed) and future (solid)
    ground track, its current instrument swath, the day/night terminator and the
    ROI, plus a live panel with UTC and ROI-local clocks, positions and a countdown
    to each satellite's next predicted ROI overpass. Updates run as an asyncio task
    on the kernel's event loop, so the notebook stays usable while it runs.

    Use ``tracker = live_tracker(...)`` then ``tracker.stop()`` / ``tracker.start()``.
    """

    def __init__(
        self,
        tles: dict[str, TLERecord],
        roi_gdf: gpd.GeoDataFrame,
        passes: gpd.GeoDataFrame | None = None,
        satellites: SatelliteSelection = None,
        refresh_s=2.0,
        track_minutes=(45, 90),
        timezone_name=None,
        zoom=3,
        height: str = "620px",
        s1_mode: str | None = None,
    ) -> None:
        try:
            import ipyleaflet as L
            import ipywidgets as W
        except ImportError as exc:
            raise ImportError("Install eo-overpass-predictor[notebooks] for live widgets.") from exc
        if not np.isfinite(refresh_s) or refresh_s <= 0:
            raise ValueError("refresh_s must be finite and positive.")

        self.L, self.W = L, W
        self.tles = tles
        self.names = _resolve_satellites(satellites) if satellites is not None else list(tles)
        self.sats = {n: _build_satellite(n, tles[n]) for n in self.names}
        self.roi = roi_gdf.geometry.iloc[0]
        self.passes = passes
        self.s1_mode = resolve_s1_mode(passes, s1_mode)
        self.refresh_s = refresh_s
        self.past_min, self.future_min = track_minutes
        self.tz_name = timezone_name or resolve_roi_timezone(roi_gdf, verbose=False)
        self.tz = ZoneInfo(self.tz_name)
        self._task, self._running, self._last_track = None, False, None

        a = _roi_anchor(self.roi)
        self.map = L.Map(
            center=(a.y, a.x),
            zoom=zoom,
            basemap=L.basemaps.Esri.WorldImagery,
            scroll_wheel_zoom=True,
            layout=W.Layout(height=height),
        )
        self.night = L.Polygon(
            locations=_night_polygon(pd.Timestamp.now(tz="UTC")),
            color="#000",
            weight=0,
            fill_color="#000",
            fill_opacity=0.35,
        )
        self.map.add(self.night)
        self.map.add(
            L.GeoJSON(
                data=json.loads(gpd.GeoSeries([self.roi]).to_json()),
                style={"color": "#FF3B30", "weight": 2, "fillOpacity": 0.15},
                name="ROI",
            )
        )

        self.layers = {}
        for n in self.names:
            c = SATELLITE_CATALOGUE[n]["colour"]
            past = L.Polyline(
                locations=[], color=c, weight=1, opacity=0.6, dash_array="4,6", fill=False
            )
            fut = L.Polyline(locations=[], color=c, weight=2, opacity=0.9, fill=False)
            swath = L.Polygon(locations=[], color=c, weight=1, fill_color=c, fill_opacity=0.25)
            dot = L.CircleMarker(
                location=(0, 0), radius=7, color="#fff", weight=2, fill_color=c, fill_opacity=1
            )
            dot.popup = W.HTML(f"<b>{n}</b>")
            for lyr in (past, fut, swath, dot):
                self.map.add(lyr)
            self.layers[n] = {"past": past, "future": fut, "swath": swath, "dot": dot}

        self.panel = W.HTML()
        self.btn = W.ToggleButton(
            value=True, description="Live", icon="satellite", tooltip="Pause / resume live updates"
        )
        self.btn.observe(lambda ch: self.start() if ch["new"] else self.stop(), "value")
        self.widget = W.VBox([W.HBox([self.btn]), self.map, self.panel])
        self.update(force_tracks=True)

    # ---------------------------------------------------------------------------------------
    def _ground_track(self, name, now):
        off = np.arange(-self.past_min * 60, self.future_min * 60 + 1, 30.0)
        lat, lon, alt = _propagate(self.sats[name], now, off)
        k = int(np.searchsorted(off, 0))
        return (
            _split_antimeridian(lat[: k + 1], lon[: k + 1]),
            _split_antimeridian(lat[k:], lon[k:]),
        )

    def _current_swath(self, name, now, minutes=2.5):
        off = np.arange(-minutes * 60, minutes * 60 + 1, 10.0)
        lat, lon, alt = _propagate(self.sats[name], now, off)
        az, _, _ = GEOD.inv(lon[:-1], lat[:-1], lon[1:], lat[1:])
        az = np.append(az, az[-1])
        cat = SATELLITE_CATALOGUE[name]
        if cat.get("geometry") == "right_looking_sar":
            ts = _ts().utc(
                now.year,
                now.month,
                now.day,
                now.hour,
                now.minute,
                now.second + now.microsecond / 1e6 + off,
            )
            states = self.sats[name].at(ts)
            ecef, velocity = states.frame_xyz_and_velocity(itrs)
            ecef = ecef.km.T
            vel = velocity.km_per_s.T
            edges = [
                sentinel1_footprint_edges(ecef[i], vel[i], float(alt[i]), self.s1_mode)
                for i in range(len(lon))
            ]
            llon = np.asarray([e[0][0] for e in edges])
            llat = np.asarray([e[0][1] for e in edges])
            rlon = np.asarray([e[1][0] for e in edges])
            rlat = np.asarray([e[1][1] for e in edges])
        else:
            half = cat["swath_km"] * 500.0
            llon, llat, _ = GEOD.fwd(lon, lat, az - 90, np.full_like(lon, half))
            rlon, rlat, _ = GEOD.fwd(lon, lat, az + 90, np.full_like(lon, half))
        ring_lon = np.concatenate([llon, rlon[::-1]])
        if ring_lon.max() - ring_lon.min() > 180:  # skip drawing across the antimeridian
            return []
        return [[float(a), float(b)] for a, b in zip(np.concatenate([llat, rlat[::-1]]), ring_lon)]

    def update(self, force_tracks: bool = False) -> None:
        now = pd.Timestamp.now(tz="UTC")
        pos = satellite_positions(self.tles, now, self.names).set_index("satellite")
        redo = (
            force_tracks
            or self._last_track is None
            or (now - self._last_track).total_seconds() > 60
        )
        for n, lyr in self.layers.items():
            p = pos.loc[n]
            lyr["dot"].location = (p["lat"], p["lon"])
            lyr["swath"].locations = self._current_swath(n, now)
            if redo:
                past, fut = self._ground_track(n, now)
                lyr["past"].locations, lyr["future"].locations = past, fut
        if redo:
            self.night.locations = _night_polygon(now)
            self._last_track = now
        self.panel.value = self._panel_html(now, pos)

    def _panel_html(self, now, pos):
        loc = now.tz_convert(self.tz)
        nxt = (
            next_overpasses(self.passes, now).set_index("Satellite")
            if self.passes is not None and len(self.passes)
            else pd.DataFrame()
        )
        rows = []
        for n, p in pos.iterrows():
            c = SATELLITE_CATALOGUE[n]["colour"]
            cd = f"{nxt.loc[n, 'Countdown']} · {nxt.loc[n, 'Local']}" if n in nxt.index else "—"
            rows.append(
                f"<tr><td><span style='color:{c}'>●</span> {n}</td>"
                f"<td>{p['lat']:+7.2f}</td><td>{p['lon']:+8.2f}</td><td>{p['alt_km']:.0f}</td>"
                f"<td>{'↑' if p['direction'] == 'Ascending' else '↓'}</td><td>{cd}</td></tr>"
            )
        return (
            "<div style='font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px'>"
            f"<b>UTC</b> {now:%Y-%m-%d %H:%M:%S} &nbsp;|&nbsp; <b>ROI local</b> "
            f"{loc:%Y-%m-%d %H:%M:%S} {loc.tzname()} ({self.tz_name})"
            "<table style='border-collapse:collapse;margin-top:6px' cellpadding='3'>"
            "<tr style='text-align:left;border-bottom:1px solid #999'><th>Satellite</th><th>Lat</th>"
            "<th>Lon</th><th>Alt km</th><th></th><th>Next ROI overpass (countdown · local)</th></tr>"
            + "".join(rows)
            + "</table></div>"
        )

    # ---------------------------------------------------------------------------------------
    async def _loop(self):
        while self._running:
            try:
                self.update()
            except Exception as exc:  # keep the loop alive; surface the error in the panel
                self.panel.value += f"<pre style='color:#c00'>{exc}</pre>"
            await asyncio.sleep(self.refresh_s)

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.ensure_future(self._loop())
        if not self.btn.value:
            self.btn.value = True

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self.btn.value:
            self.btn.value = False

    def _ipython_display_(self):
        from IPython.display import display

        display(self.widget)


def live_tracker(
    tles: dict[str, TLERecord],
    roi_gdf: gpd.GeoDataFrame,
    passes: gpd.GeoDataFrame | None = None,
    satellites: SatelliteSelection = None,
    refresh_s: float = 2.0,
    track_minutes=(45, 90),
    timezone_name=None,
    zoom: int = 3,
    height: str = "620px",
    autostart: bool = True,
    s1_mode: str | None = None,
) -> LiveTracker:
    """
    Create (and by default start) the ipyleaflet live tracker in a notebook cell.

    Args:
        tles (dict): Output of ``fetch_tles``.
        roi_gdf (gpd.GeoDataFrame): ROI from ``load_roi``.
        passes (gpd.GeoDataFrame, optional): Output of ``predict_overpasses`` — enables
            the next-overpass countdown column.
        satellites (Iterable, optional): Subset to display.
        refresh_s (float, optional): Position refresh interval. Defaults to 2 s.
        track_minutes (tuple, optional): (past, future) ground-track span. Defaults to (45, 90).
        timezone_name (str, optional): IANA zone for the local clock. Defaults to ROI zone.
        zoom (int, optional): Initial zoom. Defaults to 3.
        height (str, optional): Map height. Defaults to "620px".
        autostart (bool, optional): Start updating immediately. Defaults to True.

    Returns:
        LiveTracker: Display it (last line of a cell) and call ``.stop()`` when done.
    """

    tr = LiveTracker(
        tles,
        roi_gdf,
        passes=passes,
        satellites=satellites,
        refresh_s=refresh_s,
        track_minutes=track_minutes,
        timezone_name=timezone_name,
        zoom=zoom,
        height=height,
        s1_mode=s1_mode,
    )
    if autostart:
        tr.start()
    return tr
