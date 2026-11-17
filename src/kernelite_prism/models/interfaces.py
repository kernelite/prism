from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class SysfsAttribute(BaseModel):
    name: str
    mode: str
    show_function: Optional[str] = None
    store_function: Optional[str] = None


class IoctlCommand(BaseModel):
    name: str
    dir: Optional[str] = None
    type: Optional[str] = None
    nr: Optional[str] = None
    size: Optional[str] = None


class DriverInterfaces(BaseModel):
    file_operations: Dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of file_operations struct fields (e.g., 'unlocked_ioctl', 'read') to C function names",
    )
    sysfs_attributes: List[SysfsAttribute] = Field(default_factory=list)
    procfs_entries: List[str] = Field(default_factory=list)
    ioctl_commands: List[IoctlCommand] = Field(default_factory=list)