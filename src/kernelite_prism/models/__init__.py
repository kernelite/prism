"""Pydantic data models for extracted kernel driver specifications."""

from .base import DriverSpec
from .metadata import DriverMetadata, ModuleParam
from .matching import MatchingTables, DeviceMatch
from .interfaces import DriverInterfaces, SysfsAttribute, IoctlCommand
from .lifecycle import DriverLifecycle
from .registers import RegisterMap, Bitfield

__all__ = [
    "DriverSpec",
    "DriverMetadata",
    "ModuleParam",
    "MatchingTables",
    "DeviceMatch",
    "DriverInterfaces",
    "SysfsAttribute",
    "IoctlCommand",
    "DriverLifecycle",
    "RegisterMap",
    "Bitfield",
]