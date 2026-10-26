"""Swath-aware Landsat and Sentinel-1/2 prediction. Original science: Julian Manning."""

from importlib.metadata import version

from .api import *  # noqa: F403
from .api import __all__ as __all__

__version__ = version("eo-overpass-predictor")
