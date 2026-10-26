"""Predict a point ROI and export a CSV. Run from an installed environment."""

from eo_overpass_predictor import export_overpass_csv, fetch_tles, load_roi, predict_overpasses


def main() -> None:
    area = load_roi((144.96, -37.82), name="Melbourne")
    elements = fetch_tles(satellites=["S2B"])
    passes = predict_overpasses(area, elements, start_utc="2026-11-01", days=14)
    export_overpass_csv(passes, out_dir="OUTPUT", file_name="melbourne.csv")


if __name__ == "__main__":
    main()
