"""Reproduce a frozen example without requesting current orbital data."""

from pathlib import Path

from eo_overpass_predictor import fetch_tles, load_roi, predict_overpasses, summarise_overpasses


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    elements = fetch_tles(
        satellites=["S2B"],
        tle_text=(root / "tests/fixtures/satellites.tle").read_text(),
        tle_dir=root / "DATA/TLE",
        offline=True,
        verbose=False,
    )
    passes = predict_overpasses(
        load_roi((144.35, -38.35, 145.15, -37.80)),
        elements,
        start_utc="2026-10-26",
        days=1,
        timezone_name="Australia/Melbourne",
        verbose=False,
    )
    print(summarise_overpasses(passes).to_string(index=False))


if __name__ == "__main__":
    main()
