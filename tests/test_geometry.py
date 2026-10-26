import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon, box, mapping

from eo_overpass_predictor import load_roi, resolve_roi_timezone


@pytest.mark.parametrize(
    "source",
    [
        (144.96, -37.82),
        Point(144.96, -37.82),
        "POINT (144.96 -37.82)",
        {"type": "Point", "coordinates": [144.96, -37.82]},
        {"type": "Feature", "properties": {}, "geometry": mapping(Point(144.96, -37.82))},
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": mapping(Point(144.96, -37.82))}
            ],
        },
        gpd.GeoSeries([Point(144.96, -37.82)], crs=4326),
        gpd.GeoDataFrame(geometry=[Point(144.96, -37.82)], crs=4326).to_crs(32755),
    ],
)
def test_roi_representations(source):
    frame = load_roi(source, name="Melbourne")
    assert len(frame) == 1
    assert frame.crs.to_epsg() == 4326
    assert frame.iloc[0]["name"] == "Melbourne"
    assert frame.geometry.iloc[0].distance(Point(144.96, -37.82)) < 1e-10


def test_roi_file_reprojection_and_dissolve(tmp_path):
    # A filename starting with POINT is a path, rather than a WKT geometry.
    point_path = tmp_path / "points.geojson"
    point_path.write_text('{"type":"Point","coordinates":[144.96,-37.82]}')
    assert load_roi(str(point_path)).geometry.iloc[0].equals(Point(144.96, -37.82))
    path = tmp_path / "area.gpkg"
    gpd.GeoDataFrame(geometry=[box(144, -38, 145, -37), box(145, -38, 146, -37)], crs=4326).to_crs(
        32755
    ).to_file(path)
    roi = load_roi(path)
    assert roi.geometry.iloc[0].area == pytest.approx(2, abs=1e-9)
    assert roi["name"].iloc[0] == "area"


def test_missing_crs_warns_and_geometry_is_repaired():
    invalid = Polygon([(144, -38), (145, -37), (144, -37), (145, -38), (144, -38)])
    with pytest.warns(UserWarning, match="no CRS"):
        roi = load_roi(gpd.GeoDataFrame(geometry=[invalid]))
    assert roi.geometry.iloc[0].is_valid
    assert not roi.geometry.iloc[0].is_empty


def test_bbox_and_metric_buffer():
    assert load_roi("MULTIPOINT ((144 -38), (145 -37))").geometry.iloc[0].geom_type == "MultiPoint"
    shape = Polygon([(144, -38), (145, -38), (144, -37)])
    assert load_roi(shape, use_bbox=True).geometry.iloc[0].equals(box(144, -38, 145, -37))
    buffered = load_roi((144.96, -37.82), buffer_km=1)
    assert buffered.geometry.iloc[0].geom_type == "Polygon"
    assert buffered.to_crs(32755).geometry.area.iloc[0] == pytest.approx(3.14e6, rel=0.01)


@pytest.mark.parametrize(
    "source",
    [
        Point(),
        (144, 91),
        (181, 0),
        (float("nan"), 0),
        (145, -38, 144, -37),
        {"type": "FeatureCollection", "features": []},
        {"type": "Feature", "geometry": None},
        gpd.GeoDataFrame(geometry=[], crs=4326),
    ],
)
def test_invalid_or_empty_roi(source):
    with pytest.raises(ValueError):
        load_roi(source)


def test_invalid_buffer():
    with pytest.raises(ValueError):
        load_roi((0, 0), buffer_km=-1)


def test_timezone_lookup_and_override():
    area = load_roi((144.96, -37.82))
    assert resolve_roi_timezone(area, verbose=False) == "Australia/Melbourne"
    assert resolve_roi_timezone(area, override="UTC", verbose=False) == "UTC"
    assert resolve_roi_timezone(load_roi((-145, 0)), verbose=False).startswith("Etc/GMT")
