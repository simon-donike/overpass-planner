"""Install a wheel into a clean environment and smoke-test it outside the checkout.

Usage: uv run python tests/validate_distribution.py dist/package.whl
Dependencies are fetched during installation; the installed API/CLI checks are offline.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    wheel = Path(sys.argv[1]).resolve()
    tests = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="eo-wheel-") as scratch:
        root = Path(scratch)
        environment = root / "venv"
        subprocess.run(["uv", "venv", "--python", sys.executable, str(environment)], check=True)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        cli = environment / ("Scripts/eo-overpass.exe" if os.name == "nt" else "bin/eo-overpass")
        subprocess.run(["uv", "pip", "install", "--python", str(python), str(wheel)], check=True)
        subprocess.run(
            [str(python), str(tests / "smoke_installed.py"), str(tests / "fixtures"), str(root)],
            cwd=root,
            check=True,
        )
        subprocess.run([str(cli), "--help"], cwd=root, check=True)
        subprocess.run(
            [str(cli), "cache", "info", "--cache-dir", str(root / "unused-cache")],
            cwd=root,
            check=True,
        )
        assert not (root / "unused-cache").exists()
        roi = root / "area.geojson"
        roi.write_text('{"type":"Point","coordinates":[144.96,-37.82]}')
        subprocess.run(
            [
                str(cli),
                "predict",
                "--roi",
                str(roi),
                "--start",
                "2026-10-26",
                "--end",
                "2026-10-27",
                "--satellite",
                "S2B",
                "--tle-file",
                str(tests / "fixtures" / "satellites.tle"),
                "--offline",
                "--no-cache",
                "--output",
                str(root / "cli.csv"),
            ],
            cwd=root,
            check=True,
        )


if __name__ == "__main__":
    main()
