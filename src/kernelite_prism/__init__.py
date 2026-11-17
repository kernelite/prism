"""Kernelite Prism: AI-driven Linux driver extraction and C++ microkernel specification pipeline."""

from typing import Any

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "DriverSpec",
    "export_json_schema",
    "ASTPreProcessor",
    "parse_driver_ast",
    "DriverExtractor",
    "extract_driver_spec",
    "generate_markdown_spec",
    "run_full_verification",
]


def __getattr__(name: str) -> Any:
    if name in {"DriverSpec", "export_json_schema"}:
        from .models import DriverSpec, export_json_schema

        return {"DriverSpec": DriverSpec, "export_json_schema": export_json_schema}[name]
    if name in {
        "ASTPreProcessor",
        "parse_driver_ast",
        "DriverExtractor",
        "extract_driver_spec",
        "generate_markdown_spec",
        "run_full_verification",
    }:
        from . import core

        return getattr(core, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")