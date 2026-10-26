import numpy as np

from eo_overpass_predictor.core.sensors import (
    _ray_to_ellipsoid,
    sensor_config,
    sentinel1_footprint_edges,
    sentinel1_look_angles,
)


def test_sentinel1_off_nadir_envelopes_interpolate_by_altitude():
    near, far = sentinel1_look_angles(712.0, "IW")
    assert near == 26.765
    assert far == 39.64
    assert sentinel1_look_angles(700.0, "EW")[0] > 17.0


def test_sentinel1_ray_edges_are_right_looking_and_have_incidence():
    near, far, looks, incidences = sentinel1_footprint_edges(
        np.array([7078.0, 0.0, 0.0]), np.array([0.0, 7.5, 0.0]), 700.0, "IW"
    )
    assert near[1] < 0 and far[1] < 0
    assert looks[0] < looks[1]
    assert 0 < incidences[0] < incidences[1] < 90


def test_sentinel1_rays_are_zero_doppler_and_on_wgs84():
    position = np.array([7078.0, 120.0, 80.0])
    velocity = np.array([0.15, 7.45, 0.85])
    near, far, *_ = sentinel1_footprint_edges(position, velocity, 700.0, "IW")
    nadir = -position / np.linalg.norm(position)
    nadir -= (
        np.dot(nadir, velocity / np.linalg.norm(velocity)) * velocity / np.linalg.norm(velocity)
    )
    nadir /= np.linalg.norm(nadir)
    right = np.cross(nadir, velocity / np.linalg.norm(velocity))
    right /= np.linalg.norm(right)
    ray = np.cos(np.deg2rad(27.4)) * nadir + np.sin(np.deg2rad(27.4)) * right
    hit = _ray_to_ellipsoid(position, ray)
    assert abs(float(np.dot(ray, velocity))) < 1e-10
    assert np.isclose(
        (hit[0] ** 2 + hit[1] ** 2) / 6378.137**2 + hit[2] ** 2 / 6356.752314245**2, 1.0
    )


def test_sensor_config_is_tracker_serialisable():
    config = sensor_config("S1", "EW")
    assert config["mode"] == "EW"
    assert config["look_side"] == "right"
    assert config["look_angle_limits_deg"]["near"] == [17.94, 16.36]
