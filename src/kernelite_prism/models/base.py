from typing import Optional, List
from pydantic import BaseModel, Field
from .metadata import DriverMetadata
from .matching import MatchingTables
from .interfaces import DriverInterfaces
from .lifecycle import DriverLifecycle
from .registers import RegisterMap

class DriverSpec(BaseModel):
    driver_name: str = Field(..., description="Canonical name of the driver module")
    metadata: DriverMetadata = Field(default_factory=DriverMetadata)
    matching_tables: MatchingTables = Field(default_factory=MatchingTables)
    interfaces: DriverInterfaces = Field(default_factory=DriverInterfaces)
    lifecycle: DriverLifecycle = Field(default_factory=DriverLifecycle)
    structs: List[str] = Field(default_factory=list, description="List of driver structs defined (e.g. platform_driver)")
    registers: List[RegisterMap] = Field(default_factory=list, description="Extracted register definitions and macros")