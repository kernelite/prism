import re
from typing import Literal, Optional, List
from pydantic import BaseModel, Field, model_validator

AccessMode = Literal["RW", "RO", "WO", "R", "W", "W1C", "UNKNOWN"]


def infer_bits_from_macro(mask_macro: str) -> Optional[str]:
    """Deterministically derives bit position/range from common Linux bitmask macros."""
    if not mask_macro:
        return None
    cleaned = mask_macro.strip()

    bit_match = re.search(r"\bBIT(?:_ULL)?\s*\(\s*(\d+)\s*\)", cleaned)
    if bit_match:
        return bit_match.group(1)

    genmask_match = re.search(r"\bGENMASK(?:_ULL)?\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", cleaned)
    if genmask_match:
        return f"{genmask_match.group(1)}:{genmask_match.group(2)}"

    shift_match = re.search(r"\(\s*1U?L{0,2}\s*<<\s*(\d+)\s*\)", cleaned)
    if shift_match:
        return shift_match.group(1)

    return None


class Bitfield(BaseModel):
    name: str = Field(..., description="C macro or logical identifier for the bitfield")
    bits: Optional[str] = Field(
        default=None,
        description="Bit index or inclusive MSB:LSB range (e.g., '3' or '7:4')",
    )
    mask_macro: str = Field(
        ...,
        description="Raw C bitmask expression or hex mask (e.g., 'BIT(3)', 'GENMASK(7, 4)', '0x08')",
    )
    access: Optional[AccessMode] = Field(
        default=None,
        description="Bitfield-level access permissions if distinct from parent register",
    )
    reset_value: Optional[str] = Field(
        default=None,
        description="Default hardware reset value for this bitfield (e.g., '0x0')",
    )
    description: Optional[str] = Field(
        default=None,
        description="Hardware behavior controlled by this bitfield",
    )

    @model_validator(mode="after")
    def populate_bits_if_missing(self) -> "Bitfield":
        if not self.bits:
            self.bits = infer_bits_from_macro(self.mask_macro)
        return self


class RegisterMap(BaseModel):
    name: str = Field(..., description="Register macro name from C header/source (e.g., 'E1000_CTRL')")
    offset: str = Field(..., description="Hexadecimal byte offset from MMIO/BAR base (e.g., '0x0000')")
    offset_expression: Optional[str] = Field(
        default=None,
        description="Isolated C offset calculation formula if indexed/computed (e.g., '0x03800 + (i * 0x100)')",
    )
    width: int = Field(
        default=32,
        ge=8,
        le=512,
        description="Register bit width (8, 16, 32, 64, 128, 256)",
    )
    access: AccessMode = Field(
        default="RW",
        description="Register access mode: RW, RO, WO, R, W, W1C, or UNKNOWN",
    )
    reset_value: Optional[str] = Field(
        default=None,
        description="Hardware power-on reset value in hex (e.g., '0x00000000')",
    )
    description: Optional[str] = Field(
        default=None,
        description="Functional description of the hardware register",
    )
    bitfields: List[Bitfield] = Field(default_factory=list)


# Alias for schema clarity
RegisterDefinition = RegisterMap