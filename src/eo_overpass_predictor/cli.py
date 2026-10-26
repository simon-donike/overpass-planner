"""Noninteractive command-line adapter for the public Python API."""

import math
import sys
import warnings
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Annotated, Any
from xml.etree.ElementTree import ParseError
from zoneinfo import ZoneInfoNotFoundError

import geopandas as gpd
import pandas as pd
import requests
import typer
from typer.core import TyperCommand

from . import api
from .core.orbits import _to_utc_timestamp
from .io.cache import cache_files, cache_root, cache_session, clear_cache
from .types import TLERecord

app = typer.Typer(
    no_args_is_help=True, help="Swath-aware Landsat and Sentinel-2 overpass prediction."
)


class _SatelliteCommand(TyperCommand):
    """Translate variadic satellite values into Typer's repeated-option syntax."""

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        expanded: list[str] = []
        collecting = False
        for index, argument in enumerate(args):
            if argument == "--":
                expanded.extend(args[index:])
                break
            if argument in {"--satellite", "-s"} or argument.startswith("--satellite="):
                expanded.append(argument)
                collecting = True
            elif argument.startswith("-s") and (not argument.startswith("--")):
                expanded.append(argument)
                collecting = True
            elif collecting and (not argument.startswith("-")):
                if expanded[-1] not in {"--satellite", "-s"}:
                    expanded.append("--satellite")
                expanded.append(argument)
            else:
                expanded.append(argument)
                collecting = False
        return super().parse_args(ctx, expanded)


ROI = Annotated[
    Path,
    typer.Option("--roi", exists=True, dir_okay=False, help="ROI vector file; any declared CRS."),
]
Start = Annotated[str, typer.Option("--start", help="ISO-8601 start; naive dates mean UTC.")]
End = Annotated[str, typer.Option("--end", help="ISO-8601 end; must follow start.")]
Satellite = Annotated[
    list[str] | None,
    typer.Option(
        "--satellite",
        "-s",
        metavar="SATELLITE...",
        help="Names, aliases or NORAD IDs; space/comma separated or repeated. S2 selects Sentinel-2A/B/C; LS selects Landsat 8/9. Default: all five.",
    ),
]
Timezone = Annotated[str | None, typer.Option("--timezone", help="Override the ROI IANA timezone.")]
TLEFile = Annotated[
    Path | None,
    typer.Option("--tle-file", exists=True, dir_okay=False, help="Checksum-verified TLE text."),
]
Cache = Annotated[
    Path | None,
    typer.Option("--cache-dir", file_okay=False, help="Download cache root; default: user cache."),
]
NoCache = Annotated[
    bool, typer.Option("--no-cache", help="Use temporary downloads, removed on exit.")
]
Output = Annotated[Path, typer.Option("--output", "-o", help="Explicit destination path.")]
JsonOutput = Annotated[
    bool, typer.Option("--json", help="Emit one versioned JSON response; diagnostics go to stderr.")
]
MinCoverage = Annotated[float, typer.Option(min=0, max=100, help="Minimum ROI coverage percent.")]
MinSun = Annotated[
    float, typer.Option("--min-sun-elevation", min=-90, max=90, help="Solar elevation threshold.")
]
AllPasses = Annotated[bool, typer.Option(help="Include night/ascending geometric passes.")]
Plans = Annotated[
    Path | None, typer.Option(exists=True, help="Local ESA KML/KMZ file or directory.")
]
FetchPlans = Annotated[
    bool, typer.Option(help="Explicitly fetch ESA plans and cross-check predictions.")
]


@contextmanager
def _errors(command: str) -> Iterator[None]:
    try:
        yield
    except (ValueError, KeyError, ZoneInfoNotFoundError, ParseError, OverflowError) as exc:
        typer.echo(f"Invalid input: {exc}", err=True)
        raise typer.Exit(2) from exc
    except (OSError, RuntimeError, requests.RequestException, ImportError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


@contextmanager
def _diagnostics() -> Iterator[list[dict[str, str]]]:
    """Keep library progress and Python warnings out of the response stream."""
    messages: list[dict[str, str]] = []
    with redirect_stdout(sys.stderr), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", UserWarning)
        try:
            yield messages
        finally:
            for warning in caught:
                message = str(warning.message)
                messages.append({"code": "runtime_warning", "message": message})
                typer.echo(f"Warning: {message}", err=True)


def _calculate(
    roi: object,
    start: str,
    end: str,
    satellite: list[str] | None,
    timezone: str | None,
    tle_file: Path | None,
    offline: bool,
    cache_dir: Path | None,
    min_roi_coverage_pct: float = 0,
    min_sun_elevation_deg: float = 0,
    daytime_descending_only: bool = True,
) -> tuple[gpd.GeoDataFrame, dict[str, TLERecord], gpd.GeoDataFrame]:
    start_ts, end_ts = (_to_utc_timestamp(start), _to_utc_timestamp(end))
    if end_ts <= start_ts:
        raise ValueError("--end must be later than --start.")
    if not math.isfinite(min_roi_coverage_pct) or not math.isfinite(min_sun_elevation_deg):
        raise ValueError("Coverage and sun-elevation thresholds must be finite.")
    area = api.load_roi(roi)
    if timezone:
        api.resolve_roi_timezone(area, override=timezone, verbose=False)
    tles = api.fetch_tles(
        satellites=satellite,
        tle_dir=cache_dir / "tle" if cache_dir else None,
        tle_text=tle_file.read_text() if tle_file else None,
        offline=offline,
        verbose=False,
    )
    passes = api.predict_overpasses(
        area,
        tles,
        start_utc=start_ts,
        days=(end_ts - start_ts).total_seconds() / 86400,
        timezone_name=timezone,
        verbose=False,
        min_roi_coverage_pct=min_roi_coverage_pct,
        min_sun_elevation_deg=min_sun_elevation_deg,
        daytime_descending_only=daytime_descending_only,
    )
    return (area, tles, passes)


def _check_plans(
    passes: gpd.GeoDataFrame,
    area: gpd.GeoDataFrame,
    tles: dict[str, TLERecord],
    plans: Path | None,
    fetch_plans: bool,
    cache_dir: Path | None,
    diagnostics: list[dict[str, str]],
) -> gpd.GeoDataFrame:
    if not plans and (not fetch_plans):
        return passes
    plan_source: Path | list[Path] | None = plans
    if fetch_plans:
        units = [name for name in tles if name.startswith("SENTINEL-2")]
        try:
            plan_source = (
                api.fetch_s2_acquisition_plans(
                    plan_dir=cache_dir / "plans" if cache_dir else None,
                    start_utc=passes.attrs["window_start_utc"],
                    end_utc=passes.attrs["window_end_utc"],
                    satellites=units,
                    verbose=False,
                )
                if units
                else []
            )
        except (requests.RequestException, RuntimeError, OSError) as exc:
            message = f"ESA plans unavailable: {exc}; using cached plans."
            diagnostics.append({"code": "plans_unavailable", "message": message})
            typer.echo(f"Warning: {message}", err=True)
            plan_source = (
                cache_dir / "plans"
                if cache_dir and (cache_dir / "plans").is_dir()
                else []
                if cache_dir
                else None
            )
    loaded = api.load_s2_acquisition_plans(plan_source)
    return api.crosscheck_s2_acquisition_plan(passes, loaded, area)


def _output_format(output: Path | None, explicit: str | None = None) -> str | None:
    if output is None:
        if explicit:
            raise ValueError("--format requires --output.")
        return None
    suffix = {
        ".csv": "csv",
        ".geojson": "geojson",
        ".json": "geojson",
        ".gpkg": "gpkg",
        ".html": "html",
        ".htm": "html",
    }.get(output.suffix.lower())
    if not suffix or (explicit and explicit.lower() != suffix):
        raise ValueError("Output suffix and --format must agree: .csv, .geojson, .gpkg or .html.")
    return suffix


def _run_prediction(
    command: str,
    roi: object,
    start: str,
    end: str,
    output: Path | None,
    satellite: list[str] | None,
    timezone: str | None,
    tle_file: Path | None,
    cache_dir: Path | None,
    no_cache: bool,
    offline: bool,
    min_coverage: float = 0,
    min_sun_elevation: float = 0,
    all_passes: bool = False,
    plans: Path | None = None,
    fetch_plans: bool = False,
    format: str | None = None,
) -> None:
    chosen = _output_format(output, format)
    if command == "tracker" and chosen != "html":
        raise ValueError("Tracker output must have an .html or .htm suffix.")
    if plans and fetch_plans:
        raise ValueError("Use either --plans or --fetch-plans.")
    if offline and fetch_plans:
        raise ValueError("--fetch-plans cannot be used with --offline.")
    with _diagnostics() as diagnostics, cache_session(cache_dir, no_cache) as active_cache:
        area, tles, passes = _calculate(
            roi,
            start,
            end,
            satellite,
            timezone,
            tle_file,
            offline,
            active_cache,
            min_roi_coverage_pct=min_coverage,
            min_sun_elevation_deg=min_sun_elevation,
            daytime_descending_only=not all_passes,
        )
        passes = _check_plans(passes, area, tles, plans, fetch_plans, active_cache, diagnostics)
        if output is not None:
            if chosen == "csv":
                api.export_overpass_csv(passes, out_dir=output.parent, file_name=output.name)
            elif chosen == "html":
                api.export_live_tracker_html(
                    tles, area, out_path=output, passes=passes, timezone_name=timezone
                )
            else:
                api.export_overpass_footprints(passes, output)
    if command == "next":
        typer.echo(
            f"{len(passes)} overpass(es) · {passes.attrs['roi_name']} · {passes.attrs['timezone']}"
        )
        typer.echo(
            f"Window (UTC): {passes.attrs['window_start_utc']} to {passes.attrs['window_end_utc']}"
        )
        if passes.empty:
            typer.echo("No passes meet the selected window and filters.")
        else:
            typer.echo(api.summarise_overpasses(passes).to_string(index=False))


@app.command("next", cls=_SatelliteCommand)
def next_command(
    point: Annotated[
        tuple[float, float] | None, typer.Option(help="WGS84 longitude latitude.")
    ] = None,
    bbox: Annotated[
        tuple[float, float, float, float] | None, typer.Option(help="WGS84 west south east north.")
    ] = None,
    roi: Annotated[
        Path | None, typer.Option(exists=True, dir_okay=False, help="ROI vector file.")
    ] = None,
    days: Annotated[float, typer.Option(help="Days to search from --start; default: 14.")] = 14,
    start: Annotated[
        str | None, typer.Option(help="ISO-8601 start; defaults to now (UTC).")
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Optional CSV, GeoJSON, GeoPackage or HTML export."),
    ] = None,
    satellite: Satellite = None,
    timezone: Timezone = None,
    tle_file: TLEFile = None,
    cache_dir: Cache = None,
    no_cache: NoCache = False,
    offline: Annotated[bool, typer.Option(help="Use supplied TLEs or cache; no network.")] = False,
    min_coverage: MinCoverage = 0,
    min_sun_elevation: MinSun = 0,
    all_passes: AllPasses = False,
    plans: Plans = None,
    fetch_plans: FetchPlans = False,
) -> None:
    """Find upcoming passes for exactly one point, bounding box or ROI file."""
    with _errors("next"):
        if sum(value is not None for value in (point, bbox, roi)) != 1:
            raise ValueError(
                "Choose exactly one of --point LON LAT, --bbox WEST SOUTH EAST NORTH, or --roi FILE."
            )
        if not math.isfinite(days) or days * 86400 < 1:
            raise ValueError("--days must be finite and cover at least one second.")
        start_ts = (
            _to_utc_timestamp(start)
            if start is not None
            else pd.Timestamp.now(tz="UTC").floor("min")
        )
        end_ts = start_ts + pd.Timedelta(days=days)
        _run_prediction(
            "next",
            point if point is not None else bbox if bbox is not None else roi,
            start_ts.isoformat(),
            end_ts.isoformat(),
            output,
            satellite,
            timezone,
            tle_file,
            cache_dir,
            no_cache,
            offline,
            min_coverage,
            min_sun_elevation,
            all_passes,
            plans,
            fetch_plans,
        )


@app.command("predict", cls=_SatelliteCommand)
def predict_command(
    roi: ROI,
    start: Start,
    end: End,
    output: Output,
    satellite: Satellite = None,
    timezone: Timezone = None,
    tle_file: TLEFile = None,
    cache_dir: Cache = None,
    no_cache: NoCache = False,
    offline: Annotated[bool, typer.Option(help="Use supplied TLEs or cache; no network.")] = False,
    format: Annotated[
        str | None, typer.Option("--format", help="csv, geojson, gpkg or html; infer from suffix.")
    ] = None,
    min_coverage: MinCoverage = 0,
    min_sun_elevation: MinSun = 0,
    all_passes: AllPasses = False,
    plans: Plans = None,
    fetch_plans: FetchPlans = False,
) -> None:
    """Predict ROI coverage and export a report, footprints or HTML planner."""
    with _errors("predict"):
        _run_prediction(
            "predict",
            roi,
            start,
            end,
            output,
            satellite,
            timezone,
            tle_file,
            cache_dir,
            no_cache,
            offline,
            min_coverage,
            min_sun_elevation,
            all_passes,
            plans,
            fetch_plans,
            format,
        )


@app.command("tracker", cls=_SatelliteCommand)
def tracker_command(
    roi: ROI,
    start: Start,
    end: End,
    output: Output,
    satellite: Satellite = None,
    timezone: Timezone = None,
    tle_file: TLEFile = None,
    cache_dir: Cache = None,
    no_cache: NoCache = False,
    offline: Annotated[bool, typer.Option(help="Never access the network.")] = False,
    min_coverage: MinCoverage = 0,
    min_sun_elevation: MinSun = 0,
    all_passes: AllPasses = False,
    plans: Plans = None,
    fetch_plans: FetchPlans = False,
) -> None:
    """Export a standalone HTML tracker and interactive observation planner."""
    with _errors("tracker"):
        _run_prediction(
            "tracker",
            roi,
            start,
            end,
            output,
            satellite,
            timezone,
            tle_file,
            cache_dir,
            no_cache,
            offline,
            min_coverage,
            min_sun_elevation,
            all_passes,
            plans,
            fetch_plans,
        )


@app.command("satellites", cls=TyperCommand)
def satellites_command(
    mission: Annotated[str | None, typer.Option(help="Landsat or Sentinel-2.")] = None,
) -> None:
    """List supported satellites and nominal instrument characteristics."""
    with _errors("satellites"):
        table = api.list_satellites(mission)
        typer.echo(
            table[["satellite", "alias", "norad_id", "mission", "sensor", "swath_km"]].to_string(
                index=False
            )
        )


cache_app = typer.Typer(no_args_is_help=True, help="Inspect or clear reusable downloads.")
app.add_typer(cache_app, name="cache")


@cache_app.command("info", cls=TyperCommand)
def cache_info_command(cache_dir: Cache = None) -> None:
    """Show cache location and download sizes without creating files."""
    with _errors("cache info"):
        root = cache_root(cache_dir)
        files = cache_files(root)
        size = sum(path.stat().st_size for path in files)
        typer.echo(f"Cache: {root}\n{len(files)} download file(s), {size / 1024**2:.2f} MiB")
        typer.echo("Default ESA retention: 30 days / 50 MiB; active plans are retained.")


@cache_app.command("clear", cls=TyperCommand)
def cache_clear_command(cache_dir: Cache = None) -> None:
    """Delete recognised downloads; preserve unrelated files and user exports."""
    with _errors("cache clear"):
        root = cache_root(cache_dir)
        count, size = clear_cache(root)
        typer.echo(f"Cleared {count} download file(s), {size / 1024**2:.2f} MiB from {root}")


if __name__ == "__main__":
    app()
