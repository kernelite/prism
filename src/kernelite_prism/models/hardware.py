from typing import Literal, Optional, List
from pydantic import BaseModel, Field

BusType = Literal["mmio", "pci", "platform", "i2c", "spi", "usb", "uart", "other"]
EndianType = Literal["little", "big", "configurable", "unknown"]
SequenceOpType = Literal[
    "Write Register",
    "Read & Poll",
    "Read Register",
    "Memory Allocate",
    "MMIO Map",
    "DMA Setup",
    "Interrupt Hook",
    "Clock/Reset Control",
    "Hardware Reset",
    "Register Device",
    "Lock Acquire",
    "Lock Release",
    "Other",
]
AbstractLockType = Literal[
    "IRQSafeSpinlock",
    "Spinlock",
    "SleepableMutex",
    "ReadWriteLock",
    "Atomic",
    "None",
]


class BaseAddressRegion(BaseModel):
    region_id: str = Field(..., description="BAR index, MMIO resource index, or I/O port base identifier")
    name: Optional[str] = Field(default=None, description="Logical name of the memory/IO region")
    base_offset: str = Field(default="0x0", description="Base address offset or BAR selector")
    size_hint: Optional[str] = Field(default=None, description="Region byte size or address span if known")
    description: Optional[str] = None


class HardwareInfo(BaseModel):
    bus_type: BusType = Field(default="mmio", description="Primary hardware interconnect bus type")
    compatible_strings: List[str] = Field(
        default_factory=list,
        description="Device Tree (DTB) compatible strings",
    )
    vendor_ids: List[str] = Field(default_factory=list, description="PCI/USB vendor IDs in hex")
    device_ids: List[str] = Field(default_factory=list, description="PCI/USB device IDs in hex")
    base_address_offsets: List[BaseAddressRegion] = Field(
        default_factory=list,
        description="MMIO BARs or platform memory resource regions",
    )
    endianness: EndianType = Field(default="little", description="Hardware register byte ordering")
    irq_lines: List[str] = Field(default_factory=list, description="Interrupt vectors or IRQ trigger modes")


class SequenceStep(BaseModel):
    step: int = Field(..., ge=1, description="1-based chronological order of execution")
    operation: SequenceOpType = Field(..., description="Categorized hardware or driver initialization operation")
    source_function: str = Field(..., description="Originating C function name (e.g., 'e1000_probe')")
    target_register: Optional[str] = Field(
        default=None,
        description="Target register macro name or hardware resource",
    )
    offset: Optional[str] = Field(
        default=None,
        description="Hexadecimal register offset if applicable",
    )
    value_or_mask: Optional[str] = Field(
        default=None,
        description="Written constant/bitmask, poll mask, DMA size, or IRQ flags",
    )
    poll_condition: Optional[str] = Field(
        default=None,
        description="Exit condition and timeout for 'Read & Poll' steps",
    )
    description: str = Field(..., description="Detailed explanation of what this step performs on hardware")


class RuntimeOpMapping(BaseModel):
    operation_name: str = Field(..., description="Logical operation (e.g., 'transmit', 'read', 'set_termios')")
    c_function: str = Field(..., description="Linux C callback implementation name")
    registers_touched: List[str] = Field(
        default_factory=list,
        description="Register macros read or written during this operation",
    )
    description: Optional[str] = None


class DMASpec(BaseModel):
    allocation_api: Optional[str] = Field(
        default=None,
        description="Linux DMA allocation API used (e.g., 'dma_alloc_coherent')",
    )
    address_mask_bits: Optional[int] = Field(
        default=None,
        description="DMA addressing bit width (e.g., 32 or 64)",
    )
    descriptor_rings: List[str] = Field(
        default_factory=list,
        description="TX/RX descriptor ring structures and base/head/tail registers",
    )
    coherency_notes: Optional[str] = Field(
        default=None,
        description="Memory barrier (`wmb`/`rmb`) and cache coherency requirements",
    )


class InterruptLifecycle(BaseModel):
    irq_handler: Optional[str] = Field(default=None, description="Primary top-half ISR C function name")
    threaded_handler: Optional[str] = Field(default=None, description="Bottom-half / NAPI poll / threaded ISR")
    registration_api: Optional[str] = Field(default=None, description="e.g., 'request_irq', 'devm_request_irq'")
    irq_flags: List[str] = Field(default_factory=list, description="e.g., ['IRQF_SHARED']")
    status_register: Optional[str] = Field(
        default=None,
        description="Register read to determine active interrupt causes",
    )
    ack_register: Optional[str] = Field(
        default=None,
        description="Register read or written to acknowledge/clear pending interrupts",
    )
    mask_register: Optional[str] = Field(
        default=None,
        description="Register used to enable/disable hardware interrupt masks",
    )
    ack_mechanism: Optional[str] = Field(
        default=None,
        description="Hardware acknowledgment behavior (e.g., 'Read-to-clear ICR', 'Write-1-to-clear INTERRUPT_ACK')",
    )
    flow_steps: List[SequenceStep] = Field(
        default_factory=list,
        description="Ordered steps executed during interrupt handling",
    )


class RuntimeOperations(BaseModel):
    probe_handler: Optional[str] = None
    remove_handler: Optional[str] = None
    read_handlers: List[RuntimeOpMapping] = Field(default_factory=list)
    write_handlers: List[RuntimeOpMapping] = Field(default_factory=list)
    transmit_handler: Optional[RuntimeOpMapping] = None
    receive_handler: Optional[RuntimeOpMapping] = None
    dma_setup: Optional[DMASpec] = None
    interrupt_lifecycle: InterruptLifecycle = Field(default_factory=InterruptLifecycle)


class ConcurrencyLock(BaseModel):
    linux_primitive: str = Field(..., description="Original Linux lock declaration or call (e.g., 'spin_lock_irqsave')")
    abstract_lock_type: AbstractLockType = Field(
        ...,
        description="Normalized hardware/OS concurrency primitive",
    )
    protected_resources: List[str] = Field(
        default_factory=list,
        description="Registers, rings, or driver state protected by this lock",
    )
    bare_metal_recommendation: str = Field(
        ...,
        description="Recommended bare-metal C++ synchronization implementation",
    )


class ApiMigrationMapping(BaseModel):
    linux_api: str = Field(..., description="Linux kernel function/macro (e.g., 'writel', 'devm_request_irq')")
    microkernel_equivalent: str = Field(
        ...,
        description="Bare-metal C++ microkernel primitive (e.g., 'MmioRegion::write32', 'IrqDispatcher::bind')",
    )
    notes: str = Field(..., description="Semantics, memory barriers, or ownership caveats")


class MigrationNotes(BaseModel):
    linux_to_microkernel_mappings: List[ApiMigrationMapping] = Field(default_factory=list)
    architectural_caveats: List[str] = Field(default_factory=list)