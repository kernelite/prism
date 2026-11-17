import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator

from .metadata import DriverMetadata
from .matching import MatchingTables
from .interfaces import DriverInterfaces
from .lifecycle import DriverLifecycle
from .registers import RegisterMap
from .hardware import (
    HardwareInfo,
    SequenceStep,
    RuntimeOperations,
    ConcurrencyLock,
    MigrationNotes,
)


class DriverSpec(BaseModel):
    device_name: str = Field(
        default="",
        description="Human-readable hardware controller or peripheral name",
    )
    driver_name: str = Field(..., description="Canonical name of the Linux driver module")
    compatibility: List[str] = Field(
        default_factory=list,
        description="Compatible DTB strings or hardware matching identifiers",
    )
    hardware_info: HardwareInfo = Field(
        default_factory=HardwareInfo,
        description="Bus, compatible strings, vendor/device IDs, and MMIO base regions",
    )
    metadata: DriverMetadata = Field(default_factory=DriverMetadata)
    matching_tables: MatchingTables = Field(default_factory=MatchingTables)
    interfaces: DriverInterfaces = Field(default_factory=DriverInterfaces)
    lifecycle: DriverLifecycle = Field(default_factory=DriverLifecycle)
    structs: List[str] = Field(
        default_factory=list,
        description="List of driver structs defined (e.g., 'platform_driver', 'pci_driver')",
    )
    register_map: List[RegisterMap] = Field(
        default_factory=list,
        description="Strictly typed hardware register map with offsets, access modes, and bitfields",
    )
    init_sequence: List[SequenceStep] = Field(
        default_factory=list,
        description="Chronologically ordered probe and hardware initialization sequence",
    )
    runtime_operations: RuntimeOperations = Field(
        default_factory=RuntimeOperations,
        description="Runtime I/O handlers, DMA configuration, and interrupt lifecycle",
    )
    concurrency_locks: List[ConcurrencyLock] = Field(
        default_factory=list,
        description="Linux spinlocks/mutexes translated into abstract concurrency locks",
    )
    migration_notes: MigrationNotes = Field(
        default_factory=MigrationNotes,
        description="Guidance for porting the driver to a bare-metal C++ microkernel",
    )

    @model_validator(mode="after")
    def synchronize_aliases(self) -> "DriverSpec":
        """Keeps overlapping hardware/lifecycle fields synchronized across schema sections."""
        if not self.device_name:
            self.device_name = self.driver_name

        # Synchronize compatibility <-> hardware_info.compatible_strings <-> matching_tables.of_device_id
        merged_compat: List[str] = []
        for item in (
            self.compatibility
            + self.hardware_info.compatible_strings
            + [m.compatible for m in self.matching_tables.of_device_id if m.compatible]
        ):
            if item and item not in merged_compat:
                merged_compat.append(item)

        self.compatibility = merged_compat
        self.hardware_info.compatible_strings = merged_compat

        # Synchronize probe/remove handlers between lifecycle and runtime_operations
        if self.lifecycle.probe_function and not self.runtime_operations.probe_handler:
            self.runtime_operations.probe_handler = self.lifecycle.probe_function
        elif self.runtime_operations.probe_handler and not self.lifecycle.probe_function:
            self.lifecycle.probe_function = self.runtime_operations.probe_handler

        if self.lifecycle.remove_function and not self.runtime_operations.remove_handler:
            self.runtime_operations.remove_handler = self.lifecycle.remove_function
        elif self.runtime_operations.remove_handler and not self.lifecycle.remove_function:
            self.lifecycle.remove_function = self.runtime_operations.remove_handler

        # Ensure init_sequence steps are sequentially numbered 1..N
        for idx, step in enumerate(self.init_sequence, start=1):
            step.step = idx

        return self

    @classmethod
    def draft_2020_12_schema(cls) -> Dict[str, Any]:
        """Generates a JSON Schema Draft 2020-12 compliant schema dictionary."""
        raw_schema = cls.model_json_schema()
        schema: Dict[str, Any] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://kernelite.dev/schemas/driver_spec.schema.json",
            **raw_schema,
        }
        return schema


def export_json_schema(indent: int = 2) -> str:
    """Serializes the canonical DriverSpec JSON Schema Draft 2020-12 to string."""
    return json.dumps(DriverSpec.draft_2020_12_schema(), indent=indent)