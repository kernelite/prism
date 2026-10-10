from typing import Optional, List
from pydantic import BaseModel, Field

class Bitfield(BaseModel):
    name: str
    mask_macro: str  # e.g., "BIT(3)" or "GENMASK(7, 4)"
    description: Optional[str] = None

class RegisterMap(BaseModel):
    name: str
    offset: str  # e.g., "0x04"
    description: Optional[str] = None
    bitfields: List[Bitfield] = Field(default_factory=list)