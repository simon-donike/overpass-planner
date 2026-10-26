"""ROI input adapters for files, GeoJSON, WKT and in-memory geometry."""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from shapely import wkt as shp_wkt
from shapely.geometry import Point, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union

from ..core.geometry import _local_equal_area


def load_roi(
    source: object,
    use_bbox: bool = False,
    buffer_km: float = 0.0,
    name: str | None = None,
) -> gpd.GeoDataFrame:
    """
    Load a Region of Interest from almost any input and return it as a single EPSG:4326 geometry.

    Accepted inputs:
        * Path / str to any vector file GeoPandas can read (any CRS — reprojected).
        * A GeoDataFrame or GeoSeries (any CRS).
        * A shapely geometry or WKT string (assumed EPSG:4326).
        * A GeoJSON geometry, Feature or FeatureCollection mapping (EPSG:4326).
        * ``(lon, lat)`` for a point ROI.
        * ``(min_lon, min_lat, max_lon, max_lat)`` for a bounding box.

    Multi-feature inputs are dissolved into one geometry. Invalid geometries are
    repaired. A point ROI is valid — the pass test then answers "is this point
    inside the swath" and coverage is reported as 0 or 100 %.

    Args:
        source: The ROI input (see above).
        use_bbox (bool, optional): Replace the geometry by its bounding box. Defaults to False.
        buffer_km (float, optional): Geodesic-ish buffer applied in a local equal-area
            projection, e.g. to catch scenes that only clip the ROI edge. Defaults to 0.
        name (str, optional): Label stored in the ``name`` column (used in output file names).

    Returns:
        gpd.GeoDataFrame: One row, EPSG:4326, columns ``name`` and ``geometry``.
    """

    if not np.isfinite(buffer_km) or buffer_km < 0:
        raise ValueError("buffer_km must be finite and nonnegative.")
    if isinstance(source, dict):
        kind = source.get("type")
        if kind == "FeatureCollection":
            features = source.get("features", [])
            if not features:
                raise ValueError("ROI FeatureCollection is empty.")
            gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
        else:
            geometry = source.get("geometry") if kind == "Feature" else source
            if geometry is None:
                raise ValueError("ROI feature has no geometry.")
            gdf = gpd.GeoDataFrame(geometry=[shape(geometry)], crs="EPSG:4326")
    elif isinstance(source, gpd.GeoDataFrame):
        gdf = source.copy()
    elif isinstance(source, gpd.GeoSeries):
        gdf = gpd.GeoDataFrame(geometry=source)
    elif isinstance(source, BaseGeometry):
        gdf = gpd.GeoDataFrame(geometry=[source], crs="EPSG:4326")
    elif (
        isinstance(source, (tuple, list))
        and len(source) in (2, 4)
        and all(isinstance(v, (int, float, np.floating)) for v in source)
    ):
        if not np.isfinite(source).all():
            raise ValueError("ROI coordinates must be finite.")
        if len(source) == 4 and (source[0] >= source[2] or source[1] >= source[3]):
            raise ValueError("ROI bounds must be ordered west, south, east, north.")
        geom = Point(source) if len(source) == 2 else box(*source)
        gdf = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
    elif isinstance(source, str) and re.match(
        r"^(?:POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON|GEOMETRYCOLLECTION)"
        r"\s*(?:(?:ZM|Z|M)\s*)?(?:\(|EMPTY\s*$)",
        source.strip(),
        re.IGNORECASE,
    ):
        gdf = gpd.GeoDataFrame(geometry=[shp_wkt.loads(source)], crs="EPSG:4326")
    else:
        path = Path(source)
        gdf = gpd.read_file(path)
        name = name or path.stem

    if gdf.crs is None:
        warnings.warn("ROI has no CRS; assuming EPSG:4326.")
        gdf = gdf.set_crs("EPSG:4326")
    gdf = gdf.to_crs("EPSG:4326")
    geom = shapely.make_valid(
        unary_union([g for g in gdf.geometry if g is not None and not g.is_empty])
    )
    if geom.is_empty:
        raise ValueError("ROI geometry is empty after cleaning.")

    coords = shapely.get_coordinates(geom)
    if (
        not np.isfinite(coords).all()
        or (np.abs(coords[:, 0]) > 180).any()
        or (np.abs(coords[:, 1]) > 90).any()
    ):
        raise ValueError("ROI must have finite WGS84 longitude/latitude coordinates.")

    if use_bbox:
        geom = box(*geom.bounds)
    if buffer_km:
        fwd, inv = _local_equal_area(geom)
        geom = transform(inv, transform(fwd, geom).buffer(buffer_km * 1000.0))
        if geom.is_empty:
            raise ValueError("ROI is empty after buffering.")

    return gpd.GeoDataFrame({"name": [name or "ROI"]}, geometry=[geom], crs="EPSG:4326")
