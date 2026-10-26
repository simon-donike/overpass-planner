"""Noninteractive command-line adapter for the public Python API."""

import json
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
from .io.cache import cache_files, cache_root, cache_session, clear_cache, prune_plan_cache
from .presentation import SCHEMA_VERSION, json_value, prediction_document
from .types import TLERecord

app = typer.Typer(
    no_args_is_help=True, help="Swath-aware Landsat and Sentinel overpass prediction."
)


def _emit_json(document: dict[str, Any]) -> None:
    typer.echo(json.dumps(json_value(document), allow_nan=False, ensure_ascii=False))


def _fail(message: str, code: int, command: str, json_output: bool) -> None:
    if json_output:
        _emit_json(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": False,
                "command": command,
                "error": {
                    "code": "invalid_input" if code == 2 else "operational_error",
                    "message": message,
                },
            }
        )
    typer.echo(f"{'Invalid input' if code == 2 else 'Error'}: {message}", err=True)
    raise typer.Exit(code)


class _MachineCommand(TyperCommand):
    """Include Click/Typer argument errors in the JSON contract."""

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        json_output = "--json" in args
        try:
            return super().parse_args(ctx, args)
        except Exception as exc:
            # Typer versions use either standalone or vendored Click exceptions.
            # Only translate usage errors; propagate unexpected programming errors.
            format_message = getattr(exc, "format_message", None)
            if json_output and getattr(exc, "exit_code", None) == 2 and callable(format_message):
                command = (
                    f"cache {ctx.info_name}"
                    if ctx.parent and ctx.parent.info_name == "cache"
                    else ctx.info_name
                )
                _fail(format_message(), 2, command, True)
            raise


class _SatelliteCommand(_MachineCommand):
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
            elif argument.startswith("-s") and not argument.startswith("--"):
                expanded.append(argument)
                collecting = True
            elif collecting and not argument.startswith("-"):
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
        help="Names, aliases or NORAD IDs; space/comma separated or repeated. "
        "S2 selects Sentinel-2A/B/C; S1 selects Sentinel-1C/D; LS selects Landsat 8/9. Default: all seven.",
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
S1Mode = Annotated[
    str, typer.Option("--s1-mode", case_sensitive=False, help="Sentinel-1 mode: IW or EW.")
]
Plans = Annotated[
    Path | None, typer.Option(exists=True, help="Local ESA KML/KMZ file or directory.")
]
FetchPlans = Annotated[
    bool, typer.Option(help="Explicitly fetch ESA plans and cross-check predictions.")
]


@contextmanager
def _errors(command: str, json_output: bool = False) -> Iterator[None]:
    try:
        yield
    except (ValueError, KeyError, ZoneInfoNotFoundError, ParseError, OverflowError) as exc:
        _fail(str(exc), 2, command, json_output)
    except (OSError, RuntimeError, requests.RequestException, ImportError) as exc:
        _fail(str(exc), 1, command, json_output)


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
    s1_mode: str = "IW",
    imaging_only: bool | None = None,
) -> tuple[gpd.GeoDataFrame, dict[str, TLERecord], gpd.GeoDataFrame]:
    start_ts, end_ts = _to_utc_timestamp(start), _to_utc_timestamp(end)
    s1_mode = str(s1_mode).upper()
    if s1_mode not in {"IW", "EW"}:
        raise ValueError("--s1-mode must be IW or EW.")
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
        s1_mode=s1_mode,
        imaging_only=imaging_only,
    )
    return area, tles, passes


def _check_plans(
    passes: gpd.GeoDataFrame,
    area: gpd.GeoDataFrame,
    tles: dict[str, TLERecord],
    plans: Path | None,
    fetch_plans: bool,
    cache_dir: Path | None,
    diagnostics: list[dict[str, str]],
) -> gpd.GeoDataFrame:
    if not plans and not fetch_plans:
        return passes
    plan_source: Path | list[Path] | None = plans
    if fetch_plans:
        plan_source = []
        # Share one directory across mission downloads. Passing it explicitly
        # prevents each mission adapter from pruning the other's fresh files.
        plan_dir = (cache_root() if cache_dir is None else cache_dir) / "plans"
        for mission, fetcher in (
            ("Sentinel-2", api.fetch_s2_acquisition_plans),
            ("Sentinel-1", api.fetch_s1_acquisition_plans),
        ):
            units = [name for name in tles if name.startswith(mission.upper())]
            if not units:
                continue
            try:
                plan_source.extend(
                    fetcher(
                        plan_dir=plan_dir,
                        start_utc=passes.attrs["window_start_utc"],
                        end_utc=passes.attrs["window_end_utc"],
                        satellites=units,
                        verbose=False,
                    )
                )
            except (requests.RequestException, RuntimeError, OSError) as exc:
                message = f"{mission} ESA plans unavailable: {exc}; using cached plans."
                diagnostics.append({"code": "plans_unavailable", "message": message})
                typer.echo(f"Warning: {message}", err=True)
                fallback_root = cache_root() if cache_dir is None else cache_dir
                if (fallback_root / "plans").is_dir():
                    prefix = "s1" if mission == "Sentinel-1" else "s2"
                    plan_source.extend(
                        sorted(
                            path
                            for path in (fallback_root / "plans").iterdir()
                            if path.is_file()
                            and path.suffix.lower() in {".kml", ".kmz"}
                            and path.name.lower().startswith(prefix)
                        )
                    )
        if cache_dir is None:
            prune_plan_cache(plan_dir.parent, protected=[Path(path) for path in plan_source])
        if not plan_source and plan_dir.is_dir():
            plan_source = plan_dir
    loaded = api.load_acquisition_plans(plan_source)
    return api.crosscheck_acquisition_plan(passes, loaded, area)


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


def _print_prediction(document: dict[str, Any]) -> None:
    request = document["request"]
    typer.echo(
        f"{document['count']} overpass(es) · {request['roi_name']} · {request['timezone']}\n"
        f"Window (UTC): {request['start_utc']} to {request['end_utc']}"
    )
    if not document["passes"]:
        typer.echo(
            "No passes meet the selected window and filters. Try a longer window or lower coverage threshold."
        )
    else:
        for row in document["passes"]:
            status = row["status"]
            typer.echo(
                f"\n{row['satellite']} · {row['overpass_local']} local\n"
                f"  UTC: {row['overpass_utc']} · Coverage: {row['roi_coverage_pct']:.1f}% "
                f"· Geometry: intersects\n"
                f"  {status['imaging_label']} · {status['acquisition_label']}\n"
                f"  TLE: {status['orbital_data']} ({abs(status['tle_age_days']):.1f} days from pass)"
            )
            if row.get("mission") == "Sentinel-1":
                incidence = row.get("incidence_angle_deg")
                incidence_text = (
                    f"{float(incidence):.1f}°"
                    if incidence is not None and not pd.isna(incidence)
                    else "n/a"
                )
                sar = (
                    f"  SAR: {row.get('s1_mode') or 'IW'} · {row.get('look_side') or 'right'}-looking"
                    f" · incidence {incidence_text}"
                )
                typer.echo(sar)
            plan_details = []
            for key, label in (
                ("esa_polarisation", "polarisation"),
                ("esa_relative_orbit", "relative orbit"),
                ("esa_plan_file", "plan"),
            ):
                value = row.get(key)
                if value is not None and not pd.isna(value):
                    plan_details.append(f"{label}: {value}")
            if plan_details:
                typer.echo("  ESA: " + " · ".join(plan_details))
            for warning in status["warnings"]:
                typer.echo(f"  Note: {warning['message']}")
        typer.echo(
            "\nExpected imaging and plan matches do not guarantee acquisition or available imagery."
        )
    if document["output"]:
        typer.echo(f"Saved: {document['output']}")


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
    json_output: bool,
    min_coverage: float = 0,
    min_sun_elevation: float = 0,
    all_passes: bool = False,
    plans: Path | None = None,
    fetch_plans: bool = False,
    format: str | None = None,
    s1_mode: str = "IW",
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
            s1_mode=s1_mode,
            imaging_only=not all_passes,
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
    document = prediction_document(
        command,
        area,
        tles,
        passes,
        {
            "min_coverage_pct": min_coverage,
            "min_sun_elevation_deg": min_sun_elevation,
            "daytime_descending_only": not all_passes,
            "offline": offline,
            "coarse_step_s": 30,
            "fine_step_s": 1,
            "plans": str(plans.resolve()) if plans else None,
            "fetch_plans": fetch_plans,
            "s1_mode": s1_mode.upper(),
            "imaging_only": not all_passes,
        },
        diagnostics,
        str(output.resolve()) if output else None,
    )
    if json_output:
        _emit_json(document)
    else:
        _print_prediction(document)


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
    s1_mode: S1Mode = "IW",
    json_output: JsonOutput = False,
) -> None:
    """Find upcoming passes for exactly one point, bounding box or ROI file."""
    with _errors("next", json_output):
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
            json_output,
            min_coverage,
            min_sun_elevation,
            all_passes,
            plans,
            fetch_plans,
            None,
            s1_mode,
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
    s1_mode: S1Mode = "IW",
    json_output: JsonOutput = False,
) -> None:
    """Predict ROI coverage and export a report, footprints or HTML planner."""
    with _errors("predict", json_output):
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
            json_output,
            min_coverage,
            min_sun_elevation,
            all_passes,
            plans,
            fetch_plans,
            format,
            s1_mode,
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
    s1_mode: S1Mode = "IW",
    json_output: JsonOutput = False,
) -> None:
    """Export a standalone HTML tracker and interactive observation planner."""
    with _errors("tracker", json_output):
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
            json_output,
            min_coverage,
            min_sun_elevation,
            all_passes,
            plans,
            fetch_plans,
            None,
            s1_mode,
        )


@app.command("satellites", cls=_MachineCommand)
def satellites_command(
    mission: Annotated[str | None, typer.Option(help="Landsat or Sentinel-2.")] = None,
    json_output: JsonOutput = False,
) -> None:
    """List supported satellites and nominal instrument characteristics."""
    with _errors("satellites", json_output):
        table = api.list_satellites(mission)
        if json_output:
            _emit_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "ok": True,
                    "command": "satellites",
                    "satellites": table.to_dict("records"),
                }
            )
        else:
            typer.echo(
                table[
                    ["satellite", "alias", "norad_id", "mission", "sensor", "swath_km"]
                ].to_string(index=False)
            )


cache_app = typer.Typer(no_args_is_help=True, help="Inspect or clear reusable downloads.")
app.add_typer(cache_app, name="cache")


@cache_app.command("info", cls=_MachineCommand)
def cache_info_command(cache_dir: Cache = None, json_output: JsonOutput = False) -> None:
    """Show cache location and download sizes without creating files."""
    with _errors("cache info", json_output):
        root = cache_root(cache_dir)
        files = cache_files(root)
        size = sum(path.stat().st_size for path in files)
        if json_output:
            _emit_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "ok": True,
                    "command": "cache info",
                    "path": str(root),
                    "files": len(files),
                    "bytes": size,
                }
            )
        else:
            typer.echo(f"Cache: {root}\n{len(files)} download file(s), {size / 1024**2:.2f} MiB")
            typer.echo("Default ESA retention: 30 days / 50 MiB; active plans are retained.")


@cache_app.command("clear", cls=_MachineCommand)
def cache_clear_command(cache_dir: Cache = None, json_output: JsonOutput = False) -> None:
    """Delete recognised downloads; preserve unrelated files and user exports."""
    with _errors("cache clear", json_output):
        root = cache_root(cache_dir)
        count, size = clear_cache(root)
        if json_output:
            _emit_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "ok": True,
                    "command": "cache clear",
                    "path": str(root),
                    "files_removed": count,
                    "bytes_removed": size,
                }
            )
        else:
            typer.echo(f"Cleared {count} download file(s), {size / 1024**2:.2f} MiB from {root}")


if __name__ == "__main__":
    app()
