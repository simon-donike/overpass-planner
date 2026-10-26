"""Locations and lifecycle of disposable downloads; user exports are never managed here."""

import re
import tempfile
import time
import warnings
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from platformdirs import user_cache_path

from ..core.orbits import SATELLITE_CATALOGUE

PLAN_MAX_AGE_DAYS = 30
PLAN_MAX_BYTES = 50 * 1024 * 1024
_PLAN_NAME = re.compile(
    r"(?:s2[abc]_mp_acq__kml|s1[cd]_mp_user)_\d{8}t\d{6}_\d{8}t\d{6}\.(?:kml|kmz)",
    re.I,
)
_TLE_NAMES = {f"{record['norad_id']}.tle" for record in SATELLITE_CATALOGUE.values()}


def cache_root(directory: str | Path | None = None) -> Path:
    """Resolve a cache root without creating it (honours platformdirs/XDG settings)."""
    return Path(directory) if directory is not None else user_cache_path("eo-overpass-predictor")


@contextmanager
def cache_session(directory: Path | None, no_cache: bool) -> Iterator[Path | None]:
    """Use normal caches or a temporary root removed on context exit, including errors."""
    if no_cache and directory is not None:
        raise ValueError("--no-cache cannot be used with --cache-dir.")
    if no_cache:
        with tempfile.TemporaryDirectory(prefix="eo-overpass-") as temporary:
            yield Path(temporary)
    else:
        yield directory


def write_cache_file(path: Path, content: bytes) -> None:
    """Replace a cache file atomically, removing unfinished writes on ordinary errors."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".eo-overpass-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def cache_files(root: Path) -> list[Path]:
    """Recognise only our download filenames, never following file/directory symlinks."""
    files = []
    for category in ("tle", "plans"):
        directory = root / category
        if directory.is_symlink() or not directory.is_dir():
            continue
        for path in directory.iterdir():
            owned = (
                path.name in _TLE_NAMES
                if category == "tle"
                else _PLAN_NAME.fullmatch(path.name) is not None
            )
            owned = owned or (path.name.startswith(".eo-overpass-") and path.suffix == ".tmp")
            if owned and not path.is_symlink() and path.is_file():
                files.append(path)
    return sorted(files)


def clear_cache(root: Path) -> tuple[int, int]:
    """Remove recognised downloads only; preserve unrelated files and directories."""
    count = size = 0
    for path in cache_files(root):
        try:
            length = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            continue  # Another process may have cleared the same cache.
        count += 1
        size += length
    return count, size


def prune_plan_cache(
    root: Path,
    protected: Iterable[Path] = (),
    *,
    now: float | None = None,
    max_age_days: float = PLAN_MAX_AGE_DAYS,
    max_bytes: int = PLAN_MAX_BYTES,
) -> None:
    """Bound managed ESA downloads by age/size, retaining files needed by this request.

    TLEs remain available for stale/offline fallback. Active plans may temporarily
    exceed the budget. This runs only after online retrieval into the default cache.
    """
    current = time.time() if now is None else now
    keep = {path.absolute() for path in protected}
    try:
        entries = []
        for path in cache_files(root):
            stat = path.stat()
            if path.suffix == ".tmp":
                if current - stat.st_mtime > 86400:
                    path.unlink(missing_ok=True)
            elif path.parent.name == "plans":
                entries.append((stat.st_mtime, stat.st_size, path))
        total = sum(size for _, size, _ in entries)
        for modified, size, path in sorted(entries):
            if path.absolute() in keep:
                continue
            if current - modified > max_age_days * 86400 or total > max_bytes:
                path.unlink(missing_ok=True)
                total -= size
    except OSError as exc:
        warnings.warn(f"Could not clean download cache: {exc}", stacklevel=2)
