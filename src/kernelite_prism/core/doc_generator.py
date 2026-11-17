import re
from pathlib import Path
from typing import Optional
from jinja2 import BaseLoader, Environment

from ..exceptions import DocumentationError
from ..models.base import DriverSpec

DRIVER_SPEC_MD_TEMPLATE = """# Hardware & Driver Specification: {{ spec.device_name or spec.driver_name }}

> **Linux Driver Module:** `{{ spec.driver_name }}` | **Bus Type:** `{{ spec.hardware_info.bus_type | upper }}` | **Endianness:** `{{ spec.hardware_info.endianness }}`

---

## 1. Summary & Hardware Identification

{{ spec.metadata.description or "Hardware peripheral and register specification extracted from Linux kernel driver source." }}

- **Driver Module:** `{{ spec.driver_name }}`
- **Device Name:** {{ spec.device_name or spec.driver_name }}
- **License / Author:** {{ spec.metadata.license or "Unknown" }}{% if spec.metadata.author %} — {{ spec.metadata.author }}{% endif %}
- **Primary Driver Structs:** {% if spec.structs %}`{{ spec.structs | join("`, `") }}`{% else %}_None extracted_{% endif %}
- **Probe / Remove Entry Points:** `{{ spec.lifecycle.probe_function or "N/A" }}` / `{{ spec.lifecycle.remove_function or "N/A" }}`

### Compatible Hardware Nodes & Device IDs

| Match Source | Identifier / Compatible String | Vendor ID | Device ID |
| :--- | :--- | :--- | :--- |
{% if spec.compatibility -%}
{% for compat in spec.compatibility -%}
| Device Tree (`of_device_id`) | `{{ compat }}` | — | — |
{% endfor -%}
{% endif -%}
{% for pci in spec.matching_tables.pci_device_id -%}
| PCI (`pci_device_id`) | `{{ pci.compatible or "PCI Device" }}` | `{{ pci.vendor_id or "*" }}` | `{{ pci.device_id or "*" }}` |
{% endfor -%}
{% if spec.hardware_info.vendor_ids or spec.hardware_info.device_ids -%}
{% for vid in spec.hardware_info.vendor_ids -%}
| Hardware Info ID | `{{ spec.device_name }}` | `{{ vid }}` | `{{ spec.hardware_info.device_ids[loop.index0] if loop.index0 < spec.hardware_info.device_ids|length else "*" }}` |
{% endfor -%}
{% endif -%}
{% if not spec.compatibility and not spec.matching_tables.pci_device_id and not spec.hardware_info.vendor_ids -%}
| Generic / Early Console | `{{ spec.driver_name }}` | — | — |
{% endif %}

{% if spec.hardware_info.base_address_offsets %}
### MMIO / BAR Base Address Regions

| Region ID | Name | Base Offset | Size Hint | Description |
| :--- | :--- | :--- | :--- | :--- |
{% for region in spec.hardware_info.base_address_offsets -%}
| `{{ region.region_id }}` | {{ region.name or "MMIO" }} | `{{ region.base_offset }}` | {{ region.size_hint or "—" }} | {{ region.description or "—" }} |
{% endfor %}
{% endif %}

---

## 2. Register Memory Map

{% if spec.register_map %}
| Register Name | Offset | Offset Formula | Width | Access | Reset Value | Description |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
{% for reg in spec.register_map -%}
| `{{ reg.name }}` | `{{ reg.offset }}` | {% if reg.offset_expression %}`{{ reg.offset_expression }}`{% else %}—{% endif %} | {{ reg.width }}-bit | `{{ reg.access }}` | `{{ reg.reset_value or "0x0" }}` | {{ reg.description or "—" }} |
{% endfor %}

### Bitfield Definitions

{% for reg in spec.register_map %}
{% if reg.bitfields %}
#### `{{ reg.name }}` (`{{ reg.offset }}`)

| Bitfield | Bits | Mask / Macro | Access | Reset | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
{% for bf in reg.bitfields -%}
| `{{ bf.name }}` | `{{ bf.bits or "—" }}` | `{{ bf.mask_macro }}` | `{{ bf.access or reg.access }}` | `{{ bf.reset_value or "—" }}` | {{ bf.description or "—" }} |
{% endfor %}
{% endif %}
{% endfor %}
{% else %}
_No MMIO registers extracted._
{% endif %}

---

## 3. Sequence Diagrams

### 3.1 Initialization & Probe Lifecycle (`{{ spec.runtime_operations.probe_handler or spec.lifecycle.probe_function or "probe" }}`)

```mermaid
sequenceDiagram
    autonumber
    participant Kernel as Microkernel / Bus Manager
    participant Driver as {{ spec.driver_name | mermaid_id }} Driver
    participant HW as Hardware MMIO / IRQ

    Kernel->>Driver: invoke {{ (spec.runtime_operations.probe_handler or spec.lifecycle.probe_function or "probe") | mermaid_label }}()
    activate Driver
{% if spec.init_sequence %}
{% for step in spec.init_sequence %}
{% if step.operation == "Read & Poll" %}
    loop Poll until {{ (step.poll_condition or "status ready") | mermaid_label }}
        Driver->>HW: READ {{ (step.target_register or "STATUS") | mermaid_label }} ({{ (step.offset or "offset") | mermaid_label }})
        HW-->>Driver: register value ({{ (step.value_or_mask or "mask") | mermaid_label }})
    end
{% elif step.operation in ["Write Register", "Hardware Reset"] %}
    Driver->>HW: WRITE {{ (step.target_register or "REG") | mermaid_label }} [{{ (step.offset or "0x0") | mermaid_label }}] = {{ (step.value_or_mask or "val") | mermaid_label }}
{% elif step.operation == "Read Register" %}
    Driver->>HW: READ {{ (step.target_register or "REG") | mermaid_label }} [{{ (step.offset or "0x0") | mermaid_label }}]
    HW-->>Driver: value
{% elif step.operation == "Interrupt Hook" %}
    Driver->>Kernel: register_irq({{ (step.value_or_mask or spec.runtime_operations.interrupt_lifecycle.irq_handler or "irq_handler") | mermaid_label }})
{% elif step.operation in ["Memory Allocate", "DMA Setup", "MMIO Map"] %}
    Driver->>Kernel: {{ step.operation | mermaid_label }} ({{ (step.value_or_mask or step.description) | mermaid_label }})
{% else %}
    Note over Driver,HW: Step {{ step.step }}: {{ step.description | mermaid_label }}
{% endif %}
{% endfor %}
{% else %}
    Driver->>HW: Map MMIO BAR & initialize hardware registers
    Driver->>Kernel: Register IRQ handler & device interface
{% endif %}
    Driver-->>Kernel: probe complete (0)
    deactivate Driver
```

### 3.2 Interrupt Handling Lifecycle (`{{ spec.runtime_operations.interrupt_lifecycle.irq_handler or "irq_handler" }}`)

```mermaid
sequenceDiagram
    autonumber
    participant HW as Hardware Device
    participant IC as Interrupt Controller (APIC/GIC)
    participant ISR as {{ (spec.runtime_operations.interrupt_lifecycle.irq_handler or "irq_handler") | mermaid_label }}

    HW->>IC: Assert Hardware IRQ
    IC->>ISR: Dispatch Interrupt Vector
    activate ISR
{% if spec.runtime_operations.interrupt_lifecycle.flow_steps %}
{% for step in spec.runtime_operations.interrupt_lifecycle.flow_steps %}
{% if step.operation == "Read Register" %}
    ISR->>HW: READ {{ (step.target_register or spec.runtime_operations.interrupt_lifecycle.status_register or "IRQ_STATUS") | mermaid_label }} ({{ (step.offset or "status") | mermaid_label }})
    HW-->>ISR: interrupt cause flags ({{ (step.value_or_mask or "flags") | mermaid_label }})
{% elif step.operation == "Write Register" %}
    ISR->>HW: WRITE {{ (step.target_register or spec.runtime_operations.interrupt_lifecycle.ack_register or "IRQ_ACK") | mermaid_label }} = {{ (step.value_or_mask or "ack_mask") | mermaid_label }}
{% else %}
    Note over ISR,HW: {{ step.description | mermaid_label }}
{% endif %}
{% endfor %}
{% else %}
{% if spec.runtime_operations.interrupt_lifecycle.status_register %}
    ISR->>HW: READ {{ spec.runtime_operations.interrupt_lifecycle.status_register | mermaid_label }}
    HW-->>ISR: active IRQ status bits
{% endif %}
{% if spec.runtime_operations.interrupt_lifecycle.ack_register %}
    ISR->>HW: ACK via {{ spec.runtime_operations.interrupt_lifecycle.ack_register | mermaid_label }} ({{ (spec.runtime_operations.interrupt_lifecycle.ack_mechanism or "clear") | mermaid_label }})
{% endif %}
{% endif %}
    ISR-->>IC: IRQ_HANDLED (EOI)
    deactivate ISR
```

---

## 4. Runtime Operations, DMA & Concurrency Model

### 4.1 Concurrency & Locking Primitives

{% if spec.concurrency_locks %}
| Linux Primitive | Abstract Lock Type | Protected Resources | Bare-Metal C++ Recommendation |
| :--- | :--- | :--- | :--- |
{% for lock in spec.concurrency_locks -%}
| `{{ lock.linux_primitive }}` | `{{ lock.abstract_lock_type }}` | {{ lock.protected_resources | join(", ") if lock.protected_resources else "Driver state & MMIO" }} | {{ lock.bare_metal_recommendation }} |
{% endfor %}
{% else %}
_No explicit Linux spinlocks or mutexes detected in driver context._
{% endif %}

{% if spec.runtime_operations.dma_setup %}
### 4.2 DMA & Memory Coherency

- **Allocation API:** `{{ spec.runtime_operations.dma_setup.allocation_api or "N/A" }}`
- **DMA Address Mask:** {{ spec.runtime_operations.dma_setup.address_mask_bits or 32 }}-bit
- **Descriptor Rings:** {{ spec.runtime_operations.dma_setup.descriptor_rings | join("; ") if spec.runtime_operations.dma_setup.descriptor_rings else "None" }}
- **Memory Barriers & Coherency:** {{ spec.runtime_operations.dma_setup.coherency_notes or "Use explicit store/load barriers (`std::atomic_thread_fence`) before updating hardware tail pointers." }}
{% endif %}

---

## 5. C++ Microkernel Migration Notes

### 5.1 Linux Kernel to Bare-Metal C++ API Mapping

| Linux Kernel API | Bare-Metal C++ Microkernel Equivalent | Migration Notes |
| :--- | :--- | :--- |
{% if spec.migration_notes.linux_to_microkernel_mappings -%}
{% for mapping in spec.migration_notes.linux_to_microkernel_mappings -%}
| `{{ mapping.linux_api }}` | `{{ mapping.microkernel_equivalent }}` | {{ mapping.notes }} |
{% endfor -%}
{% else -%}
| `readl` / `writel` (`ioread32` / `iowrite32`) | `MmioRegion::read32(offset)` / `MmioRegion::write32(offset, val)` | Replace Linux I/O accessors with volatile MMIO accessors + explicit hardware memory barriers. |
| `devm_ioremap_resource` / `pci_iomap` | `Kernel::map_mmio(phys_addr, size)` | Map physical BAR/DTB address range into uncached (`UC` / `Device-nGnRnE`) virtual address space. |
| `request_irq` / `devm_request_irq` | `IrqDispatcher::register_handler(vector, isr)` | Acknowledge hardware IRQ status register directly inside top-half ISR before signaling EOI. |
| `spin_lock_irqsave` | `IrqSafeSpinlockGuard guard(lock_)` | Disable local CPU interrupts while holding the lock to prevent ISR deadlock. |
{% endif %}

{% if spec.migration_notes.architectural_caveats %}
### 5.2 Architectural Caveats

{% for caveat in spec.migration_notes.architectural_caveats -%}
- {{ caveat }}
{% endfor %}
{% endif %}

### 5.3 Generated C++20 Bare-Metal Driver Skeleton

```cpp
#pragma once

#include <cstdint>
#include <cstddef>

namespace microkernel::drivers::{{ spec.driver_name | cpp_ident }} {

namespace regs {
{% for reg in spec.register_map %}
    inline constexpr std::uintptr_t {{ reg.name | cpp_ident }} = {{ reg.offset }}; // {{ reg.width }}-bit {{ reg.access }}{% if reg.description %} - {{ reg.description }}{% endif %}
{% endfor %}
} // namespace regs

class {{ spec.driver_name | cpp_class }}Driver {
public:
    explicit {{ spec.driver_name | cpp_class }}Driver(std::uintptr_t mmio_base) noexcept
        : mmio_base_(mmio_base) {}

    bool init() noexcept {
        // Implement ordered initialization steps from Section 3.1 ({{ spec.init_sequence | length }} steps)
        return true;
    }

    void handle_irq() noexcept {
{% if spec.runtime_operations.interrupt_lifecycle.status_register %}
        const auto status = read32(regs::{{ spec.runtime_operations.interrupt_lifecycle.status_register | cpp_ident }});
        (void)status;
{% endif %}
{% if spec.runtime_operations.interrupt_lifecycle.ack_register %}
        // Acknowledge interrupt ({{ spec.runtime_operations.interrupt_lifecycle.ack_mechanism or "clear status" }})
        write32(regs::{{ spec.runtime_operations.interrupt_lifecycle.ack_register | cpp_ident }}, status);
{% endif %}
    }

private:
    [[nodiscard]] std::uint32_t read32(std::uintptr_t offset) const noexcept {
        return *reinterpret_cast<const volatile std::uint32_t*>(mmio_base_ + offset);
    }

    void write32(std::uintptr_t offset, std::uint32_t value) const noexcept {
        *reinterpret_cast<volatile std::uint32_t*>(mmio_base_ + offset) = value;
    }

    std::uintptr_t mmio_base_;
};

} // namespace microkernel::drivers::{{ spec.driver_name | cpp_ident }}
```
"""


def _sanitize_mermaid_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "Node"))
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"N_{cleaned}"
    return cleaned


def _sanitize_mermaid_label(value: str) -> str:
    cleaned = str(value or "").replace('"', "'").replace(";", ",").replace("#", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:96]


def _to_cpp_ident(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "driver"))
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"d_{cleaned}"
    return cleaned


def _to_cpp_class(value: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", str(value or "Driver"))
    pascal = "".join(p.capitalize() for p in parts if p)
    if not pascal or pascal[0].isdigit():
        pascal = f"Dev{pascal}"
    return pascal


def generate_markdown_spec(
    spec: DriverSpec,
    template_path: Optional[Path] = None,
) -> str:
    """Renders a validated DriverSpec instance into human-readable DRIVER_SPEC.md Markdown."""
    try:
        env = Environment(loader=BaseLoader(), autoescape=False, trim_blocks=True, lstrip_blocks=True)
        env.filters["mermaid_id"] = _sanitize_mermaid_id
        env.filters["mermaid_label"] = _sanitize_mermaid_label
        env.filters["cpp_ident"] = _to_cpp_ident
        env.filters["cpp_class"] = _to_cpp_class

        template_source = (
            template_path.read_text(encoding="utf-8")
            if template_path is not None
            else DRIVER_SPEC_MD_TEMPLATE
        )
        template = env.from_string(template_source)
        return template.render(spec=spec).strip() + "\n"
    except Exception as e:
        raise DocumentationError(f"Failed to generate Markdown documentation: {e}") from e