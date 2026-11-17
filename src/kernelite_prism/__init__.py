"""Kernelite Prism: LLM-first Linux driver extraction tool."""

__version__ = "0.1.0"

from .models import DriverSpec
from .core import DriverExtractor, extract_driver_spec

__all__ = [
    "__version__",
    "DriverSpec",
    "DriverExtractor",
    "extract_driver_spec",
]