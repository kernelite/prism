from typing import Optional, List
from pydantic import BaseModel, Field

class ModuleParam(BaseModel):
    name: str
    type: str
    permissions: str
    description: Optional[str] = None

class DriverMetadata(BaseModel):
    author: Optional[str] = None
    license: Optional[str] = None
    description: Optional[str] = None
    parameters: List[ModuleParam] = Field(default_factory=list)