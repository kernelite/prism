from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

class DeviceMatch(BaseModel):
    compatible: Optional[str] = None
    vendor_id: Optional[str] = None
    device_id: Optional[str] = None
    raw_entry: Dict[str, Any] = Field(default_factory=dict)

class MatchingTables(BaseModel):
    of_device_id: List[DeviceMatch] = Field(default_factory=list)
    pci_device_id: List[DeviceMatch] = Field(default_factory=list)
    usb_device_id: List[DeviceMatch] = Field(default_factory=list)
    acpi_device_id: List[DeviceMatch] = Field(default_factory=list)