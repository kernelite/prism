"""Pydantic data models for extracted kernel driver specifications."""

from .base import DriverSpec, export_json_schema
from .metadata import DriverMetadata, ModuleParam
from .matching import MatchingTables, DeviceMatch
from .interfaces import DriverInterfaces, SysfsAttribute, IoctlCommand
from .lifecycle import DriverLifecycle
from .registers import RegisterMap, RegisterDefinition, Bitfield
from .hardware import (
    BaseAddressRegion,
    HardwareInfo,
    SequenceStep,
    RuntimeOpMapping,
    DMASpec,
    InterruptLifecycle,
    RuntimeOperations,
    ConcurrencyLock,
    ApiMigrationMapping,
    MigrationNotes,
)

__all__ = [
    "DriverSpec",
    "export_json_schema",
    "DriverMetadata",
    "ModuleParam",
    "MatchingTables",
    "DeviceMatch",
    "DriverInterfaces",
    "SysfsAttribute",
    "IoctlCommand",
    "DriverLifecycle",
    "RegisterMap",
    "RegisterDefinition",
    "Bitfield",
    "BaseAddressRegion",
    "HardwareInfo",
    "SequenceStep",
    "RuntimeOpMapping",
    "DMASpec",
    "InterruptLifecycle",
    "RuntimeOperations",
    "ConcurrencyLock",
    "ApiMigrationMapping",
    "MigrationNotes",
]