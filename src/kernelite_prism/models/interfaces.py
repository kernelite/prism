from typing import Optional, List
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
    file_operations: List[str] = Field(default_factory=list)
    sysfs_attributes: List[SysfsAttribute] = Field(default_factory=list)
    procfs_entries: List[str] = Field(default_factory=list)
    ioctl_commands: List[IoctlCommand] = Field(default_factory=list)