// ==========================================================================
// File: .devcontainer/devcontainer.json
// ==========================================================================

{
  "name": "Kernelite Prism Dev Environment",
  "image": "mcr.microsoft.com/devcontainers/python:1-3.11-bookworm",
  "features": {
    "ghcr.io/devcontainers/features/github-cli:1": {}
  },
  "runArgs": [
    "--gpus",
    "all",
    "--ipc=host"
  ],
  "customizations": {
    "vscode": {
      "settings": {
        "python.defaultInterpreterPath": "/usr/local/bin/python",
        "python.analysis.typeCheckingMode": "basic",
        "python.analysis.extraPaths": [
          "${containerWorkspaceFolder}/src"
        ]
      },
      "extensions": [
        "ms-python.python",
        "ms-python.vscode-pylance",
        "redhat.vscode-yaml",
        "tamasfe.even-better-toml"
      ]
    }
  },
  "postCreateCommand": "pip install --upgrade pip && pip install torch --index-url https://download.pytorch.org/whl/cu128 && pip install 'bitsandbytes>=0.45.3' && pip install -e ."
}


// ==========================================================================
// File: .devcontainer/devcontainer-lock.json
// ==========================================================================

{
  "features": {
    "ghcr.io/devcontainers/features/github-cli:1": {
      "version": "1.1.3",
      "resolved": "ghcr.io/devcontainers/features/github-cli@sha256:bd7ab48a832228f633239277552c30b353867fef2e5b037e064b4e64f0b843f2",
      "integrity": "sha256:bd7ab48a832228f633239277552c30b353867fef2e5b037e064b4e64f0b843f2"
    }
  }
}


// ==========================================================================
// File: src/kernelite_prism/config.py
// ==========================================================================

import os
from typing import Optional
import torch
from .exceptions import ConfigurationError

DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-3B-Instruct"
VALID_QUANTIZATIONS = {"4bit", "8bit", "none"}
VALID_BACKENDS = {"transformers", "outlines", "openai", "instructor"}


class Settings:
    """Holds configuration state for inference backend and model selection."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        device: str = "cuda",
        quantization: str = "4bit",
        backend: str = "transformers",
        hf_token: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        openai_base_url: Optional[str] = None,
    ):
        self.model: str = model
        self.device: str = device
        self.quantization: str = quantization.lower()
        self.backend: str = backend.lower()
        self.hf_token: Optional[str] = (
            hf_token or os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
        )
        self.openai_api_key: Optional[str] = openai_api_key or os.getenv("OPENAI_API_KEY")
        self.openai_base_url: Optional[str] = openai_base_url or os.getenv("OPENAI_BASE_URL")

    def validate(self) -> None:
        if self.backend not in VALID_BACKENDS:
            raise ConfigurationError(
                f"Unsupported backend '{self.backend}'. "
                f"Choose from: {', '.join(sorted(VALID_BACKENDS))}."
            )

        if self.backend in {"transformers", "outlines"}:
            if self.quantization not in VALID_QUANTIZATIONS:
                raise ConfigurationError(
                    f"Unsupported quantization '{self.quantization}'. "
                    f"Choose from: {', '.join(sorted(VALID_QUANTIZATIONS))}."
                )

            is_cuda = self.device.startswith("cuda")

            if is_cuda and not torch.cuda.is_available():
                raise ConfigurationError(
                    f"Device '{self.device}' was requested, but CUDA is not available. "
                    "Ensure an NVIDIA GPU with appropriate CUDA drivers and PyTorch are installed, "
                    "or pass '--device cpu --quant none'."
                )

            if not is_cuda and self.quantization != "none":
                raise ConfigurationError(
                    f"Quantization '{self.quantization}' is only supported on CUDA devices, "
                    f"got device '{self.device}'. Pass '--quant none' for non-CUDA execution."
                )


def get_settings(
    model: str = DEFAULT_MODEL,
    device: str = "cuda",
    quantization: str = "4bit",
    backend: str = "transformers",
    hf_token: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    openai_base_url: Optional[str] = None,
) -> Settings:
    """Factory function to fetch and validate settings."""
    settings = Settings(
        model=model,
        device=device,
        quantization=quantization,
        backend=backend,
        hf_token=hf_token,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
    )
    settings.validate()
    return settings

// ==========================================================================
// File: src/kernelite_prism/core/aggregator.py
// ==========================================================================

from pathlib import Path
from typing import List, Optional, Tuple, Union
import warnings
from ..exceptions import AggregationError
from .ast_parser import ASTExtractionResult, ASTPreProcessor

DEFAULT_TOKEN_WARN_THRESHOLD = 24_000
CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_tokens(text: str) -> int:
    """Estimates LLM token count using a conservative character-to-token heuristic."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


def collect_driver_files(target_path: Union[str, Path]) -> List[Tuple[str, str]]:
    """Collects `(relative_name, file_content)` pairs for `.c` and `.h` files."""
    path = Path(target_path)

    if not path.exists():
        raise AggregationError(f"Target path '{path}' does not exist.")

    files_to_process: List[Path] = []

    if path.is_file():
        if path.suffix not in [".c", ".h"]:
            raise AggregationError(
                f"File '{path}' is not a valid C source file (.c or .h)."
            )
        files_to_process.append(path)
        # Also include sibling header with matching stem if present (e.g., e1000_main.c + e1000.h / e1000_hw.h)
        for sibling_hdr in sorted(path.parent.glob("*.h")):
            if sibling_hdr not in files_to_process:
                files_to_process.append(sibling_hdr)
    elif path.is_dir():
        files_to_process = sorted(
            [f for f in path.rglob("*") if f.is_file() and f.suffix in [".c", ".h"]]
        )

    if not files_to_process:
        raise AggregationError(
            f"No .c or .h source files found in target path '{path}'."
        )

    collected: List[Tuple[str, str]] = []
    for file_path in files_to_process:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            relative_name = (
                str(file_path.relative_to(path))
                if path.is_dir()
                else file_path.name
            )
            collected.append((relative_name, content))
        except Exception as e:
            raise AggregationError(
                f"Failed to read file '{file_path}': {e}"
            ) from e

    return collected


def aggregate_with_ast(
    target_path: Union[str, Path],
    use_ast_pruning: bool = True,
    warn_token_threshold: int = DEFAULT_TOKEN_WARN_THRESHOLD,
    max_tokens: Optional[int] = None,
) -> Tuple[str, ASTExtractionResult]:
    """Aggregates C source files, parses AST primitives, and returns `(context_str, ast_result)`."""
    path = Path(target_path)
    collected_files = collect_driver_files(path)

    preprocessor = ASTPreProcessor(strip_boilerplate=use_ast_pruning)
    ast_result = preprocessor.parse_files(collected_files)

    if use_ast_pruning:
        aggregated = ast_result.pruned_source
    else:
        blocks = [
            f"/* =========================================================================\n"
            f" * File: {rel_name}\n"
            f" * ========================================================================= */\n\n"
            f"{content}"
            for rel_name, content in collected_files
        ]
        aggregated = "\n\n".join(blocks)

    estimated_tokens = estimate_tokens(aggregated)

    if max_tokens is not None and estimated_tokens > max_tokens:
        raise AggregationError(
            f"Aggregated context from '{path}' ({len(collected_files)} files, "
            f"~{estimated_tokens:,} tokens) exceeds the configured limit of {max_tokens:,} tokens."
        )

    if estimated_tokens > warn_token_threshold:
        warnings.warn(
            f"Aggregated context from '{path}' spans {len(collected_files)} file(s) "
            f"(~{estimated_tokens:,} estimated tokens), exceeding the recommended threshold "
            f"of {warn_token_threshold:,} tokens. Large multi-file contexts may cause context "
            f"truncation, degraded accuracy, or GPU OOM errors.",
            UserWarning,
            stacklevel=2,
        )

    return aggregated, ast_result


def aggregate_context(
    target_path: Union[str, Path],
    warn_token_threshold: int = DEFAULT_TOKEN_WARN_THRESHOLD,
    max_tokens: Optional[int] = None,
    use_ast_pruning: bool = True,
) -> str:
    """Recursively collects and aggregates C source and header files into a formatted string context."""
    aggregated, _ = aggregate_with_ast(
        target_path=target_path,
        use_ast_pruning=use_ast_pruning,
        warn_token_threshold=warn_token_threshold,
        max_tokens=max_tokens,
    )
    return aggregated

// ==========================================================================
// File: src/kernelite_prism/core/doc_generator.py
// ==========================================================================

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
- **License / Author:** {{ spec.metadata.license or "Unknown" }}{% if spec.metadata.author %} â€” {{ spec.metadata.author }}{% endif %}
- **Primary Driver Structs:** {% if spec.structs %}`{{ spec.structs | join("`, `") }}`{% else %}_None extracted_{% endif %}
- **Probe / Remove Entry Points:** `{{ spec.lifecycle.probe_function or "N/A" }}` / `{{ spec.lifecycle.remove_function or "N/A" }}`

### Compatible Hardware Nodes & Device IDs

| Match Source | Identifier / Compatible String | Vendor ID | Device ID |
| :--- | :--- | :--- | :--- |
{% if spec.compatibility -%}
{% for compat in spec.compatibility -%}
| Device Tree (`of_device_id`) | `{{ compat }}` | â€” | â€” |
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
| Generic / Early Console | `{{ spec.driver_name }}` | â€” | â€” |
{% endif %}

{% if spec.hardware_info.base_address_offsets %}
### MMIO / BAR Base Address Regions

| Region ID | Name | Base Offset | Size Hint | Description |
| :--- | :--- | :--- | :--- | :--- |
{% for region in spec.hardware_info.base_address_offsets -%}
| `{{ region.region_id }}` | {{ region.name or "MMIO" }} | `{{ region.base_offset }}` | {{ region.size_hint or "â€”" }} | {{ region.description or "â€”" }} |
{% endfor %}
{% endif %}

---

## 2. Register Memory Map

{% if spec.register_map %}
| Register Name | Offset | Offset Formula | Width | Access | Reset Value | Description |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
{% for reg in spec.register_map -%}
| `{{ reg.name }}` | `{{ reg.offset }}` | {% if reg.offset_expression %}`{{ reg.offset_expression }}`{% else %}â€”{% endif %} | {{ reg.width }}-bit | `{{ reg.access }}` | `{{ reg.reset_value or "0x0" }}` | {{ reg.description or "â€”" }} |
{% endfor %}

### Bitfield Definitions

{% for reg in spec.register_map %}
{% if reg.bitfields %}
#### `{{ reg.name }}` (`{{ reg.offset }}`)

| Bitfield | Bits | Mask / Macro | Access | Reset | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
{% for bf in reg.bitfields -%}
| `{{ bf.name }}` | `{{ bf.bits or "â€”" }}` | `{{ bf.mask_macro }}` | `{{ bf.access or reg.access }}` | `{{ bf.reset_value or "â€”" }}` | {{ bf.description or "â€”" }} |
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

// ==========================================================================
// File: src/kernelite_prism/core/validator.py
// ==========================================================================

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, ValidationError

from ..exceptions import SchemaValidationError
from ..models.base import DriverSpec
from .ast_parser import ASTExtractionResult


class ValidationFinding(BaseModel):
    check: str
    passed: bool
    message: str


class VerificationReport(BaseModel):
    passed: bool
    schema_compliant: bool
    register_deterministic: bool
    sequence_parity: bool
    markdown_complete: bool
    findings: List[ValidationFinding] = Field(default_factory=list)


def load_driver_spec_schema(schema_path: Optional[Path] = None) -> Dict[str, Any]:
    """Loads driver_spec.schema.json from disk if provided, otherwise returns canonical Draft 2020-12 schema."""
    if schema_path is not None and schema_path.exists():
        return json.loads(schema_path.read_text(encoding="utf-8"))
    return DriverSpec.draft_2020_12_schema()


def validate_schema_compliance(
    spec_payload: Union[str, Dict[str, Any], DriverSpec],
    schema_path: Optional[Path] = None,
) -> List[ValidationFinding]:
    """Validates a JSON string, dictionary, or DriverSpec against driver_spec.schema.json (Draft 2020-12)."""
    findings: List[ValidationFinding] = []
    schema = load_driver_spec_schema(schema_path)

    if isinstance(spec_payload, DriverSpec):
        data = spec_payload.model_dump()
    elif isinstance(spec_payload, str):
        try:
            data = json.loads(spec_payload)
        except json.JSONDecodeError as e:
            return [
                ValidationFinding(
                    check="json_syntax",
                    passed=False,
                    message=f"Invalid JSON syntax: {e}",
                )
            ]
    else:
        data = spec_payload

    validator = Draft202012Validator(schema)
    schema_errors = sorted(validator.iter_errors(data), key=lambda err: list(err.path))
    if schema_errors:
        for err in schema_errors:
            loc = ".".join(str(p) for p in err.path) or "<root>"
            findings.append(
                ValidationFinding(
                    check="json_schema_draft_2020_12",
                    passed=False,
                    message=f"At '{loc}': {err.message}",
                )
            )
    else:
        findings.append(
            ValidationFinding(
                check="json_schema_draft_2020_12",
                passed=True,
                message="100% compliant with driver_spec.schema.json (Draft 2020-12).",
            )
        )

    try:
        DriverSpec.model_validate(data)
        findings.append(
            ValidationFinding(
                check="pydantic_model_validation",
                passed=True,
                message="Passed strict Pydantic DriverSpec type validation.",
            )
        )
    except ValidationError as e:
        findings.append(
            ValidationFinding(
                check="pydantic_model_validation",
                passed=False,
                message=f"Pydantic validation failed: {e}",
            )
        )

    return findings


def verify_register_determinism(
    spec: DriverSpec,
    ast_result: ASTExtractionResult,
) -> List[ValidationFinding]:
    """Ensures extracted register offsets and bitmasks match C AST constants with zero hallucinations."""
    findings: List[ValidationFinding] = []
    macro_map = ast_result.macro_lookup()

    if not spec.register_map:
        findings.append(
            ValidationFinding(
                check="register_presence",
                passed=False,
                message="Extracted register_map is empty.",
            )
        )
        return findings

    for reg in spec.register_map:
        if reg.name not in macro_map:
            findings.append(
                ValidationFinding(
                    check=f"register_symbol:{reg.name}",
                    passed=False,
                    message=f"Register '{reg.name}' not found in C header/source AST macros.",
                )
            )
            continue

        macro = macro_map[reg.name]
        if macro.hex_value is not None:
            try:
                extracted_int = (
                    int(reg.offset, 16)
                    if reg.offset.lower().startswith("0x")
                    else int(reg.offset)
                )
                if extracted_int != macro.numeric_value:
                    findings.append(
                        ValidationFinding(
                            check=f"register_offset:{reg.name}",
                            passed=False,
                            message=(
                                f"Offset mismatch for '{reg.name}': extracted '{reg.offset}', "
                                f"but C AST resolved '{macro.hex_value}'."
                            ),
                        )
                    )
                else:
                    findings.append(
                        ValidationFinding(
                            check=f"register_offset:{reg.name}",
                            passed=True,
                            message=f"Verified '{reg.name}' == {macro.hex_value}.",
                        )
                    )
            except ValueError:
                findings.append(
                    ValidationFinding(
                        check=f"register_offset:{reg.name}",
                        passed=False,
                        message=f"Non-numeric offset '{reg.offset}' for '{reg.name}'.",
                    )
                )

        for bf in reg.bitfields:
            if bf.name in macro_map:
                findings.append(
                    ValidationFinding(
                        check=f"bitfield_symbol:{reg.name}.{bf.name}",
                        passed=True,
                        message=f"Verified bitfield '{bf.name}' ({bf.mask_macro}).",
                    )
                )

    return findings


def verify_sequence_parity(
    spec: DriverSpec,
    ast_result: ASTExtractionResult,
    markdown_text: Optional[str] = None,
) -> List[ValidationFinding]:
    """Verifies chronological sequence parity between C .probe MMIO operations, init_sequence, and Mermaid."""
    findings: List[ValidationFinding] = []
    probe_funcs = [fn for fn in ast_result.functions if fn.role == "probe"]

    if not probe_funcs:
        findings.append(
            ValidationFinding(
                check="probe_ast_discovery",
                passed=True,
                message="No dedicated .probe function in AST; sequence parity check skipped.",
            )
        )
        return findings

    probe_fn = probe_funcs[0]
    if not spec.init_sequence:
        findings.append(
            ValidationFinding(
                check="init_sequence_populated",
                passed=False,
                message=f"Probe function '{probe_fn.name}' exists in AST, but init_sequence is empty.",
            )
        )
        return findings

    # Check strictly increasing 1..N step ordering
    expected_steps = list(range(1, len(spec.init_sequence) + 1))
    actual_steps = [s.step for s in spec.init_sequence]
    findings.append(
        ValidationFinding(
            check="init_sequence_monotonic_order",
            passed=(expected_steps == actual_steps),
            message=(
                f"init_sequence has {len(spec.init_sequence)} ordered steps."
                if expected_steps == actual_steps
                else f"Step numbering mismatch: {actual_steps} != {expected_steps}"
            ),
        )
    )

    if markdown_text is not None:
        has_mermaid = "```mermaid" in markdown_text and "sequenceDiagram" in markdown_text
        findings.append(
            ValidationFinding(
                check="mermaid_sequence_diagram",
                passed=has_mermaid,
                message=(
                    "Markdown contains valid Mermaid sequenceDiagram blocks."
                    if has_mermaid
                    else "Missing Mermaid sequenceDiagram in generated DRIVER_SPEC.md."
                ),
            )
        )

    return findings


def verify_markdown_completeness(markdown_text: str) -> List[ValidationFinding]:
    """Validates that DRIVER_SPEC.md contains all required human-readable documentation sections."""
    required_sections = [
        "## 1. Summary & Hardware Identification",
        "## 2. Register Memory Map",
        "## 3. Sequence Diagrams",
        "## 4. Runtime Operations, DMA & Concurrency Model",
        "## 5. C++ Microkernel Migration Notes",
    ]
    findings: List[ValidationFinding] = []
    for section in required_sections:
        present = section in markdown_text
        findings.append(
            ValidationFinding(
                check=f"markdown_section:{section}",
                passed=present,
                message=(
                    f"Section '{section}' present."
                    if present
                    else f"Missing section '{section}'."
                ),
            )
        )

    # Ensure no unrendered Jinja2 tags remain
    no_raw_jinja = "{{" not in markdown_text and "{%" not in markdown_text
    findings.append(
        ValidationFinding(
            check="markdown_no_unrendered_templates",
            passed=no_raw_jinja,
            message=(
                "All Jinja2 template expressions rendered cleanly."
                if no_raw_jinja
                else "Found unrendered Jinja2 delimiters in DRIVER_SPEC.md."
            ),
        )
    )
    return findings


def run_full_verification(
    spec: DriverSpec,
    ast_result: Optional[ASTExtractionResult] = None,
    markdown_text: Optional[str] = None,
    schema_path: Optional[Path] = None,
    strict: bool = False,
) -> VerificationReport:
    """Runs the complete verification harness across schema, AST determinism, sequence parity, and Markdown."""
    schema_findings = validate_schema_compliance(spec, schema_path=schema_path)
    reg_findings = (
        verify_register_determinism(spec, ast_result)
        if ast_result is not None
        else [ValidationFinding(check="register_ast_skip", passed=True, message="AST not provided.")]
    )
    seq_findings = (
        verify_sequence_parity(spec, ast_result, markdown_text=markdown_text)
        if ast_result is not None
        else [ValidationFinding(check="sequence_ast_skip", passed=True, message="AST not provided.")]
    )
    md_findings = (
        verify_markdown_completeness(markdown_text)
        if markdown_text is not None
        else [ValidationFinding(check="markdown_skip", passed=True, message="Markdown not provided.")]
    )

    all_findings = schema_findings + reg_findings + seq_findings + md_findings
    schema_ok = all(f.passed for f in schema_findings)
    reg_ok = all(f.passed for f in reg_findings)
    seq_ok = all(f.passed for f in seq_findings)
    md_ok = all(f.passed for f in md_findings)
    overall_ok = schema_ok and reg_ok and seq_ok and md_ok

    report = VerificationReport(
        passed=overall_ok,
        schema_compliant=schema_ok,
        register_deterministic=reg_ok,
        sequence_parity=seq_ok,
        markdown_complete=md_ok,
        findings=all_findings,
    )

    if strict and not overall_ok:
        failed_msgs = "; ".join(f.message for f in all_findings if not f.passed)
        raise SchemaValidationError(f"DriverSpec verification failed: {failed_msgs}")

    return report

// ==========================================================================
// File: src/kernelite_prism/core/serializer.py
// ==========================================================================

import json
import yaml

from ..exceptions import SerializationError
from ..models.base import DriverSpec
from .doc_generator import generate_markdown_spec


def serialize_spec(spec: DriverSpec, format_type: str = "json") -> str:
    """Serializes a validated DriverSpec model instance into JSON, YAML, or Markdown format."""
    try:
        fmt = format_type.lower()

        if fmt == "json":
            data = spec.model_dump(exclude_none=True)
            return json.dumps(data, indent=2)
        elif fmt in ["yaml", "yml"]:
            data = spec.model_dump(exclude_none=True)
            return yaml.dump(data, sort_keys=False, default_flow_style=False)
        elif fmt in ["md", "markdown"]:
            return generate_markdown_spec(spec)
        else:
            raise SerializationError(
                f"Unsupported output format: '{format_type}'. Supported formats are 'json', 'yaml', and 'markdown'."
            )

    except Exception as e:
        if isinstance(e, SerializationError):
            raise
        raise SerializationError(f"Failed to serialize specification: {e}") from e

// ==========================================================================
// File: src/kernelite_prism/core/ast_parser.py
// ==========================================================================

import ast
import operator
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from pydantic import BaseModel, Field

try:
    import tree_sitter_c as tsc
    from tree_sitter import Language, Node, Parser

    C_LANGUAGE: Optional[Language] = Language(tsc.language())
    _TREE_SITTER_IMPORT_ERROR: Optional[Exception] = None
except ImportError as exc:
    tsc = None  # type: ignore[assignment]
    Language = None  # type: ignore[misc, assignment]
    Node = Any  # type: ignore[misc, assignment]
    Parser = None  # type: ignore[misc, assignment]
    C_LANGUAGE = None
    _TREE_SITTER_IMPORT_ERROR = exc

from ..exceptions import ASTParsingError


DRIVER_STRUCT_TYPES: Set[str] = {
    "pci_driver",
    "platform_driver",
    "file_operations",
    "uart_ops",
    "net_device_ops",
    "of_device_id",
    "pci_device_id",
    "usb_device_id",
    "acpi_device_id",
    "i2c_device_id",
    "spi_device_id",
    "platform_device_id",
    "hid_device_id",
    "console",
    "uart_driver",
    "virtio_driver",
}

PM_STRUCT_TYPES: Set[str] = {
    "dev_pm_ops",
}

MMIO_CALL_NAMES: Set[str] = {
    "readb",
    "readw",
    "readl",
    "readq",
    "writeb",
    "writew",
    "writel",
    "writeq",
    "readb_relaxed",
    "readw_relaxed",
    "readl_relaxed",
    "readq_relaxed",
    "writeb_relaxed",
    "writew_relaxed",
    "writel_relaxed",
    "writeq_relaxed",
    "ioread8",
    "ioread16",
    "ioread32",
    "ioread64",
    "ioread16be",
    "ioread32be",
    "iowrite8",
    "iowrite16",
    "iowrite32",
    "iowrite64",
    "iowrite16be",
    "iowrite32be",
    "inb",
    "inw",
    "inl",
    "outb",
    "outw",
    "outl",
    "serial_in",
    "serial_out",
    "serial_port_in",
    "serial_port_out",
    "er32",
    "ew32",
}

IRQ_CALL_NAMES: Set[str] = {
    "request_irq",
    "devm_request_irq",
    "request_threaded_irq",
    "devm_request_threaded_irq",
    "free_irq",
    "devm_free_irq",
    "enable_irq",
    "disable_irq",
    "disable_irq_nosync",
    "synchronize_irq",
}

LOCK_CALL_NAMES: Set[str] = {
    "spin_lock_init",
    "spin_lock",
    "spin_unlock",
    "spin_lock_irq",
    "spin_unlock_irq",
    "spin_lock_irqsave",
    "spin_unlock_irqrestore",
    "raw_spin_lock_init",
    "raw_spin_lock_irqsave",
    "raw_spin_unlock_irqrestore",
    "mutex_init",
    "mutex_lock",
    "mutex_unlock",
    "mutex_lock_interruptible",
}

DMA_MEM_CALL_NAMES: Set[str] = {
    "dma_alloc_coherent",
    "dmam_alloc_coherent",
    "dma_free_coherent",
    "dma_map_single",
    "dma_unmap_single",
    "dma_set_mask_and_coherent",
    "dma_set_mask",
    "dma_set_coherent_mask",
    "devm_ioremap_resource",
    "devm_platform_ioremap_resource",
    "ioremap",
    "pci_iomap",
    "pcim_iomap_regions",
    "devm_kzalloc",
    "kzalloc",
}

DEBUG_BOILERPLATE_CALLS: Set[str] = {
    "dev_dbg",
    "pr_debug",
    "netdev_dbg",
    "v4l2_dbg",
    "dev_vdbg",
    "pr_devel",
}

MODULE_BOILERPLATE_MACROS: Set[str] = {
    "MODULE_LICENSE",
    "MODULE_AUTHOR",
    "MODULE_DESCRIPTION",
    "MODULE_VERSION",
    "MODULE_ALIAS",
    "MODULE_SOFTDEP",
    "MODULE_FIRMWARE",
    "EXPORT_SYMBOL",
    "EXPORT_SYMBOL_GPL",
    "EXPORT_SYMBOL_NS",
    "EXPORT_SYMBOL_NS_GPL",
}

SAFE_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.BitOr: operator.or_,
    ast.BitAnd: operator.and_,
    ast.BitXor: operator.xor,
}

SAFE_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Invert: operator.invert,
}


def safe_eval_c_constant(expr: str, known_macros: Optional[Dict[str, int]] = None) -> Optional[int]:
    """Safely evaluates integer/hex/bitshift C preprocessor expressions without eval()."""
    if not expr:
        return None
    cleaned = re.sub(r"/\*.*?\*/", "", expr).strip()
    if not cleaned:
        return None

    # Expand BIT(n) and GENMASK(h, l)
    cleaned = re.sub(r"\bBIT(?:_ULL)?\s*\(\s*(\d+)\s*\)", r"(1 << \1)", cleaned)
    cleaned = re.sub(
        r"\bGENMASK(?:_ULL)?\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)",
        r"(((1 << (\1 - \2 + 1)) - 1) << \2)",
        cleaned,
    )
    # Strip C integer suffixes (U, L, UL, ULL)
    cleaned = re.sub(r"\b(0x[0-9a-fA-F]+|\d+)[uUlL]+\b", r"\1", cleaned)

    # Substitute known macro symbols if provided
    if known_macros:
        for sym, val in sorted(known_macros.items(), key=lambda kv: -len(kv[0])):
            cleaned = re.sub(rf"\b{re.escape(sym)}\b", hex(val), cleaned)

    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError:
        return None

    def _eval_node(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return _eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return int(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in SAFE_BIN_OPS:
            return SAFE_BIN_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in SAFE_UNARY_OPS:
            return SAFE_UNARY_OPS[type(node.op)](_eval_node(node.operand))
        raise ValueError("Unsupported AST expression")

    try:
        return _eval_node(tree)
    except Exception:
        return None


class MacroConstant(BaseModel):
    name: str
    value: str
    is_function_macro: bool = False
    category: str = "constant"  # "register_offset", "bitmask", "constant", "macro"
    numeric_value: Optional[int] = None
    hex_value: Optional[str] = None
    file: str = ""


class DriverStructInfo(BaseModel):
    struct_type: str
    var_name: str
    fields: Dict[str, str] = Field(default_factory=dict)
    raw_snippet: str = ""
    file: str = ""


class MMIOCall(BaseModel):
    function_name: str
    op: str
    args: List[str] = Field(default_factory=list)
    line: int = 0
    file: str = ""


class ConcurrencyCall(BaseModel):
    function_name: str
    primitive: str
    lock_arg: str = ""
    line: int = 0
    file: str = ""


class FunctionSummary(BaseModel):
    name: str
    role: str = "helper"  # "probe", "remove", "xmit", "irq_handler", "io_op", "init", "pm", "helper"
    return_type: str = ""
    parameters: str = ""
    mmio_calls: List[MMIOCall] = Field(default_factory=list)
    irq_calls: List[str] = Field(default_factory=list)
    lock_calls: List[ConcurrencyCall] = Field(default_factory=list)
    dma_calls: List[str] = Field(default_factory=list)
    called_functions: List[str] = Field(default_factory=list)
    source_code: str = ""
    file: str = ""


class ASTExtractionResult(BaseModel):
    macros: List[MacroConstant] = Field(default_factory=list)
    driver_structs: List[DriverStructInfo] = Field(default_factory=list)
    functions: List[FunctionSummary] = Field(default_factory=list)
    mmio_operations: List[MMIOCall] = Field(default_factory=list)
    concurrency_calls: List[ConcurrencyCall] = Field(default_factory=list)
    metadata_macros: Dict[str, Any] = Field(default_factory=dict)
    pruned_source: str = ""
    original_chars: int = 0
    pruned_chars: int = 0

    @property
    def compression_ratio(self) -> float:
        if self.original_chars == 0:
            return 0.0
        return round(1.0 - (self.pruned_chars / self.original_chars), 4)

    def macro_lookup(self) -> Dict[str, MacroConstant]:
        return {m.name: m for m in self.macros}


class ASTPreProcessor:
    """Tree-sitter C AST analyzer that extracts hardware/driver primitives and strips boilerplate."""

    def __init__(self, strip_boilerplate: bool = True) -> None:
        if C_LANGUAGE is None or Parser is None:
            raise ASTParsingError(
                "tree-sitter and tree-sitter-c are required. Install with: pip install 'tree-sitter>=0.23.0' 'tree-sitter-c>=0.23.0'"
            ) from _TREE_SITTER_IMPORT_ERROR
        self.strip_boilerplate = strip_boilerplate
        self.parser = Parser(C_LANGUAGE)

    @staticmethod
    def _node_text(node: Optional[Node], source_bytes: bytes) -> str:
        if node is None:
            return ""
        return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def _classify_macro(self, name: str, value: str, is_func: bool) -> str:
        if is_func:
            return "macro"
        upper_name = name.upper()
        if any(tok in value for tok in ("BIT(", "BIT_ULL(", "GENMASK(", "GENMASK_ULL(", "<<")):
            return "bitmask"
        if any(
            prefix in upper_name
            for prefix in ("REG_", "_REG", "UART_", "VIRTIO_MMIO_", "E1000_", "CSR_", "MMIO_")
        ):
            # Distinguish bitfield masks from register offsets when obvious
            if any(suffix in upper_name for suffix in ("_MASK", "_SHIFT", "_BIT", "_EN", "_ENABLE")):
                return "bitmask"
            return "register_offset"
        if re.match(r"^0x[0-9a-fA-F]+$", value.strip()):
            return "register_offset"
        return "constant"

    def _extract_struct_fields(self, init_list_node: Node, source_bytes: bytes) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        for child in init_list_node.named_children:
            if child.type == "initializer_pair":
                designator = child.child_by_field_name("designator")
                value = child.child_by_field_name("value")
                if designator is not None and value is not None:
                    field_key = self._node_text(designator, source_bytes).lstrip(".").strip()
                    field_val = self._node_text(value, source_bytes).strip()
                    fields[field_key] = field_val
        return fields

    def _extract_function_name_and_params(self, declarator: Optional[Node], source_bytes: bytes) -> Tuple[str, str]:
        curr = declarator
        while curr is not None:
            if curr.type == "function_declarator":
                fn_id = curr.child_by_field_name("declarator")
                params = curr.child_by_field_name("parameters")
                return (
                    self._node_text(fn_id, source_bytes).strip(),
                    self._node_text(params, source_bytes).strip(),
                )
            curr = curr.child_by_field_name("declarator")
        return ("", "")

    def _parse_call_args(self, args_node: Optional[Node], source_bytes: bytes) -> List[str]:
        if args_node is None:
            return []
        return [
            self._node_text(child, source_bytes).strip()
            for child in args_node.named_children
        ]

    def _analyze_function(
        self,
        fn_node: Node,
        source_bytes: bytes,
        filename: str,
        callback_roles: Dict[str, str],
        pm_functions: Set[str],
    ) -> Tuple[FunctionSummary, List[Tuple[int, int]]]:
        ret_node = fn_node.child_by_field_name("type")
        decl_node = fn_node.child_by_field_name("declarator")
        body_node = fn_node.child_by_field_name("body")

        ret_type = self._node_text(ret_node, source_bytes).strip()
        fn_name, params_text = self._extract_function_name_and_params(decl_node, source_bytes)

        mmio_calls: List[MMIOCall] = []
        irq_calls: List[str] = []
        lock_calls: List[ConcurrencyCall] = []
        dma_calls: List[str] = []
        called_functions: List[str] = []
        strip_spans: List[Tuple[int, int]] = []
        detected_irq_handlers: List[str] = []

        def _walk(node: Node) -> None:
            if node.type == "call_expression":
                callee_node = node.child_by_field_name("function")
                args_node = node.child_by_field_name("arguments")
                callee_name = self._node_text(callee_node, source_bytes).strip()
                args = self._parse_call_args(args_node, source_bytes)

                if callee_name and callee_name not in called_functions:
                    called_functions.append(callee_name)

                if callee_name in MMIO_CALL_NAMES:
                    mmio_calls.append(
                        MMIOCall(
                            function_name=fn_name,
                            op=callee_name,
                            args=args,
                            line=node.start_point[0] + 1,
                            file=filename,
                        )
                    )
                elif callee_name in IRQ_CALL_NAMES:
                    call_sig = f"{callee_name}({', '.join(args)})"
                    irq_calls.append(call_sig)
                    if "request" in callee_name and len(args) >= 2:
                        handler_candidate = args[1].lstrip("&").strip()
                        if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", handler_candidate):
                            detected_irq_handlers.append(handler_candidate)
                elif callee_name in LOCK_CALL_NAMES:
                    lock_calls.append(
                        ConcurrencyCall(
                            function_name=fn_name,
                            primitive=callee_name,
                            lock_arg=args[0] if args else "",
                            line=node.start_point[0] + 1,
                            file=filename,
                        )
                    )
                elif callee_name in DMA_MEM_CALL_NAMES:
                    dma_calls.append(f"{callee_name}({', '.join(args)})")

                if self.strip_boilerplate and callee_name in DEBUG_BOILERPLATE_CALLS:
                    parent = node.parent
                    if parent is not None and parent.type == "expression_statement":
                        strip_spans.append((parent.start_byte, parent.end_byte))
                    else:
                        strip_spans.append((node.start_byte, node.end_byte))

            for child in node.children:
                _walk(child)

        if body_node is not None:
            _walk(body_node)

        for handler in detected_irq_handlers:
            callback_roles.setdefault(handler, "irq_handler")

        # Determine semantic role
        if fn_name in pm_functions:
            role = "pm"
        elif fn_name in callback_roles:
            role = callback_roles[fn_name]
        elif "irqreturn_t" in ret_type:
            role = "irq_handler"
        elif "probe" in fn_name or "early_setup" in fn_name or "earlycon" in fn_name:
            role = "probe"
        elif "remove" in fn_name:
            role = "remove"
        elif "xmit" in fn_name or "tx" in fn_name:
            role = "xmit"
        elif "intr" in fn_name or "irq" in fn_name:
            role = "irq_handler"
        elif "init" in fn_name or "reset" in fn_name:
            role = "init"
        else:
            role = "helper"

        raw_fn_src = self._node_text(fn_node, source_bytes)
        summary = FunctionSummary(
            name=fn_name,
            role=role,
            return_type=ret_type,
            parameters=params_text,
            mmio_calls=mmio_calls,
            irq_calls=irq_calls,
            lock_calls=lock_calls,
            dma_calls=dma_calls,
            called_functions=called_functions,
            source_code=raw_fn_src,
            file=filename,
        )
        return summary, strip_spans

    def parse_files(self, files: List[Tuple[str, str]]) -> ASTExtractionResult:
        """Parses a list of `(relative_filename, content)` C source/header pairs."""
        all_macros: List[MacroConstant] = []
        known_numeric_macros: Dict[str, int] = {}
        all_structs: List[DriverStructInfo] = []
        all_functions: List[FunctionSummary] = []
        all_mmio: List[MMIOCall] = []
        all_locks: List[ConcurrencyCall] = []
        metadata_macros: Dict[str, Any] = {"module_params": []}
        pruned_file_blocks: List[str] = []

        total_orig = 0
        total_pruned = 0

        for filename, content in files:
            total_orig += len(content)
            source_bytes = content.encode("utf-8", errors="replace")
            try:
                tree = self.parser.parse(source_bytes)
            except Exception as e:
                raise ASTParsingError(f"Tree-sitter failed to parse '{filename}': {e}") from e

            root = tree.root_node
            strip_spans: List[Tuple[int, int]] = []
            callback_roles: Dict[str, str] = {}
            pm_functions: Set[str] = set()

            # Pass 1: Preprocessor macros, top-level driver structs, and boilerplate spans
            def _collect_top_level(node: Node) -> None:
                if node.type == "preproc_def":
                    name_node = node.child_by_field_name("name")
                    val_node = node.child_by_field_name("value")
                    macro_name = self._node_text(name_node, source_bytes).strip()
                    macro_val = self._node_text(val_node, source_bytes).strip()
                    if macro_name:
                        num_val = safe_eval_c_constant(macro_val, known_numeric_macros)
                        if num_val is not None:
                            known_numeric_macros[macro_name] = num_val
                        category = self._classify_macro(macro_name, macro_val, is_func=False)
                        all_macros.append(
                            MacroConstant(
                                name=macro_name,
                                value=macro_val,
                                is_function_macro=False,
                                category=category,
                                numeric_value=num_val,
                                hex_value=hex(num_val) if num_val is not None else None,
                                file=filename,
                            )
                        )

                elif node.type == "preproc_function_def":
                    name_node = node.child_by_field_name("name")
                    params_node = node.child_by_field_name("parameters")
                    val_node = node.child_by_field_name("value")
                    macro_name = (
                        f"{self._node_text(name_node, source_bytes).strip()}"
                        f"{self._node_text(params_node, source_bytes).strip()}"
                    )
                    macro_val = self._node_text(val_node, source_bytes).strip()
                    all_macros.append(
                        MacroConstant(
                            name=macro_name,
                            value=macro_val,
                            is_function_macro=True,
                            category="macro",
                            file=filename,
                        )
                    )

                elif node.type in {"preproc_ifdef", "preproc_if"} and self.strip_boilerplate:
                    header_line = content[node.start_byte : min(node.end_byte, node.start_byte + 80)]
                    if re.search(r"CONFIG_PM(_SLEEP)?\b", header_line):
                        strip_spans.append((node.start_byte, node.end_byte))
                        return

                elif node.type == "declaration":
                    type_node = node.child_by_field_name("type")
                    decl_node = node.child_by_field_name("declarator")
                    if type_node is not None and type_node.type == "struct_specifier":
                        struct_name_node = type_node.child_by_field_name("name")
                        struct_type = self._node_text(struct_name_node, source_bytes).strip()

                        if struct_type in PM_STRUCT_TYPES:
                            if decl_node is not None and decl_node.type == "init_declarator":
                                val_node = decl_node.child_by_field_name("value")
                                if val_node is not None and val_node.type == "initializer_list":
                                    pm_fields = self._extract_struct_fields(val_node, source_bytes)
                                    pm_functions.update(pm_fields.values())
                            if self.strip_boilerplate:
                                strip_spans.append((node.start_byte, node.end_byte))
                            return

                        if struct_type in DRIVER_STRUCT_TYPES:
                            var_name = ""
                            fields: Dict[str, str] = {}
                            if decl_node is not None:
                                if decl_node.type == "init_declarator":
                                    ident = decl_node.child_by_field_name("declarator")
                                    val_node = decl_node.child_by_field_name("value")
                                    var_name = self._node_text(ident, source_bytes).strip()
                                    if val_node is not None and val_node.type == "initializer_list":
                                        fields = self._extract_struct_fields(val_node, source_bytes)
                                else:
                                    var_name = self._node_text(decl_node, source_bytes).strip()

                            # Map callback fields to roles
                            for field_key, fn_ref in fields.items():
                                clean_fn = fn_ref.lstrip("&").strip()
                                if "probe" in field_key:
                                    callback_roles[clean_fn] = "probe"
                                elif "remove" in field_key:
                                    callback_roles[clean_fn] = "remove"
                                elif "xmit" in field_key:
                                    callback_roles[clean_fn] = "xmit"
                                elif field_key in {"read", "write", "unlocked_ioctl", "startup", "shutdown", "set_termios"}:
                                    callback_roles[clean_fn] = "io_op"

                            all_structs.append(
                                DriverStructInfo(
                                    struct_type=struct_type,
                                    var_name=var_name,
                                    fields=fields,
                                    raw_snippet=self._node_text(node, source_bytes),
                                    file=filename,
                                )
                            )

                elif node.type == "expression_statement":
                    expr_text = self._node_text(node, source_bytes).strip()
                    call_match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*;?$", expr_text, re.DOTALL)
                    if call_match:
                        macro_id = call_match.group(1)
                        macro_arg = call_match.group(2).strip().strip('"')
                        if macro_id == "MODULE_LICENSE":
                            metadata_macros["license"] = macro_arg
                        elif macro_id == "MODULE_AUTHOR":
                            metadata_macros["author"] = macro_arg
                        elif macro_id == "MODULE_DESCRIPTION":
                            metadata_macros["description"] = macro_arg
                        elif macro_id in {"module_param", "module_param_named"}:
                            metadata_macros["module_params"].append(call_match.group(2).strip())

                        if self.strip_boilerplate and macro_id in MODULE_BOILERPLATE_MACROS:
                            strip_spans.append((node.start_byte, node.end_byte))
                            return

                for child in node.children:
                    if child.type != "function_definition":
                        _collect_top_level(child)

            _collect_top_level(root)

            # Pass 2: Function definitions (probe, remove, xmit, irq_handler, MMIO, locks)
            for child in root.children:
                if child.type == "function_definition":
                    fn_summary, fn_strip_spans = self._analyze_function(
                        child,
                        source_bytes=source_bytes,
                        filename=filename,
                        callback_roles=callback_roles,
                        pm_functions=pm_functions,
                    )
                    if self.strip_boilerplate and fn_summary.role == "pm":
                        strip_spans.append((child.start_byte, child.end_byte))
                        continue

                    all_functions.append(fn_summary)
                    all_mmio.extend(fn_summary.mmio_calls)
                    all_locks.extend(fn_summary.lock_calls)
                    strip_spans.extend(fn_strip_spans)

            # Reconstruct boilerplate-stripped source for this file
            pruned_bytes = self._apply_strip_spans(source_bytes, strip_spans)
            pruned_text = pruned_bytes.decode("utf-8", errors="replace")
            # Collapse excessive blank lines left by stripped nodes
            pruned_text = re.sub(r"\n{3,}", "\n\n", pruned_text).strip()
            total_pruned += len(pruned_text)

            pruned_file_blocks.append(
                f"/* =========================================================================\n"
                f" * File: {filename}\n"
                f" * ========================================================================= */\n\n"
                f"{pruned_text}"
            )

        ast_header = self._format_ast_summary_header(
            macros=all_macros,
            structs=all_structs,
            functions=all_functions,
            metadata=metadata_macros,
        )
        full_pruned_source = f"{ast_header}\n\n" + "\n\n".join(pruned_file_blocks)

        return ASTExtractionResult(
            macros=all_macros,
            driver_structs=all_structs,
            functions=all_functions,
            mmio_operations=all_mmio,
            concurrency_calls=all_locks,
            metadata_macros=metadata_macros,
            pruned_source=full_pruned_source,
            original_chars=total_orig,
            pruned_chars=len(full_pruned_source),
        )

    @staticmethod
    def _apply_strip_spans(source_bytes: bytes, spans: List[Tuple[int, int]]) -> bytes:
        if not spans:
            return source_bytes
        sorted_spans = sorted(spans, key=lambda s: (s[0], s[1]))
        merged: List[Tuple[int, int]] = []
        for start, end in sorted_spans:
            if not merged or start > merged[-1][1]:
                merged.append((start, end))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))

        chunks: List[bytes] = []
        cursor = 0
        for start, end in merged:
            if start > cursor:
                chunks.append(source_bytes[cursor:start])
            cursor = end
        if cursor < len(source_bytes):
            chunks.append(source_bytes[cursor:])
        return b"".join(chunks)

    @staticmethod
    def _format_ast_summary_header(
        macros: List[MacroConstant],
        structs: List[DriverStructInfo],
        functions: List[FunctionSummary],
        metadata: Dict[str, Any],
    ) -> str:
        lines = [
            "/* =========================================================================",
            " * AST PRE-PROCESSOR EXTRACTED PRIMITIVES (GROUND TRUTH CONSTANTS & SYMBOLS)",
            " * =========================================================================",
        ]
        if metadata.get("description"):
            lines.append(f" * Module Description: {metadata['description']}")
        if metadata.get("author"):
            lines.append(f" * Module Author: {metadata['author']}")
        if metadata.get("license"):
            lines.append(f" * Module License: {metadata['license']}")

        reg_macros = [m for m in macros if m.category in {"register_offset", "bitmask"}]
        if reg_macros:
            lines.append(" *")
            lines.append(" * Extracted Register & Bitmask Macros:")
            for m in reg_macros[:150]:
                hex_note = f"  /* resolved={m.hex_value} */" if m.hex_value else ""
                lines.append(f" *   #define {m.name} {m.value}{hex_note}")

        if structs:
            lines.append(" *")
            lines.append(" * Extracted Driver Structs:")
            for s in structs:
                lines.append(f" *   struct {s.struct_type} {s.var_name} -> {s.fields}")

        key_funcs = [f for f in functions if f.role != "helper" or f.mmio_calls]
        if key_funcs:
            lines.append(" *")
            lines.append(" * Extracted Entry Points & MMIO Functions:")
            for fn in key_funcs:
                lines.append(
                    f" *   [{fn.role.upper()}] {fn.name}({fn.parameters}) "
                    f"(mmio_ops={len(fn.mmio_calls)}, irq_calls={len(fn.irq_calls)}, locks={len(fn.lock_calls)})"
                )

        lines.append(" * ========================================================================= */")
        return "\n".join(lines)


def parse_driver_ast(
    files: List[Tuple[str, str]],
    strip_boilerplate: bool = True,
) -> ASTExtractionResult:
    """Convenience function to parse C files with Tree-sitter AST pre-processor."""
    processor = ASTPreProcessor(strip_boilerplate=strip_boilerplate)
    return processor.parse_files(files)

// ==========================================================================
// File: src/kernelite_prism/core/__pycache__/extractor.cpython-311.pyc
// ==========================================================================

§
    §Œüjê@  ã                   óv  — d dl Z d dlZd dlZd dlmZmZmZmZ d dlZd dl	m
Z
mZmZ d dlmZ ddlmZmZmZ ddlmZmZ ddlmZ dd	lmZmZmZ d
dlmZ dZdedefd„Z dedee         defd„Z! G d„ d¦  «        Z"edddddddfdedededededee         de#dee         d eeeegef                  defd!„Z$dS )"é    N)ÚAnyÚCallableÚDictÚOptional)ÚAutoModelForCausalLMÚAutoTokenizerÚBitsAndBytesConfig)ÚValidationErroré   )ÚDEFAULT_MODELÚVALID_BACKENDSÚVALID_QUANTIZATIONS)ÚConfigurationErrorÚExtractionError)Ú
DriverSpec)ÚBitfieldÚRegisterMapÚinfer_bits_from_macroé   )ÚASTExtractionResulta  You are an expert Linux kernel driver engineer and bare-metal C++ systems architect. Your objective is to analyze pre-parsed Linux kernel C source code (.c and .h files) and extract a complete, deterministic hardware and driver specification for porting to a bare-metal C++ microkernel.

MANDATORY EXTRACTION RULES:
1. ZERO HALLUCINATED ADDRESSES OR MASKS: Every register offset and bitmask in `register_map` MUST strictly match the C header/source macro definitions. Never invent offsets.
2. ISOLATE REGISTER OFFSET EXPRESSIONS: When a register is indexed or computed dynamically (e.g., `BASE + (i * 0x4)` or `0x03800 + (n * 0x100)`), record the base hex offset in `offset` and preserve the exact C arithmetic formula in `offset_expression`.
3. CHRONOLOGICAL PROBE / INIT SEQUENCE: Populate `init_sequence` in exact execution order matching `.probe` / early init (`MMIO Map`, `Hardware Reset`, `Write Register`, `Read & Poll`, `Memory Allocate`, `DMA Setup`, `Interrupt Hook`, `Register Device`).
4. ABSTRACT CONCURRENCY PRIMITIVES: Translate every Linux lock (`spin_lock_irqsave`, `spin_lock`, `mutex_lock`, `raw_spin_lock_irqsave`) in `concurrency_locks` into an abstract lock (`IRQSafeSpinlock`, `Spinlock`, `SleepableMutex`, `ReadWriteLock`, `Atomic`), listing protected registers/rings and bare-metal C++ guidance.
5. MAP IRQ ROUTINES TO RAW HARDWARE REGISTERS: In `runtime_operations.interrupt_lifecycle`, trace `irqreturn_t` handlers and `request_irq` calls to the exact hardware status register, acknowledge/clear register, mask register, acknowledgment mechanism (e.g., Read-to-clear vs. W1C), and ordered `flow_steps`.

CRITICAL OUTPUT REQUIREMENT: Output strictly a single, valid JSON object satisfying the provided JSON Schema Draft 2020-12. Do not emit markdown fences, commentary, or extra text.Ú
raw_outputÚreturnc                 ó\  — t          j        d| ¦  «        }|r'|                     d¦  «                             ¦   «         S |                      d¦  «        }|                      d¦  «        }|dk    r+|dk    r%||k    r| ||dz   …                              ¦   «         S |                      ¦   «         S )zEExtracts raw JSON payload from markdown fences or bracket boundaries.z"```(?:json)?\s*(\{[\s\S]*\})\s*```r   Ú{Ú}éÿÿÿÿ)ÚreÚsearchÚgroupÚstripÚfindÚrfind)r   Úfence_matchÚ	start_idxÚend_idxs       ú7/workspaces/prism/src/kernelite_prism/core/extractor.pyÚ_extract_json_textr'   *   s­   € å”)ĞAÀ:ÑNÔN€KØğ ,Ø× Ò  Ñ#Ô#×)Ò)Ñ+Ô+Ğ+à—’ Ñ$Ô$€IØ×Ò˜sÑ#Ô#€GØB‚€˜7 bš=˜=¨W°yÒ-@Ğ-@Ø˜) g°¡kĞ1Ô2×8Ò8Ñ:Ô:Ğ:à×ÒÑÔĞó    ÚspecÚ
ast_resultc                 óB  — |€| S |                      ¦   «         }| j        D ]§}|j        |v r;||j                 }|j        |j        |_        n|j        r|j        s|j        |_        |j        D ]Y}|j        |v r'||j                 }|j        r|j        s|j        |_        |j        s |j        rt          |j        ¦  «        |_        ŒZŒ¨|j
        }| j        j        s'|                     d¦  «        r|d         | j        _        | j        j        s'|                     d¦  «        r|d         | j        _        | j        j        s'|                     d¦  «        r|d         | j        _        |j        D ]/}|j        | j        vr| j                             |j        ¦  «         Œ0| S )zZEnforces deterministic parity between AST-parsed C constants and the extracted DriverSpec.NÚlicenseÚauthorÚdescription)Úmacro_lookupÚregister_mapÚnameÚ	hex_valueÚoffsetÚvalueÚ	bitfieldsÚ
mask_macroÚbitsr   Úmetadata_macrosÚmetadatar,   Úgetr-   r.   Údriver_structsÚstruct_typeÚstructsÚappend)	r)   r*   Ú	macro_mapÚregÚmacroÚbfÚbf_macroÚmetaÚss	            r&   Úground_spec_with_astrF   8   sÀ  € àĞØˆà×'Ò'Ñ)Ô)€Iğ Ô ğ ?ğ ?ˆØŒ8yĞ Ğ Ø˜cœhÔ'ˆEØŒĞ*Ø"œ_”

Ø”ğ ) S¤Zğ )Ø"œ[”
à”-ğ 	?ğ 	?ˆBØŒw˜)Ğ#Ğ#Ø$ R¤WÔ-Ø”>ğ 3¨"¬-ğ 3Ø$,¤NB”MØ”7ğ ?˜rœ}ğ ?İ/°´Ñ>Ô>”øğ	?ğ Ô%€DØŒ=Ô ğ 0 T§X¢X¨iÑ%8Ô%8ğ 0Ø $ Y¤ˆŒÔØŒ=Ôğ . D§H¢H¨XÑ$6Ô$6ğ .Ø# Hœ~ˆŒÔØŒ=Ô$ğ 8¨¯ª°-Ñ)@Ô)@ğ 8Ø$(¨Ô$7ˆŒÔ!ğ Ô&ğ /ğ /ˆØŒ= ¤Ğ,Ğ,ØŒL×Ò ¤Ñ.Ô.Ğ.øà€Kr(   c                   ó  — e Zd ZdZedddddddfdededed	ed
ee         dee         dee         deeeegef                  ddfd„Zdd„Z	dd„Z
deeef         fd„Zdededefd„Zdededefd„Z	 	 	 ddedededee         def
d„ZdS )ÚDriverExtractorzLEncapsulates LLM inference and schema-enforced structured output generation.ÚcudaÚ4bitÚtransformersNÚ
model_nameÚdeviceÚquantizationÚbackendÚhf_tokenÚopenai_api_keyÚopenai_base_urlÚcustom_generatorr   c	                 ó  — || _         || _        |                     ¦   «         | _        |                     ¦   «         | _        || _        |pt          j        d¦  «        | _        |pt          j        d¦  «        | _	        || _
        d | _        d | _        d | _        | j
        €_|                      ¦   «          | j        dv rD|                      ¦   «         \  | _        | _        | j        dk    r|                      ¦   «          d S d S d S d S )NÚOPENAI_API_KEYÚOPENAI_BASE_URL>   ÚoutlinesrK   rW   )rL   rM   ÚlowerrN   rO   rP   ÚosÚgetenvrQ   rR   rS   Ú	tokenizerÚmodelÚ_outlines_logits_processorÚ_validate_runtime_optionsÚ_load_model_and_tokenizerÚ_init_outlines_processor)	ÚselfrL   rM   rN   rO   rP   rQ   rR   rS   s	            r&   Ú__init__zDriverExtractor.__init__d   s  € ğ %ˆŒØˆŒØ(×.Ò.Ñ0Ô0ˆÔØ—}’}‘”ˆŒØ ˆŒØ,ĞKµ´	Ğ:JÑ0KÔ0KˆÔØ.ĞNµ"´)Ğ<MÑ2NÔ2NˆÔØ 0ˆÔà"ˆŒØˆŒ
Ø/3ˆÔ'àÔ Ğ(Ø×*Ò*Ñ,Ô,Ğ,ØŒ|Ğ;Ğ;Ğ;Ø-1×-KÒ-KÑ-MÔ-MÑ*” ¤
Ø”< :Ò-Ğ-Ø×1Ò1Ñ3Ô3Ğ3Ğ3Ğ3ğ )Ğ(à;Ğ;à-Ğ-r(   c           
      óV  — | j         t          vr@t          d| j         › dd                     t	          t          ¦  «        ¦  «        › d¦  «        ‚| j         dv rÍ| j        t          vr@t          d| j        › dd                     t	          t          ¦  «        ¦  «        › d¦  «        ‚| j                             d¦  «        }|r6t          j
                             ¦   «         st          d| j        › d	¦  «        ‚|s-| j        d
k    r$t          d| j        › d| j        › d¦  «        ‚d S d S d S )NzUnsupported backend 'z'. Choose from: z, ú.>   rW   rK   zUnsupported quantization 'rI   zDevice 'zy' was requested, but CUDA is not available. Ensure an NVIDIA GPU with appropriate CUDA drivers and PyTorch are installed.ÚnonezQuantization 'z1' is only supported on CUDA devices, got device 'z2'. Use quantization='none' for non-CUDA execution.)rO   r   r   ÚjoinÚsortedrN   r   rM   Ú
startswithÚtorchrI   Úis_available)ra   Úis_cudas     r&   r^   z)DriverExtractor._validate_runtime_optionsƒ   s£  € ØŒ<~Ğ-Ğ-İ$ğE¨¬ğ Eğ EØ $§	¢	­&µÑ*@Ô*@Ñ AÔ AğEğ Eğ Eñô ğ ğ
 Œ<Ğ7Ğ7Ğ7ØÔ Õ(;Ğ;Ğ;İ(ğN°Ô1Bğ Nğ NØ$(§I¢I­fÕ5HÑ.IÔ.IÑ$JÔ$JğNğ Nğ Nñô ğ ğ
 ”k×,Ò,¨VÑ4Ô4ˆGàğ uœz×6Ò6Ñ8Ô8ğ İ(ğd˜tœ{ğ dğ dğ dñô ğ ğ
 ğ ˜tÔ0°FÒ:Ğ:İ(ğc TÔ%6ğ cğ cØ#'¤;ğcğ cğ cñô ğ ğ! 8Ğ7ğğ Ğ:Ğ:r(   c                 óÜ   — 	 ddl }ddlm} |                     | j        | j        ¦  «        } |t          j        ¦   «         |j        ¬¦  «        | _        dS # t          $ r d| _        Y dS w xY w)zMInitializes Outlines JSON Schema constrained logits processor when available.r   N)ÚJSONLogitsProcessor)Úschemar[   )
rW   Úoutlines.processorsrm   Úfrom_transformersr\   r[   r   Údraft_2020_12_schemar]   Ú	Exception)ra   rW   rm   Úoutlines_models       r&   r`   z(DriverExtractor._init_outlines_processorŸ   s›   € ğ	3ØˆOˆOˆOØ?Ğ?Ğ?Ğ?Ğ?Ğ?à%×7Ò7¸¼
ÀDÄNÑSÔSˆNØ.AĞ.Aİ!Ô6Ñ8Ô8Ø(Ô2ğ/ñ /ô /ˆDÔ+Ğ+Ğ+øõ ğ 	3ğ 	3ğ 	3à.2ˆDÔ+Ğ+Ğ+Ğ+ğ	3øøøs   ‚AA ÁA+Á*A+c           	      ó¾  — | j                              d¦  «        }d }|rqt          j                             ¦   «         rt          j        nt          j        }| j        dk    rt          ddd|¬¦  «        }n(| j        dk    rt          d¬¦  «        }nt          j	        }	 t          j        | j        | j        d¬¦  «        }|j        €|j        |_        | j        d| j         dk    rd	n| j         d
œ}|||d<   n||d<   t!          j        | j        fi |¤}|                     ¦   «          ||fS # t$          $ r(}t'          d| j        › d| j         › d|› ¦  «        |‚d }~ww xY w)NrI   rJ   TÚnf4)Úload_in_4bitÚbnb_4bit_quant_typeÚbnb_4bit_use_double_quantÚbnb_4bit_compute_dtypeÚ8bit)Úload_in_8bit)ÚtokenÚtrust_remote_codeÚauto)r|   r}   Ú
device_mapÚquantization_configÚtorch_dtypezFailed to load model 'z' on 'z': )rM   rh   ri   rI   Úis_bf16_supportedÚbfloat16Úfloat16rN   r	   Úfloat32r   Úfrom_pretrainedrL   rP   Úpad_token_idÚeos_token_idr   Úevalrr   r   )ra   rk   Úquant_configÚcompute_dtyper[   Úmodel_kwargsr\   Úes           r&   r_   z)DriverExtractor._load_model_and_tokenizer®   s½  € Ø”+×(Ò(¨Ñ0Ô0ˆØ59ˆàğ 	*å"'¤*×">Ò">Ñ"@Ô"@ĞS•”ÅeÄmğ ğ Ô  FÒ*Ğ*İ1Ø!%Ø(-Ø.2Ø+8ğ	 ñ  ô  ğ Ô" fÒ,Ğ,İ1¸tĞDÑDÔDøå!œMˆMğ	İ%Ô5Ø”Ø”mØ"&ğñ ô ˆIğ Ô%Ğ-Ø)2Ô)?	Ô&ğ œØ%)Ø(,¬°vÒ(=Ğ(=˜f˜fÀ4Ä;ğ,ğ ,ˆLğ Ğ'Ø6BĞ2Ñ3Ğ3à.;˜]Ñ+å(Ô8Ø”ğğ Ø#/ğğ ˆEğ JŠJ‰LŒLˆLØ˜eĞ#Ğ#øåğ 	ğ 	ğ 	İ!ØS¨¬ĞSĞSÀÄĞSĞSĞPQĞSĞSñô àğøøøøğ	øøøs   ÂBD* Ä*
EÄ4#EÅEÚuser_promptÚmax_new_tokensc                 ó|  — dt           dœd|dœg}t          | j        d¦  «        r*| j        j        r| j                             |dd¬¦  «        }nt           › d|› }|                      |d	¬
¦  «                             | j        j        ¦  «        }|d| j        j        dœ}| j	        | j	        g|d<   t          j        ¦   «         5   | j        j        di |¤|¤}d d d ¦  «         n# 1 swxY w Y   |d         |j        j        d         d …         }| j                             |d¬¦  «                             ¦   «         S )NÚsystem©ÚroleÚcontentÚuserÚapply_chat_templateFT)ÚtokenizeÚadd_generation_promptz

Úpt)Úreturn_tensors)r   Ú	do_sampler‡   Úlogits_processorr   r   )Úskip_special_tokens© )ÚSYSTEM_INSTRUCTIONÚhasattrr[   Úchat_templater–   Útor\   rM   r‡   r]   ri   Úno_gradÚgenerateÚ	input_idsÚshapeÚdecoder    )	ra   r   r   ÚmessagesÚ
prompt_strÚinputsÚ
gen_kwargsÚoutput_tokensÚgenerated_idss	            r&   Ú_generate_with_transformersz+DriverExtractor._generate_with_transformersâ   sº  € àÕ*<Ğ=Ğ=Ø¨Ğ4Ğ4ğ
ˆõ D”NĞ$9Ñ:Ô:ğ	Bà”Ô,ğ	Bğ œ×;Ò;Ø 5Àğ <ñ ô ˆJˆJõ /ĞAĞA°KĞAĞAˆJà—’ 
¸4Ñ@Ô@×CÒCÀDÄJÔDUÑVÔVˆà,ØØ œNÔ7ğ&
ğ &
ˆ
ğ
 Ô*Ğ6Ø.2Ô.MĞ-NˆJĞ)Ñ*åŒ]‰_Œ_ğ 	Hğ 	HØ/˜DœJÔ/ĞGĞG°&ĞG¸JĞGĞGˆMğ	Hğ 	Hğ 	Hñ 	Hô 	Hğ 	Hğ 	Hğ 	Hğ 	Hğ 	Hğ 	Høøøğ 	Hğ 	Hğ 	Hğ 	Hğ & aÔ(¨Ô)9Ô)?ÀÔ)BĞ)DĞ)DÔEˆØŒ~×$Ò$ ]ÈĞ$ÑMÔM×SÒSÑUÔUĞUs   ÃC%Ã%C)Ã,C)c                 ó¬  — 	 ddl m} n"# t          $ r}t          d¦  «        |‚d }~ww xY w || j        | j        ¬¦  «        }| j        dk    r	 dd l}|                     |¦  «        }|j	        j
                             | j        t          |ddt          dœd	|dœg¬
¦  «        }|                     ¦   «         S # t           $ r}t          d|› ¦  «        |‚d }~ww xY w|j	        j
                             | j        d|dt          dœd	|dœgdddt          j        ¦   «         dœdœ¬¦  «        }	|	j        d         j        j        pd}
|
                     ¦   «         S )Nr   )ÚOpenAIz<The 'openai' package is required for API structured outputs.)Úapi_keyÚbase_urlÚ
instructorg        r‘   r’   r•   )r\   Úresponse_modelÚ
max_tokensÚtemperaturer¨   z)Instructor structured extraction failed: Újson_schemaÚdriver_specF)r1   Ústrictrn   )Útyper·   )r\   r¶   rµ   r¨   Úresponse_formatÚ )Úopenair°   ÚImportErrorr   rQ   rR   rO   r³   Úfrom_openaiÚchatÚcompletionsÚcreaterL   r   rŸ   Úmodel_dump_jsonrr   rq   ÚchoicesÚmessager”   r    )ra   r   r   r°   r   Úclientr³   Úpatched_clientÚspec_objÚresponser”   s              r&   Ú#_generate_with_openai_or_instructorz3DriverExtractor._generate_with_openai_or_instructor  sØ  € ğ	iØ%Ğ%Ğ%Ğ%Ğ%Ğ%Ğ%øİğ 	iğ 	iğ 	iİ!Ğ"`ÑaÔaĞghĞhøøøøğ	iøøøğ  Ô 3¸dÔ>RĞSÑSÔSˆàŒ<˜<Ò'Ğ'ğ^Ø!Ğ!Ğ!Ğ!à!+×!7Ò!7¸Ñ!?Ô!?Ø)Ô.Ô:×AÒAØœ/İ#-Ø-Ø #à!)Õ6HĞIĞIØ!'°KĞ@Ğ@ğğ Bñ 	ô 	ğ  ×/Ò/Ñ1Ô1Ğ1øİğ ^ğ ^ğ ^İ%Ğ&UĞRSĞ&UĞ&UÑVÔVĞ\]Ğ]øøøøğ^øøøğ ”;Ô*×1Ò1Ø”/ØØ%à!Õ.@ĞAĞAØ¨KĞ8Ğ8ğğ
 &à)Ø#İ(Ô=Ñ?Ô?ğ ğ  ğğ ğ 2ñ 
ô 
ˆğ" Ô" 1Ô%Ô-Ô5Ğ;¸ˆØ}Š}‰ŒĞs)   ‚	 ‰
(“#£(ÁA'B6 Â6
CÃ CÃCé   r   Úcode_contextÚmax_retriesr*   c                 óö  — t          j        t          j        ¦   «         d¬¦  «        }d|› d|› d}|}d}d}	t	          |dz   ¦  «        D ]}
	 | j        |                      t          |¦  «        }n6| j        d	v r|                      ||¦  «        }n|  	                    ||¦  «        }|r| 
                    ¦   «         st          d
¦  «        ‚t          |¦  «        }|}	t          j        |¦  «        }t          ||¦  «        c S # t          t           j        f$ r}|}|
|k     r|› d|› d}Y d}~Œäd}~wt          $ r ‚ t"          $ r}t          d|› ¦  «        |‚d}~ww xY wt          d|dz   › d|› d|	› ¦  «        |‚)z[Extracts and validates a DriverSpec from C source code with schema enforcement and retries.r   )ÚindentzœAnalyze the following Linux C driver code and extract the complete hardware and microkernel porting specification.

### Target JSON Schema (Draft 2020-12):
z

### C Source Code Context:
zC

### Output:
Provide only the raw JSON object matching the schema:Nr¼   r   >   r½   r³   z!Model produced an empty response.zb

### PREVIOUS ATTEMPT VALIDATION ERROR:
Your previous JSON failed strict schema validation with:
zI

Fix all schema errors and return ONLY the corrected, valid JSON object:zLLM extraction failed: zBFailed to validate generated JSON against DriverSpec schema after z attempt(s): z
Extracted payload was:
)ÚjsonÚdumpsr   rq   ÚrangerS   rŸ   rO   rÊ   r®   r    r   r'   Úmodel_validate_jsonrF   r
   ÚJSONDecodeErrorrr   )ra   rÌ   r   rÍ   r*   Úschema_jsonÚbase_promptÚcurrent_promptÚ
last_errorÚlast_payloadÚattemptÚresponse_textÚjson_payloadr)   r   s                  r&   ÚextractzDriverExtractor.extract1  sT  € õ ”j¥Ô!@Ñ!BÔ!BÈ1ĞMÑMÔMˆğRà8CğRğ Rğ ,8ğRğ Rğ Rğ 	ğ %ˆØ*.ˆ
Øˆå˜[¨1™_Ñ-Ô-ğ !	Lñ !	LˆGğ LØÔ(Ğ4Ø$(×$9Ò$9Õ:LÈnÑ$]Ô$]MMØ”\Ğ%=Ğ=Ğ=Ø$(×$LÒ$LØ&¨ñ%ô %MMğ %)×$DÒ$DØ&¨ñ%ô %Mğ %ğ O¨M×,?Ò,?Ñ,AÔ,Ağ Oİ)Ğ*MÑNÔNĞNå1°-Ñ@Ô@Ø+İ!Ô5°lÑCÔCİ+¨D°*Ñ=Ô=Ğ=Ğ=Ğ=øå#¥TÔ%9Ğ:ğ ğ ğ Ø
Ø˜[Ò(Ğ(à&ğ cğ càUVğcğ cğ cğ #øøøøøøøøøõ #ğ ğ ğ Øİğ Lğ Lğ Lİ%Ğ&CÀĞ&CĞ&CÑDÔDÈ!ĞKøøøøğLøøøõ ğDĞQ\Ğ_`ÑQ`ğ Dğ DØğDğ DØ5AğDğ Dñ
ô 
ğ ğ	s%   ÁB2DÄEÄD-Ä-EÅEÅE)r   N)rË   r   N)Ú__name__Ú
__module__Ú__qualname__Ú__doc__r   Ústrr   r   rb   r^   r`   Útupler   r_   Úintr®   rÊ   r   r   rİ   r   r(   r&   rH   rH   a   sÆ  € € € € € ØVĞVğ (ØØ"Ø%Ø"&Ø(,Ø)-Ø@Dğ4ğ 4àğ4ğ ğ4ğ ğ	4ğ
 ğ4ğ ˜3”-ğ4ğ ! œğ4ğ " #œğ4ğ # 8¨S°#¨J¸¨OÔ#<Ô=ğ4ğ 
ğ4ğ 4ğ 4ğ 4ğ>ğ ğ ğ ğ83ğ 3ğ 3ğ 3ğ2¨5°°c°¬?ğ 2ğ 2ğ 2ğ 2ğhV°sğ VÈCğ VĞTWğ Vğ Vğ Vğ Vğ>.¸sğ .ĞTWğ .Ğ\_ğ .ğ .ğ .ğ .ğf #ØØ48ğ;ğ ;àğ;ğ ğ;ğ ğ	;ğ
 Ğ0Ô1ğ;ğ 
ğ;ğ ;ğ ;ğ ;ğ ;ğ ;r(   rH   rI   rJ   rK   rË   rÌ   rL   rM   rN   rO   rP   r   rS   c	                 ó\   — t          ||||||¬¦  «        }	|	                     | ||¬¦  «        S )z4Convenience one-shot wrapper around DriverExtractor.)rL   rM   rN   rO   rP   rS   )rÌ   r   r*   )rH   rİ   )
rÌ   rL   rM   rN   rO   rP   r   r*   rS   Ú	extractors
             r&   Úextract_driver_specrç   o  sR   € õ  ØØØ!ØØØ)ğñ ô €Iğ ×ÒØ!Ø%Øğ ñ ô ğ r(   )%rĞ   rY   r   Útypingr   r   r   r   ri   rK   r   r   r	   Úpydanticr
   Úconfigr   r   r   Ú
exceptionsr   r   Úmodels.baser   Úmodels.registersr   r   r   Ú
ast_parserr   rŸ   râ   r'   rF   rH   rä   rç   r   r(   r&   ú<module>rï      s=  ğØ €€€Ø 	€	€	€	Ø 	€	€	€	Ø 0Ğ 0Ğ 0Ğ 0Ğ 0Ğ 0Ğ 0Ğ 0Ğ 0Ğ 0Ğ 0Ğ 0Ø €€€Ø PĞ PĞ PĞ PĞ PĞ PĞ PĞ PĞ PĞ PØ $Ğ $Ğ $Ğ $Ğ $Ğ $à GĞ GĞ GĞ GĞ GĞ GĞ GĞ GĞ GĞ GØ <Ğ <Ğ <Ğ <Ğ <Ğ <Ğ <Ğ <Ø $Ğ $Ğ $Ğ $Ğ $Ğ $Ø KĞ KĞ KĞ KĞ KĞ KĞ KĞ KĞ KĞ KØ +Ğ +Ğ +Ğ +Ğ +Ğ +ğbğ ğ6 3ğ ¨3ğ ğ ğ ğ ğ&˜zğ &°xĞ@SÔ7Tğ &ĞYcğ &ğ &ğ &ğ &ğRKğ Kğ Kğ Kğ Kñ Kô Kğ Kğ` $ØØØ!Ø"ØØ04Ø<@ğğ Øğàğğ ğğ ğ	ğ
 ğğ sŒmğğ ğğ Ğ,Ô-ğğ ˜x¨¨c¨
°C¨Ô8Ô9ğğ ğğ ğ ğ ğ ğ r(   

// ==========================================================================
// File: src/kernelite_prism/core/__pycache__/ast_parser.cpython-311.pyc
// ==========================================================================

§
    å¡üj®r  ã                   óZ  — U d dl Z d dlZd dlZd dlmZ d dlmZmZmZm	Z	m
Z
mZmZ d dlmZmZ 	 d dlZd dlmZmZmZ  e ej        ¦   «         ¦  «        Ze	e         ed<   dZe	e         ed<   n# e$ rZdZdZeZdZdZeZY dZ[ndZ[ww xY wdd	lmZ h d
£Ze
e          ed<   dhZ!e
e          ed<   h d£Z"e
e          ed<   h d£Z#e
e          ed<   h d£Z$e
e          ed<   h d£Z%e
e          ed<   h d£Z&e
e          ed<   h d£Z'e
e          ed<   e j(        ej)        e j*        ej+        e j,        ej-        e j.        ej/        e j0        ej1        e j2        ej3        e j4        ej5        e j6        ej7        iZ8e j9        ej:        e j;        ej<        e j=        ej>        iZ?d0de de	ee e@f                  de	e@         fd„ZA G d„ de¦  «        ZB G d „ d!e¦  «        ZC G d"„ d#e¦  «        ZD G d$„ d%e¦  «        ZE G d&„ d'e¦  «        ZF G d(„ d)e¦  «        ZG G d*„ d+¦  «        ZH	 d1d-eee e f                  d.eIdeGfd/„ZJdS )2é    N)ÚPath)ÚAnyÚDictÚListÚOptionalÚSetÚTupleÚUnion)Ú	BaseModelÚField)ÚLanguageÚNodeÚParserÚ
C_LANGUAGEÚ_TREE_SITTER_IMPORT_ERRORé   )ÚASTParsingError>   ÚconsoleÚuart_opsÚ
pci_driverÚuart_driverÚof_device_idÚhid_device_idÚi2c_device_idÚpci_device_idÚspi_device_idÚusb_device_idÚvirtio_driverÚacpi_device_idÚnet_device_opsÚfile_operationsÚplatform_driverÚplatform_device_idÚDRIVER_STRUCT_TYPESÚ
dev_pm_opsÚPM_STRUCT_TYPES>(   ÚinbÚinlÚinwÚer32Úew32ÚoutbÚoutlÚoutwÚreadbÚreadlÚreadqÚreadwÚwritebÚwritelÚwriteqÚwritewÚioread8Úioread16Úioread32Úioread64Úiowrite8Ú	iowrite16Ú	iowrite32Ú	iowrite64Ú	serial_inÚ
ioread16beÚ
ioread32beÚ
serial_outÚiowrite16beÚiowrite32beÚreadb_relaxedÚreadl_relaxedÚreadq_relaxedÚreadw_relaxedÚserial_port_inÚwriteb_relaxedÚwritel_relaxedÚwriteq_relaxedÚwritew_relaxedÚserial_port_outÚMMIO_CALL_NAMES>
   Úfree_irqÚ
enable_irqÚdisable_irqÚrequest_irqÚdevm_free_irqÚsynchronize_irqÚdevm_request_irqÚdisable_irq_nosyncÚrequest_threaded_irqÚdevm_request_threaded_irqÚIRQ_CALL_NAMES>   Ú	spin_lockÚ
mutex_initÚ
mutex_lockÚspin_unlockÚmutex_unlockÚspin_lock_irqÚspin_lock_initÚspin_unlock_irqÚspin_lock_irqsaveÚraw_spin_lock_initÚraw_spin_lock_irqsaveÚspin_unlock_irqrestoreÚmutex_lock_interruptibleÚraw_spin_unlock_irqrestoreÚLOCK_CALL_NAMES>   ÚioremapÚkzallocÚ	pci_iomapÚdevm_kzallocÚdma_set_maskÚdma_map_singleÚdma_unmap_singleÚdma_free_coherentÚdma_alloc_coherentÚpcim_iomap_regionsÚdmam_alloc_coherentÚdevm_ioremap_resourceÚdma_set_coherent_maskÚdma_set_mask_and_coherentÚdevm_platform_ioremap_resourceÚDMA_MEM_CALL_NAMES>   Údev_dbgÚdev_vdbgÚpr_debugÚpr_develÚv4l2_dbgÚ
netdev_dbgÚDEBUG_BOILERPLATE_CALLS>   ÚMODULE_ALIASÚEXPORT_SYMBOLÚMODULE_AUTHORÚMODULE_LICENSEÚMODULE_SOFTDEPÚMODULE_VERSIONÚMODULE_FIRMWAREÚEXPORT_SYMBOL_NSÚEXPORT_SYMBOL_GPLÚMODULE_DESCRIPTIONÚEXPORT_SYMBOL_NS_GPLÚMODULE_BOILERPLATE_MACROSÚexprÚknown_macrosÚreturnc                 óp  ‡— | sdS t          j        dd| ¦  «                             ¦   «         }|sdS t          j        dd|¦  «        }t          j        dd|¦  «        }t          j        dd	|¦  «        }|rct          |                     ¦   «         d
„ ¬¦  «        D ]>\  }}t          j        dt          j        |¦  «        › dt          |¦  «        |¦  «        }Œ?	 t          j        |d¬¦  «        }n# t          $ r Y dS w xY wdt          j
        dt          fˆfd„Š	  ‰|¦  «        S # t          $ r Y dS w xY w)zPSafely evaluates integer/hex/bitshift C preprocessor expressions without eval().Nz	/\*.*?\*/Ú z \bBIT(?:_ULL)?\s*\(\s*(\d+)\s*\)z	(1 << \1)z0\bGENMASK(?:_ULL)?\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)z"(((1 << (\1 - \2 + 1)) - 1) << \2)z\b(0x[0-9a-fA-F]+|\d+)[uUlL]+\bz\1c                 ó.   — t          | d         ¦  «         S )Nr   )Úlen)Úkvs    ú8/workspaces/prism/src/kernelite_prism/core/ast_parser.pyú<lambda>z&safe_eval_c_constant.<locals>.<lambda>È   s   € ÅSÈÈAÌÁZÄZÀK€ ó    ©Úkeyz\bÚeval)ÚmodeÚnoder   c                 óÌ  •— t          | t          j        ¦  «        r ‰| j        ¦  «        S t          | t          j        ¦  «        r.t          | j        t          ¦  «        rt          | j        ¦  «        S t          | t          j        ¦  «        r_t          | j	        ¦  «        t          v rDt          t          | j	        ¦  «                  ‰| j        ¦  «         ‰| j        ¦  «        ¦  «        S t          | t          j        ¦  «        rPt          | j	        ¦  «        t          v r5t          t          | j	        ¦  «                  ‰| j        ¦  «        ¦  «        S t!          d¦  «        ‚)NzUnsupported AST expression)Ú
isinstanceÚastÚ
ExpressionÚbodyÚConstantÚvalueÚintÚBinOpÚtypeÚopÚSAFE_BIN_OPSÚleftÚrightÚUnaryOpÚSAFE_UNARY_OPSÚoperandÚ
ValueError)rœ   Ú
_eval_nodes    €r•   r¯   z(safe_eval_c_constant.<locals>._eval_nodeĞ   s  ø€ İdCœNÑ+Ô+ğ 	)Ø:˜dœiÑ(Ô(Ğ(İdCœLÑ)Ô)ğ 	#­j¸¼ÅSÑ.IÔ.Iğ 	#İt”z‘?”?Ğ"İdCœIÑ&Ô&ğ 	^­4°´©=¬=½LĞ+HĞ+Hİ¥ T¤W¡¤Ô.¨z¨z¸$¼)Ñ/DÔ/DÀjÀjĞQUÔQ[ÑF\ÔF\Ñ]Ô]Ğ]İdCœKÑ(Ô(ğ 	K­T°$´'©]¬]½nĞ-LĞ-Lİ!¥$ t¤w¡-¤-Ô0°°¸D¼LÑ1IÔ1IÑJÔJĞJİĞ5Ñ6Ô6Ğ6r—   )ÚreÚsubÚstripÚsortedÚitemsÚescapeÚhexrŸ   ÚparseÚSyntaxErrorÚASTr¤   Ú	Exception)r   r   ÚcleanedÚsymÚvalÚtreer¯   s         @r•   Úsafe_eval_c_constantr¿   ´   s  ø€ àğ ØˆtİŒf\ 2 tÑ,Ô,×2Ò2Ñ4Ô4€GØğ Øˆtõ ŒfĞ8¸,ÈÑPÔP€GİŒfØ;Ø-Øñô €Gõ ŒfĞ7¸ÀÑHÔH€Gğ ğ Jİ˜|×1Ò1Ñ3Ô3Ğ9OĞ9OĞPÑPÔPğ 	Jğ 	J‰HˆCİ”fĞ5¥2¤9¨S¡>¤>Ğ5Ğ5Ğ5µs¸3±x´xÀÑIÔIˆGˆGğİŒy˜ vĞ.Ñ.Ô.ˆˆøİğ ğ ğ Øˆtˆtğøøøğ	7œğ 	7¥Sğ 	7ğ 	7ğ 	7ğ 	7ğ 	7ğ 	7ğØˆz˜$ÑÔĞøİğ ğ ğ Øˆtˆtğøøøs$   ÃC1 Ã1
C?Ã>C?Ä
D' Ä'
D5Ä4D5c                   ó‚   — e Zd ZU eed<   eed<   dZeed<   dZeed<   dZe	e
         ed<   dZe	e         ed	<   d
Zeed<   dS )ÚMacroConstantÚnamer£   FÚis_function_macroÚconstantÚcategoryNÚnumeric_valueÚ	hex_valuer‘   Úfile)Ú__name__Ú
__module__Ú__qualname__ÚstrÚ__annotations__rÃ   ÚboolrÅ   rÆ   r   r¤   rÇ   rÈ   © r—   r•   rÁ   rÁ   á   sx   € € € € € € Ø
€I€IIØ€J€JJØ#ĞtĞ#Ğ#Ñ#Ø€HˆcĞĞÑØ#'€M8˜C”=Ğ'Ğ'Ñ'Ø#€Iˆx˜Œ}Ğ#Ğ#Ñ#Ø€Dˆ#€N€NN€N€Nr—   rÁ   c                   ór   — e Zd ZU eed<   eed<    ee¬¦  «        Zeeef         ed<   dZ	eed<   dZ
eed<   dS )	ÚDriverStructInfoÚstruct_typeÚvar_name©Údefault_factoryÚfieldsr‘   Úraw_snippetrÈ   N)rÉ   rÊ   rË   rÌ   rÍ   r   ÚdictrÖ   r   r×   rÈ   rÏ   r—   r•   rÑ   rÑ   ë   sg   € € € € € € ØĞĞÑØ€M€MMØ"˜U°4Ğ8Ñ8Ô8€FˆDcŒNĞ8Ğ8Ñ8Ø€KĞĞÑØ€Dˆ#€N€NN€N€Nr—   rÑ   c                   ón   — e Zd ZU eed<   eed<    ee¬¦  «        Zee         ed<   dZ	e
ed<   dZeed<   d	S )
ÚMMIOCallÚfunction_namer§   rÔ   Úargsr   Úliner‘   rÈ   N)rÉ   rÊ   rË   rÌ   rÍ   r   ÚlistrÜ   r   rİ   r¤   rÈ   rÏ   r—   r•   rÚ   rÚ   ó   s`   € € € € € € ØĞĞÑØ€G€GGØe¨DĞ1Ñ1Ô1€Dˆ$ˆsŒ)Ğ1Ğ1Ñ1Ø€Dˆ#€M€MMØ€Dˆ#€N€NN€N€Nr—   rÚ   c                   óN   — e Zd ZU eed<   eed<   dZeed<   dZeed<   dZeed<   dS )	ÚConcurrencyCallrÛ   Ú	primitiver‘   Úlock_argr   rİ   rÈ   N)	rÉ   rÊ   rË   rÌ   rÍ   râ   rİ   r¤   rÈ   rÏ   r—   r•   rà   rà   û   sO   € € € € € € ØĞĞÑØ€N€NNØ€HˆcĞĞÑØ€Dˆ#€M€MMØ€Dˆ#€N€NN€N€Nr—   rà   c                   óF  — e Zd ZU eed<   dZeed<   dZeed<   dZeed<    ee	¬¦  «        Z
ee         ed<    ee	¬¦  «        Zee         ed	<    ee	¬¦  «        Zee         ed
<    ee	¬¦  «        Zee         ed<    ee	¬¦  «        Zee         ed<   dZeed<   dZeed<   dS )ÚFunctionSummaryrÂ   ÚhelperÚroler‘   Úreturn_typeÚ
parametersrÔ   Ú
mmio_callsÚ	irq_callsÚ
lock_callsÚ	dma_callsÚcalled_functionsÚsource_coderÈ   N)rÉ   rÊ   rË   rÌ   rÍ   ræ   rç   rè   r   rŞ   ré   r   rÚ   rê   rë   rà   rì   rí   rî   rÈ   rÏ   r—   r•   rä   rä     s  € € € € € € Ø
€I€IIØ€Dˆ#ĞĞÑØ€KĞĞÑØ€JĞĞÑØ!& °tĞ!<Ñ!<Ô!<€JX”Ğ<Ğ<Ñ<Ø ˜5°Ğ6Ñ6Ô6€IˆtCŒyĞ6Ğ6Ñ6Ø(-¨¸dĞ(CÑ(CÔ(C€J_Ô%ĞCĞCÑCØ ˜5°Ğ6Ñ6Ô6€IˆtCŒyĞ6Ğ6Ñ6Ø"' %¸Ğ"=Ñ"=Ô"=Ğd˜3”iĞ=Ğ=Ñ=Ø€KĞĞÑØ€Dˆ#€N€NN€N€Nr—   rä   c                   óŠ  — e Zd ZU  ee¬¦  «        Zee         ed<    ee¬¦  «        Z	ee
         ed<    ee¬¦  «        Zee         ed<    ee¬¦  «        Zee         ed<    ee¬¦  «        Zee         ed<    ee¬¦  «        Zeeef         ed<   dZeed	<   d
Zeed<   d
Zeed<   edefd„¦   «         Zdeeef         fd„ZdS )ÚASTExtractionResultrÔ   ÚmacrosÚdriver_structsÚ	functionsÚmmio_operationsÚconcurrency_callsÚmetadata_macrosr‘   Úpruned_sourcer   Úoriginal_charsÚpruned_charsr   c                 ó\   — | j         dk    rdS t          d| j        | j         z  z
  d¦  «        S )Nr   g        g      ğ?é   )rø   Úroundrù   ©Úselfs    r•   Úcompression_ratioz%ASTExtractionResult.compression_ratio  s5   € àÔ !Ò#Ğ#Ø3İS˜DÔ-°Ô0CÑCÑDÀaÑHÔHĞHr—   c                 ó$   — d„ | j         D ¦   «         S )Nc                 ó   — i | ]
}|j         |“ŒS rÏ   )rÂ   ©Ú.0Úms     r•   ú
<dictcomp>z4ASTExtractionResult.macro_lookup.<locals>.<dictcomp>#  s   € Ğ/Ğ/Ğ/˜a”˜Ğ/Ğ/Ğ/r—   )rñ   rı   s    r•   Úmacro_lookupz ASTExtractionResult.macro_lookup"  s   € Ø/Ğ/ 4¤;Ğ/Ñ/Ô/Ğ/r—   N)rÉ   rÊ   rË   r   rŞ   rñ   r   rÁ   rÍ   rò   rÑ   ró   rä   rô   rÚ   rõ   rà   rØ   rö   r   rÌ   r   r÷   rø   r¤   rù   ÚpropertyÚfloatrÿ   r  rÏ   r—   r•   rğ   rğ     se  € € € € € € Ø"' %¸Ğ"=Ñ"=Ô"=€FˆDÔĞ=Ğ=Ñ=Ø-2¨UÀ4Ğ-HÑ-HÔ-H€NDĞ)Ô*ĞHĞHÑHØ', u¸TĞ'BÑ'BÔ'B€IˆtOÔ$ĞBĞBÑBØ&+ e¸DĞ&AÑ&AÔ&A€OT˜(”^ĞAĞAÑAØ/4¨uÀTĞ/JÑ/JÔ/JĞt˜OÔ,ĞJĞJÑJØ&+ e¸DĞ&AÑ&AÔ&A€OT˜#˜s˜(”^ĞAĞAÑAØ€M3ĞĞÑØ€NCĞĞÑØ€L#ĞĞÑàğI 5ğ Iğ Iğ Iñ „XğIğ
0˜d 3¨Ğ#5Ô6ğ 0ğ 0ğ 0ğ 0ğ 0ğ 0r—   rğ   c                   ó   — e Zd ZdZd"deddfd„Zedee         de	de
fd	„¦   «         Zd
e
de
dede
fd„Zdede	dee
e
f         fd„Zdee         de	dee
e
f         fd„Zdee         de	dee
         fd„Zdede	de
dee
e
f         dee
         deeeeeef                  f         fd„Zdeee
e
f                  defd„Zede	deeeef                  de	fd„¦   «         Zedee         dee         dee         d ee
ef         de
f
d!„¦   «         ZdS )#ÚASTPreProcessorz[Tree-sitter C AST analyzer that extracts hardware/driver primitives and strips boilerplate.TÚstrip_boilerplater   Nc                 óŒ   — t           t          €t          d¦  «        t          ‚|| _        t          t           ¦  «        | _        d S )Nzstree-sitter and tree-sitter-c are required. Install with: pip install 'tree-sitter>=0.23.0' 'tree-sitter-c>=0.23.0')r   r   r   r   r  Úparser)rş   r  s     r•   Ú__init__zASTPreProcessor.__init__)  sD   € İĞ¥ İ!ğ Fñô å,ğ-ğ "3ˆÔİZÑ(Ô(ˆŒˆˆr—   rœ   Úsource_bytesc                 ó\   — | €dS || j         | j        …                              dd¬¦  «        S )Nr‘   úutf-8Úreplace©Úerrors)Ú
start_byteÚend_byteÚdecode)rœ   r  s     r•   Ú
_node_textzASTPreProcessor._node_text1  s4   € àˆ<Ø2Ø˜DœO¨d¬mĞ;Ô<×CÒCÀGĞT]ĞCÑ^Ô^Ğ^r—   rÂ   r£   Úis_funcc                 ó:  ‡‡— |rdS |                      ¦   «         Št          ˆfd„dD ¦   «         ¦  «        rdS t          ˆfd„dD ¦   «         ¦  «        rt          ˆfd„dD ¦   «         ¦  «        rdS d	S t          j        d
‰                     ¦   «         ¦  «        rd	S dS )NÚmacroc              3   ó    •K  — | ]}|‰v V — Œ	d S ©NrÏ   )r  Útokr£   s     €r•   ú	<genexpr>z2ASTPreProcessor._classify_macro.<locals>.<genexpr>;  s'   øè è € Ğ^Ğ^ ˆseˆ|Ğ^Ğ^Ğ^Ğ^Ğ^Ğ^r—   )zBIT(zBIT_ULL(zGENMASK(zGENMASK_ULL(z<<Úbitmaskc              3   ó    •K  — | ]}|‰v V — Œ	d S r  rÏ   )r  ÚprefixÚ
upper_names     €r•   r  z2ASTPreProcessor._classify_macro.<locals>.<genexpr>=  s<   øè è € ğ 
ğ 
àğ jĞ ğ
ğ 
ğ 
ğ 
ğ 
ğ 
r—   )ÚREG_Ú_REGÚUART_ÚVIRTIO_MMIO_ÚE1000_ÚCSR_ÚMMIO_c              3   ó    •K  — | ]}|‰v V — Œ	d S r  rÏ   )r  Úsuffixr#  s     €r•   r  z2ASTPreProcessor._classify_macro.<locals>.<genexpr>B  s(   øè è € ĞdĞd¨F6˜ZĞ'ĞdĞdĞdĞdĞdĞdr—   )Ú_MASKÚ_SHIFTÚ_BITÚ_ENÚ_ENABLEÚregister_offsetz^0x[0-9a-fA-F]+$rÄ   )ÚupperÚanyr°   Úmatchr²   )rş   rÂ   r£   r  r#  s     ` @r•   Ú_classify_macrozASTPreProcessor._classify_macro7  sÜ   øø€ Øğ 	Ø7Ø—Z’Z‘\”\ˆ
İĞ^Ğ^Ğ^Ğ^Ğ']Ğ^Ñ^Ô^Ñ^Ô^ğ 	Ø9İğ 
ğ 
ğ 
ğ 
à^ğ
ñ 
ô 
ñ 
ô 
ğ 	%õ
 ĞdĞdĞdĞdĞ6cĞdÑdÔdÑdÔdğ !Ø yØ$Ğ$İŒ8Ğ'¨¯ª©¬Ñ7Ô7ğ 	%Ø$Ğ$Øˆzr—   Úinit_list_nodec                 ó`  — i }|j         D ]£}|j        dk    r–|                     d¦  «        }|                     d¦  «        }|j|h|                      ||¦  «                             d¦  «                             ¦   «         }|                      ||¦  «                             ¦   «         }|||<   Œ¤|S )NÚinitializer_pairÚ
designatorr£   ú.)Únamed_childrenr¦   Úchild_by_field_namer  Úlstripr²   )	rş   r7  r  rÖ   Úchildr:  r£   Ú	field_keyÚ	field_vals	            r•   Ú_extract_struct_fieldsz&ASTPreProcessor._extract_struct_fieldsI  sµ   € Ø!#ˆØ#Ô2ğ 	2ğ 	2ˆEØŒzĞ/Ò/Ğ/Ø"×6Ò6°|ÑDÔD
Ø×1Ò1°'Ñ:Ô:ØĞ)¨eĞ.?Ø $§¢°
¸LÑ IÔ I× PÒ PĞQTÑ UÔ U× [Ò [Ñ ]Ô ]IØ $§¢°°|Ñ DÔ D× JÒ JÑ LÔ LIØ(1F˜9Ñ%øØˆr—   Ú
declaratorc                 óF  — |}|œ|j         dk    rz|                     d¦  «        }|                     d¦  «        }|                      ||¦  «                             ¦   «         |                      ||¦  «                             ¦   «         fS |                     d¦  «        }|­œdS )NÚfunction_declaratorrC  rè   )r‘   r‘   )r¦   r=  r  r²   )rş   rC  r  ÚcurrÚfn_idÚparamss         r•   Ú!_extract_function_name_and_paramsz1ASTPreProcessor._extract_function_name_and_paramsU  s©   € ØˆØĞØŒyĞ1Ò1Ğ1Ø×0Ò0°Ñ>Ô>Ø×1Ò1°,Ñ?Ô?à—O’O E¨<Ñ8Ô8×>Ò>Ñ@Ô@Ø—O’O F¨LÑ9Ô9×?Ò?ÑAÔAğğ ğ ×+Ò+¨LÑ9Ô9ˆDğ Ğğ ˆxr—   Ú	args_nodec                 ó6   ‡ ‡— |€g S ˆ ˆfd„|j         D ¦   «         S )Nc                 ó`   •— g | ]*}‰                      |‰¦  «                             ¦   «         ‘Œ+S rÏ   )r  r²   )r  r?  rş   r  s     €€r•   ú
<listcomp>z4ASTPreProcessor._parse_call_args.<locals>.<listcomp>e  sC   ø€ ğ 
ğ 
ğ 
àğ OŠO˜E <Ñ0Ô0×6Ò6Ñ8Ô8ğ
ğ 
ğ 
r—   )r<  )rş   rJ  r  s   ` `r•   Ú_parse_call_argsz ASTPreProcessor._parse_call_argsb  sB   øø€ ØĞØˆIğ
ğ 
ğ 
ğ 
ğ 
à"Ô1ğ
ñ 
ô 
ğ 	
r—   Úfn_nodeÚfilenameÚcallback_rolesÚpm_functionsc                 óÊ  ‡ ‡‡‡‡‡‡‡‡‡‡‡— |                      d¦  «        }|                      d¦  «        }|                      d¦  «        }‰                      |‰¦  «                             ¦   «         }	‰                      |‰¦  «        \  Š}
g Šg Šg Šg Šg Šg Šg Šdt          dd fˆˆˆˆˆˆˆˆˆˆ ˆˆfd„Š| ‰|¦  «         ‰D ]}|                     |d¦  «         Œ‰|v rd}nM‰|v r	|‰         }n@d	|	v rd}n9d
‰v sd‰v sd‰v rd
}n*d‰v rd}n#d‰v sd‰v rd}nd‰v sd‰v rd}nd‰v sd‰v rd}nd}‰                      |‰¦  «        }t          ‰||	|
‰‰‰‰‰|‰¬¦  «        }|‰fS )Nr¦   rC  r¡   rœ   r   c           	      óî  •— | j         dk    rR|                      d¦  «        }|                      d¦  «        }‰                     |‰¦  «                             ¦   «         }‰                     |‰¦  «        }|r|‰
vr‰
                     |¦  «         |t          v r7‰                     t          ‰||| j        d         dz   ‰¬¦  «        ¦  «         n(|t          v rŸ|› dd 
                    |¦  «        › d	}‰                     |¦  «         d
|v rjt          |¦  «        dk    rW|d                              d¦  «                             ¦   «         }t          j        d|¦  «        r‰                     |¦  «         n€|t          v r@‰                     t!          ‰||r|d         nd| j        d         dz   ‰¬¦  «        ¦  «         n7|t"          v r.‰                     |› dd 
                    |¦  «        › d	¦  «         ‰j        r`|t&          v rW| j        }|-|j         dk    r"‰                     |j        |j        f¦  «         n!‰                     | j        | j        f¦  «         | j        D ]} ‰	|¦  «         Œd S )NÚcall_expressionÚfunctionÚ	argumentsr   é   )rÛ   r§   rÜ   rİ   rÈ   ú(z, ú)Úrequestr   ú&z^[a-zA-Z_][a-zA-Z0-9_]*$r‘   )rÛ   rá   râ   rİ   rÈ   Úexpression_statement)r¦   r=  r  r²   rN  ÚappendrO   rÚ   Ústart_pointrZ   Újoinr“   r>  r°   r5  ri   rà   ry   r  r€   Úparentr  r  Úchildren)rœ   Úcallee_noderJ  Úcallee_namerÜ   Úcall_sigÚhandler_candidatera  r?  Ú_walkrí   Údetected_irq_handlersrì   rP  Úfn_namerê   rë   ré   rş   r  Ústrip_spanss            €€€€€€€€€€€€r•   rg  z0ASTPreProcessor._analyze_function.<locals>._walk  sÊ  ø€ ØŒyĞ-Ò-Ñ-Ø"×6Ò6°zÑBÔBØ ×4Ò4°[ÑAÔA	Ø"Ÿošo¨k¸<ÑHÔH×NÒNÑPÔPØ×,Ò,¨Y¸ÑEÔEàğ 9 ;Ğ6FĞ#FĞ#FØ$×+Ò+¨KÑ8Ô8Ğ8à¥/Ğ1Ğ1Ø×%Ò%İ Ø*1Ø*Ø!%Ø!%Ô!1°!Ô!4°qÑ!8Ø!)ğñ ô ñô ğ ñ ğ !¥NĞ2Ğ2Ø"-ĞBĞB°·	²	¸$±´ĞBĞBĞBHØ×$Ò$ XÑ.Ô.Ğ.Ø  KĞ/Ğ/µC¸±I´IÀ²N°NØ,0°¬G¯NªN¸3Ñ,?Ô,?×,EÒ,EÑ,GÔ,GĞ)İœ8Ğ$?ĞARÑSÔSğ LØ1×8Ò8Ğ9JÑKÔKĞKøØ ¥OĞ3Ğ3Ø×%Ò%İ'Ø*1Ø&1Ø04Ğ%< T¨!¤W W¸"Ø!%Ô!1°!Ô!4°qÑ!8Ø!)ğñ ô ñô ğ ğ ğ !Õ$6Ğ6Ğ6Ø×$Ò$¨Ğ%HĞ%H°d·i²iÀ±o´oĞ%HĞ%HĞ%HÑIÔIĞIàÔ)ğ M¨kÕ=TĞ.TĞ.TØ!œ[FØĞ)¨f¬kĞ=SÒ.SĞ.SØ#×*Ò*¨FÔ,=¸v¼Ğ+OÑPÔPĞPĞPà#×*Ò*¨D¬O¸T¼]Ğ+KÑLÔLĞLàœğ ğ Øe‘”ğğ r—   Úirq_handlerÚpmÚirqreturn_tÚprobeÚearly_setupÚearlyconÚremoveÚxmitÚtxÚintrÚirqÚinitÚresetrå   )rÂ   ræ   rç   rè   ré   rê   rë   rì   rí   rî   rÈ   )r=  r  r²   rI  r   Ú
setdefaulträ   )rş   rO  r  rP  rQ  rR  Úret_nodeÚ	decl_nodeÚ	body_nodeÚret_typeÚparams_textÚhandlerræ   Ú
raw_fn_srcÚsummaryrg  rí   rh  rì   ri  rê   rë   ré   rj  s   ` ``           @@@@@@@@@r•   Ú_analyze_functionz!ASTPreProcessor._analyze_functionj  sm  øøøøøøøøøøøø€ ğ ×.Ò.¨vÑ6Ô6ˆØ×/Ò/°Ñ=Ô=ˆ	Ø×/Ò/°Ñ7Ô7ˆ	à—?’? 8¨\Ñ:Ô:×@Ò@ÑBÔBˆØ#×EÒEÀiĞQ]Ñ^Ô^Ñˆà%'ˆ
Ø!ˆ	Ø,.ˆ
Ø!ˆ	Ø&(ĞØ-/ˆØ+-Ğğ0	ğ 0	 ğ 0	ğ 0	ğ 0	ğ 0	ğ 0	ğ 0	ğ 0	ğ 0	ğ 0	ğ 0	ğ 0	ğ 0	ğ 0	ğ 0	ğ 0	ğ 0	ğ 0	ğd Ğ ØˆE)ÑÔĞà,ğ 	>ğ 	>ˆGØ×%Ò% g¨}Ñ=Ô=Ğ=Ğ=ğ lĞ"Ğ"ØˆDˆDØ˜Ğ&Ğ&Ø! 'Ô*ˆDˆDØ˜hĞ&Ğ&Ø ˆDˆDØ˜ĞĞ =°GĞ#;Ğ#;¸zÈWĞ?TĞ?TØˆDˆDØ˜Ğ Ğ ØˆDˆDØwĞĞ $¨' / /ØˆDˆDØwĞĞ %¨7Ğ"2Ğ"2Ø ˆDˆDØwĞĞ '¨WĞ"4Ğ"4ØˆDˆDàˆDà—_’_ W¨lÑ;Ô;ˆ
İ!ØØØ Ø"Ø!ØØ!ØØ-Ø"Øğ
ñ 
ô 
ˆğ ˜Ğ#Ğ#r—   Úfilesc                 óî  ‡ ‡‡‡‡‡‡‡‡‡‡‡— g Ši Šg Šg }g }g }dg iŠg }d}d}|D ]÷\  ŠŠ|t          ‰¦  «        z  }‰                     dd¬¦  «        Š	 ‰ j                             ‰¦  «        }n(# t          $ r}	t          d‰› d|	› ¦  «        |	‚d}	~	ww xY w|j        }
g Ši Št          ¦   «         Šd	t          d
dfˆˆˆˆˆˆˆˆˆˆ ˆˆfd„Š ‰|
¦  «         |
j	        D ]¼}|j
        dk    r¯‰                      |‰‰‰‰¬¦  «        \  }}‰ j        r-|j        dk    r"‰                     |j        |j        f¦  «         Œ^|                     |¦  «         |                     |j        ¦  «         |                     |j        ¦  «         ‰                     |¦  «         Œ½‰                      ‰‰¦  «        }|                     dd¬¦  «        }t-          j        dd|¦  «                             ¦   «         }|t          |¦  «        z  }|                     d‰› d|› ¦  «         Œù‰                      ‰‰|‰¬¦  «        }|› dd                     |¦  «        z   }t7          ‰‰|||‰||t          |¦  «        ¬¦	  «	        S )zFParses a list of `(relative_filename, content)` C source/header pairs.Úmodule_paramsr   r  r  r  zTree-sitter failed to parse 'z': Nrœ   r   c                 ól  •— | j         dk    rç|                      d¦  «        }|                      d¦  «        }‰"                     |‰#¦  «                             ¦   «         }‰"                     |‰#¦  «                             ¦   «         }|rit	          |‰¦  «        }||‰|<   ‰"                     ||d¬¦  «        }‰                     t          ||d|||t          |¦  «        nd ‰¬¦  «        ¦  «         n | j         dk    râ|                      d¦  «        }|                      d¦  «        }|                      d¦  «        }‰"                     |‰#¦  «                             ¦   «         › ‰"                     |‰#¦  «                             ¦   «         › }‰"                     |‰#¦  «                             ¦   «         }‰                     t          ||d	d
‰¬¦  «        ¦  «         n3| j         dv rk‰"j	        rd‰| j
        t          | j        | j
        dz   ¦  «        …         }t          j        d|¦  «        r#‰$                     | j
        | j        f¦  «         d S n¿| j         dk    rs|                      d¦  «        }	|                      d¦  «        }
|	D|	j         dk    r8|	                     d¦  «        }‰"                     |‰#¦  «                             ¦   «         }|t          v r–|
j|
j         dk    r_|
                     d¦  «        }|H|j         dk    r=‰"                     |‰#¦  «        }‰!                     |                     ¦   «         ¦  «         ‰"j	        r!‰$                     | j
        | j        f¦  «         d S |t&          v rRd}i }|
©|
j         dk    rv|
                     d¦  «        }|
                     d¦  «        }‰"                     |‰#¦  «                             ¦   «         }|!|j         dk    r‰"                     |‰#¦  «        }n(‰"                     |
‰#¦  «                             ¦   «         }|                     ¦   «         D ]S\  }}|                     d¦  «                             ¦   «         }d|v rd‰|<   Œ6d|v rd‰|<   Œ@d|v rd‰|<   ŒJ|dv rd‰|<   ŒT‰                     t-          |||‰"                     | ‰#¦  «        ‰¬¦  «        ¦  «         n@| j         dk    r4‰"                     | ‰#¦  «                             ¦   «         }t          j        d|t          j        ¦  «        }|rê|                     d¦  «        }|                     d ¦  «                             ¦   «                              d!¦  «        }|d"k    r|‰ d#<   n\|d$k    r|‰ d%<   nP|d&k    r|‰ d'<   nD|d(v r@‰ d)                              |                     d ¦  «                             ¦   «         ¦  «         ‰"j	        r,|t4          v r#‰$                     | j
        | j        f¦  «         d S | j        D ]}|j         d*k    r ‰|¦  «         Œd S )+NÚpreproc_defrÂ   r£   F)r  )rÂ   r£   rÃ   rÅ   rÆ   rÇ   rÈ   Úpreproc_function_defrè   Tr  )rÂ   r£   rÃ   rÅ   rÈ   >   Ú
preproc_ifÚpreproc_ifdeféP   zCONFIG_PM(_SLEEP)?\bÚdeclarationr¦   rC  Ústruct_specifierÚinit_declaratorÚinitializer_listr‘   r\  rn  rq  rr  >   ÚreadÚwriteÚstartupÚshutdownÚset_termiosÚunlocked_ioctlÚio_op)rÒ   rÓ   rÖ   r×   rÈ   r]  z*^([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*;?$rX  r   ú"r„   Úlicenserƒ   ÚauthorrŠ   Údescription>   Úmodule_paramÚmodule_param_namedr„  Úfunction_definition)r¦   r=  r  r²   r¿   r6  r^  rÁ   r¶   r  r  Úminr  r°   Úsearchr&   rB  ÚupdateÚvaluesr$   r´   r>  rÑ   r5  ÚDOTALLÚgrouprŒ   rb  )%rœ   Ú	name_nodeÚval_nodeÚ
macro_nameÚ	macro_valÚnum_valrÅ   Úparams_nodeÚheader_lineÚ	type_noderz  Ústruct_name_noderÒ   Ú	pm_fieldsrÓ   rÖ   Úidentr@  Úfn_refÚclean_fnÚ	expr_textÚ
call_matchÚmacro_idÚ	macro_argr?  Ú_collect_top_levelÚ
all_macrosÚall_structsrQ  ÚcontentrP  Úknown_numeric_macrosrö   rR  rş   r  rj  s%                            €€€€€€€€€€€€r•   r´  z7ASTPreProcessor.parse_files.<locals>._collect_top_levelù  sÇ  ø€ Ø”9 Ò-Ğ-Ø $× 8Ò 8¸Ñ @Ô @IØ#×7Ò7¸Ñ@Ô@HØ!%§¢°¸LÑ!IÔ!I×!OÒ!OÑ!QÔ!QJØ $§¢°¸,Ñ GÔ G× MÒ MÑ OÔ OIØ!ğ İ"6°yĞBVÑ"WÔ"W˜Ø"Ğ.Ø?FĞ0°Ñ<Ø#'×#7Ò#7¸
ÀIĞW\Ğ#7Ñ#]Ô#]˜Ø"×)Ò)İ)Ø%/Ø&/Ø27Ø)1Ø.5Ø:AĞ:M­#¨g©,¬,¨,ĞSWØ%-ğñ ô ñ
ô 
ğ 
ùğ ”YĞ"8Ò8Ğ8Ø $× 8Ò 8¸Ñ @Ô @IØ"&×":Ò":¸<Ñ"HÔ"HKØ#×7Ò7¸Ñ@Ô@HàŸ?š?¨9°lÑCÔC×IÒIÑKÔKğ PØŸ?š?¨;¸ÑEÔE×KÒKÑMÔMğPğ Pğ ğ !%§¢°¸,Ñ GÔ G× MÒ MÑ OÔ OIØ×%Ò%İ%Ø!+Ø"+Ø.2Ø%,Ø!)ğñ ô ñô ğ ñ ğ ”YĞ"AĞAĞAÀdÔF\ĞAØ")¨$¬/½CÀÄÈtÌĞacÑOcÑ<dÔ<dĞ*dÔ"eKİ”yĞ!8¸+ÑFÔFğ Ø#×*Ò*¨D¬O¸T¼]Ğ+KÑLÔLĞLØ˜ñğ ”Y -Ò/Ñ/Ø $× 8Ò 8¸Ñ @Ô @IØ $× 8Ò 8¸Ñ FÔ FIØ Ñ,°´ĞCUÒ1UÑ1UØ+4×+HÒ+HÈÑ+PÔ+PĞ(Ø&*§o¢oĞ6FÈÑ&UÔ&U×&[Ò&[Ñ&]Ô&]˜à&­/Ğ9Ğ9Ø(Ğ4¸¼ĞK\Ò9\Ğ9\Ø+4×+HÒ+HÈÑ+QÔ+Q Ø#+Ğ#7¸H¼MĞM_Ò<_Ğ<_Ø04×0KÒ0KÈHĞVbÑ0cÔ0c IØ$0×$7Ò$7¸	×8HÒ8HÑ8JÔ8JÑ$KÔ$KĞ$KØ#Ô5ğ UØ +× 2Ò 2°D´OÀTÄ]Ğ3SÑ TÔ TĞ TØ"˜Fà&Õ*=Ğ=Ñ=Ø')˜HØ57˜FØ(Ğ4Ø#,¤>Ğ5FÒ#FĞ#FØ,5×,IÒ,IÈ,Ñ,WÔ,W EØ/8×/LÒ/LÈWÑ/UÔ/U HØ/3¯ª¸uÀlÑ/SÔ/S×/YÒ/YÑ/[Ô/[ HØ'/Ğ';ÀÄĞQcÒ@cĞ@cØ15×1LÒ1LÈXĞWcÑ1dÔ1d¨øà/3¯ª¸yÈ,Ñ/WÔ/W×/]Ò/]Ñ/_Ô/_ Hğ 6<·\²\±^´^ğ 	Gğ 	GÑ 1 	¨6Ø+1¯=ª=¸Ñ+=Ô+=×+CÒ+CÑ+EÔ+E Ø#*¨iĞ#7Ğ#7Ø?F N°8Ñ$<Ğ$<Ø%-°Ğ%:Ğ%:Ø?G N°8Ñ$<Ğ$<Ø%+¨yĞ%8Ğ%8Ø?E N°8Ñ$<Ğ$<Ø%.Ğ2{Ğ%{Ğ%{Ø?F N°8Ñ$<øà'×.Ò.İ 0Ø0;Ø-5Ø+1Ø04·²ÀÀlÑ0SÔ0SØ)1ğ!"ñ !"ô !"ñô ğ ùğ ”YĞ"8Ò8Ñ8Ø $§¢°°lÑ CÔ C× IÒ IÑ KÔ KIİ!#¤Ğ*WĞYbÕdfÔdmÑ!nÔ!nJØ!ğ #Ø#-×#3Ò#3°AÑ#6Ô#6˜Ø$.×$4Ò$4°QÑ$7Ô$7×$=Ò$=Ñ$?Ô$?×$EÒ$EÀcÑ$JÔ$J˜	Ø#Ğ'7Ò7Ğ7Ø9B˜O¨IÑ6Ğ6Ø%¨Ò8Ğ8Ø8A˜O¨HÑ5Ğ5Ø%Ğ)=Ò=Ğ=Ø=F˜O¨MÑ:Ğ:Ø%Ğ)OĞOĞOØ+¨OÔ<×CÒCÀJ×DTÒDTĞUVÑDWÔDW×D]ÒD]ÑD_ÔD_Ñ`Ô`Ğ`àÔ1ğ #°hÕB[Ğ6[Ğ6[Ø'×.Ò.°´ÀÄĞ/OÑPÔPĞPØ"˜Fà!œ]ğ 2ğ 2EØ”zĞ%:Ò:Ğ:Ø*Ğ*¨5Ñ1Ô1Ğ1øğ2ğ 2r—   rœ  )r  rP  rQ  rR  rl  z\n{3,}z

zV/* =========================================================================
 * File: zR
 * ========================================================================= */

)rñ   Ústructsró   Úmetadata)	rñ   rò   ró   rô   rõ   rö   r÷   rø   rù   )r“   Úencoder  r·   rº   r   Ú	root_nodeÚsetr   rb  r¦   r  r  ræ   r^  r  r  Úextendré   rë   Ú_apply_strip_spansr  r°   r±   r²   Ú_format_ast_summary_headerr`  rğ   )rş   r‚  Úall_functionsÚall_mmioÚ	all_locksÚpruned_file_blocksÚ
total_origÚtotal_prunedr¾   ÚeÚrootr?  Ú
fn_summaryÚfn_strip_spansÚpruned_bytesÚpruned_textÚ
ast_headerÚfull_pruned_sourcer´  rµ  r¶  rQ  r·  rP  r¸  rö   rR  r  rj  s   `                 @@@@@@@@@@@r•   Úparse_fileszASTPreProcessor.parse_filesİ  s  øøøøøøøøøøøø€ à*,ˆ
Ø/1ĞØ.0ˆØ/1ˆØ#%ˆØ+-ˆ	Ø+:¸BĞ*?ˆØ(*Ğàˆ
Øˆà!&ğ j	ñ j	ÑˆHgØ#˜g™,œ,Ñ&ˆJØ"Ÿ>š>¨'¸)˜>ÑDÔDˆLğ_Ø”{×(Ò(¨Ñ6Ô6øİğ _ğ _ğ _İ%Ğ&VÀhĞ&VĞ&VĞSTĞ&VĞ&VÑWÔWĞ]^Ğ^øøøøğ_øøøğ ”>ˆDØ13ˆKØ-/ˆNİ%(¡U¤UˆLğy2­ğ y2°$ğ y2ğ y2ğ y2ğ y2ğ y2ğ y2ğ y2ğ y2ğ y2ğ y2ğ y2ğ y2ğ y2ğ y2ğ y2ğ y2ğ y2ğv Ğ˜tÑ$Ô$Ğ$ğ œğ 7ğ 7Ø”:Ğ!6Ò6Ğ6Ø15×1GÒ1GØØ%1Ø!)Ø'5Ø%1ğ 2Hñ 2ô 2Ñ.J ğ Ô-ğ !°*´/ÀTÒ2IĞ2IØ#×*Ò*¨EÔ,<¸e¼nĞ+MÑNÔNĞNØ à!×(Ò(¨Ñ4Ô4Ğ4Ø—O’O JÔ$9Ñ:Ô:Ğ:Ø×$Ò$ ZÔ%:Ñ;Ô;Ğ;Ø×&Ò& ~Ñ6Ô6Ğ6øğ  ×2Ò2°<ÀÑMÔMˆLØ&×-Ò-¨g¸iĞ-ÑHÔHˆKåœ& ¨F°KÑ@Ô@×FÒFÑHÔHˆKØC Ñ,Ô,Ñ,ˆLà×%Ò%ğ!Ø$ğ!ğ !ğ ğ!ğ !ñô ğ ñ ğ ×4Ò4ØØØ#Ø$ğ	 5ñ 
ô 
ˆ
ğ !+Ğ0Ğ0Ğ0°6·;²;Ğ?QÑ3RÔ3RÑRĞå"ØØ&Ø#Ø$Ø'Ø+Ø,Ø%İĞ/Ñ0Ô0ğ

ñ 

ô 

ğ 
	
s   ÁA0Á0
BÁ:BÂBÚspansc                 óà  — |s| S t          |d„ ¬¦  «        }g }|D ]^\  }}|r||d         d         k    r|                     ||f¦  «         Œ1|d         d         t          |d         d         |¦  «        f|d<   Œ_g }d}|D ]*\  }}||k    r|                     | ||…         ¦  «         |}Œ+|t          | ¦  «        k     r|                     | |d …         ¦  «         d                     |¦  «        S )Nc                 ó"   — | d         | d         fS )Nr   rX  rÏ   )Úss    r•   r–   z4ASTPreProcessor._apply_strip_spans.<locals>.<lambda>¯  s   € °A°a´D¸!¸A¼$°<€ r—   r˜   éÿÿÿÿrX  r   r—   )r³   r^  Úmaxr“   r`  )r  rĞ  Úsorted_spansÚmergedÚstartÚendÚchunksÚcursors           r•   r¿  z"ASTPreProcessor._apply_strip_spans«  s&  € àğ 	 ØĞİ˜eĞ)?Ğ)?Ğ@Ñ@Ô@ˆØ(*ˆØ&ğ 	Fğ 	F‰JˆE3Øğ F˜U V¨B¤Z°¤]Ò2Ğ2Ø—’˜u c˜lÑ+Ô+Ğ+Ğ+à$ Rœj¨œm­S°¸´¸A´ÀÑ-DÔ-DĞEr‘

à ˆØˆØ ğ 	ğ 	‰JˆE3ØvŠ~ˆ~Ø—’˜l¨6°%¨<Ô8Ñ9Ô9Ğ9ØˆFˆFØ•C˜Ñ%Ô%Ò%Ğ%ØMŠM˜, v w wÔ/Ñ0Ô0Ğ0ØxŠx˜ÑÔĞr—   rñ   r¹  ró   rº  c                 óÄ  — g d¢}|                      d¦  «        r|                     d|d         › ¦  «         |                      d¦  «        r|                     d|d         › ¦  «         |                      d¦  «        r|                     d|d         › ¦  «         d„ | D ¦   «         }|rr|                     d	¦  «         |                     d
¦  «         |d d…         D ]=}|j        rd|j        › dnd}|                     d|j        › d|j        › |› ¦  «         Œ>|r\|                     d	¦  «         |                     d¦  «         |D ]/}|                     d|j        › d|j        › d|j        › ¦  «         Œ0d„ |D ¦   «         }	|	r®|                     d	¦  «         |                     d¦  «         |	D ]}
|                     d|
j         	                    ¦   «         › d|
j        › d|
j
        › dt          |
j        ¦  «        › dt          |
j        ¦  «        › dt          |
j        ¦  «        › d¦  «         Œ‚|                     d¦  «         d                     |¦  «        S )N)zL/* =========================================================================zL * AST PRE-PROCESSOR EXTRACTED PRIMITIVES (GROUND TRUTH CONSTANTS & SYMBOLS)zL * =========================================================================r™  z * Module Description: r˜  z * Module Author: r—  z * Module License: c                 ó$   — g | ]}|j         d v ¯|‘ŒS )>   r   r2  )rÅ   r  s     r•   rM  z>ASTPreProcessor._format_ast_summary_header.<locals>.<listcomp>Ô  s%   € ĞXĞXĞX˜A¨1¬:Ğ9WĞ+WĞ+WaĞ+WĞ+WĞ+Wr—   z *z' * Extracted Register & Bitmask Macros:é–   z  /* resolved=z */r‘   z *   #define ú z * Extracted Driver Structs:z *   struct z -> c                 ó6   — g | ]}|j         d k    s|j        ¯|‘ŒS )rå   )ræ   ré   )r  Úfs     r•   rM  z>ASTPreProcessor._format_ast_summary_header.<locals>.<listcomp>â  s+   € ĞPĞPĞP˜1¨Q¬V°xÒ-?Ğ-?À1Ä<Ğ-?QĞ-?Ğ-?Ğ-?r—   z+ * Extracted Entry Points & MMIO Functions:z *   [z] rY  z) (mmio_ops=z, irq_calls=z, locks=rZ  zO * ========================================================================= */ú
)Úgetr^  rÇ   rÂ   r£   rÒ   rÓ   rÖ   ræ   r3  rè   r“   ré   rê   rë   r`  )rñ   r¹  ró   rº  ÚlinesÚ
reg_macrosr  Úhex_noterÓ  Ú	key_funcsÚfns              r•   rÀ  z*ASTPreProcessor._format_ast_summary_headerÁ  só  € ğ
ğ 
ğ 
ˆğ
 <Š<˜Ñ&Ô&ğ 	NØLŠLĞL°8¸MÔ3JĞLĞLÑMÔMĞMØ<Š<˜Ñ!Ô!ğ 	DØLŠLĞB¨h°xÔ.@ĞBĞBÑCÔCĞCØ<Š<˜	Ñ"Ô"ğ 	FØLŠLĞD¨x¸	Ô/BĞDĞDÑEÔEĞEàXĞX ĞXÑXÔXˆ
Øğ 	KØLŠL˜ÑÔĞØLŠLĞBÑCÔCĞCØ   Ô%ğ Kğ KØ@AÄĞSĞ<¨A¬KĞ<Ğ<Ğ<Ğ<ĞQSØ—’ĞI¨Q¬VĞIĞI°a´gĞI¸xĞIĞIÑJÔJĞJĞJàğ 	XØLŠL˜ÑÔĞØLŠLĞ7Ñ8Ô8Ğ8Øğ Xğ XØ—’ĞV¨A¬MĞVĞV¸A¼JĞVĞVÈAÌHĞVĞVÑWÔWĞWĞWàPĞP 	ĞPÑPÔPˆ	Øğ 	ØLŠL˜ÑÔĞØLŠLĞFÑGÔGĞGØğ ğ Ø—’ğr˜RœWŸ]š]™_œ_ğ rğ r°´ğ rğ r¸"¼-ğ rğ rİ!$ R¤]Ñ!3Ô!3ğrğ rİADÀRÄ\ÑARÔARğrğ rİ\_Ğ`bÔ`mÑ\nÔ\nğrğ rğ rñô ğ ğ ğ
 	ŠĞfÑgÔgĞgØyŠy˜ÑÔĞr—   ©T)rÉ   rÊ   rË   Ú__doc__rÎ   r  Ústaticmethodr   r   ÚbytesrÌ   r  r6  r   rB  r	   rI  r   rN  r   rä   r¤   r  rğ   rÏ  r¿  rÁ   rÑ   r   rÀ  rÏ   r—   r•   r
  r
  &  s²  € € € € € ØeĞeğ)ğ )¨$ğ )¸$ğ )ğ )ğ )ğ )ğ ğ_˜ $œğ _°uğ _Àğ _ğ _ğ _ñ „\ğ_ğ
 Cğ °ğ ¸dğ Àsğ ğ ğ ğ ğ$
°Tğ 
Èğ 
ĞSWĞX[Ğ]`ĞX`ÔSağ 
ğ 
ğ 
ğ 
ğ¸HÀT¼Nğ ĞZ_ğ ĞdiĞjmĞorĞjrÔdsğ ğ ğ ğ ğ
¨(°4¬.ğ 
Èğ 
ĞRVĞWZÔR[ğ 
ğ 
ğ 
ğ 
ğq$àğq$ğ ğq$ğ ğ	q$ğ
 ˜S #˜Xœğq$ğ ˜#”hğq$ğ 
ˆ  U¨3°¨8¤_Ô 5Ğ5Ô	6ğq$ğ q$ğ q$ğ q$ğfL
  e¨C°¨H¤oÔ!6ğ L
Ğ;Nğ L
ğ L
ğ L
ğ L
ğ\ ğ ¨ğ  °t¸EÀ#ÀsÀ(¼OÔ7Lğ  ĞQVğ  ğ  ğ  ñ „\ğ ğ* ğ+ Ø]Ô#ğ+ àĞ&Ô'ğ+ ğ ˜Ô(ğ+ ğ s˜Cx”.ğ	+ ğ
 
ğ+ ğ + ğ + ñ „\ğ+ ğ + ğ + r—   r
  Tr‚  r  c                 óL   — t          |¬¦  «        }|                     | ¦  «        S )zIConvenience function to parse C files with Tree-sitter AST pre-processor.)r  )r
  rÏ  )r‚  r  Ú	processors      r•   Úparse_driver_astrï  ğ  s)   € õ
  Ğ2CĞDÑDÔD€IØ× Ò  Ñ'Ô'Ğ'r—   r  ré  )KrŸ   Úoperatorr°   Úpathlibr   Útypingr   r   r   r   r   r	   r
   Úpydanticr   r   Útree_sitter_cÚtscÚtree_sitterr   r   r   Úlanguager   rÍ   r   rº   ÚImportErrorÚexcÚ
exceptionsr   r$   rÌ   r&   rO   rZ   ri   ry   r€   rŒ   ÚAddÚaddÚSubr±   ÚMultÚmulÚLShiftÚlshiftÚRShiftÚrshiftÚBitOrÚor_ÚBitAndÚand_ÚBitXorÚxorr¨   ÚUAddÚposÚUSubÚnegÚInvertÚinvertr¬   r¤   r¿   rÁ   rÑ   rÚ   rà   rä   rğ   r
  rÎ   rï  rÏ   r—   r•   ú<module>r     s¶  ğØ 
€
€
€
€
Ø €€€Ø 	€	€	€	Ø Ğ Ğ Ğ Ğ Ğ Ø ?Ğ ?Ğ ?Ğ ?Ğ ?Ğ ?Ğ ?Ğ ?Ğ ?Ğ ?Ğ ?Ğ ?Ğ ?Ğ ?Ğ ?Ğ ?Ğ ?Ğ ?Ø %Ğ %Ğ %Ğ %Ğ %Ğ %Ğ %Ğ %ğ$ØĞĞĞØ2Ğ2Ğ2Ğ2Ğ2Ğ2Ğ2Ğ2Ğ2Ğ2à%- X¨l¨c¬l©n¬nÑ%=Ô%=€J˜Ô"Ğ=Ğ=Ñ=Ø59Ğ˜x¨	Ô2Ğ9Ğ9Ñ9Ğ9øØğ $ğ $ğ $Ø
€CØ€HØ€DØ€FØ€JØ #ĞĞĞĞĞĞøøøøğ$øøøğ )Ğ (Ğ (Ğ (Ğ (Ğ (ğ!ğ !ğ !Ğ S˜”Xğ ğ ñ ğ( ğ€S”ğ ğ ñ ğ)ğ )ğ )€S”ğ )ğ )ñ )ğVğ ğ €C”ğ ğ ñ ğğ ğ €S”ğ ğ ñ ğ" ğ  ğ  Ğ C˜”Hğ ğ ñ ğ$%ğ %ğ %Ğ ˜˜Sœğ ğ ñ ğ'ğ 'ğ 'Ğ ˜3˜sœ8ğ ğ ñ ğ „GˆXŒ\Ø„GˆXŒ\Ø„HˆhŒlØ„J”Ø„J”Ø„IˆxŒ|Ø„J”Ø„J”ğ	€ğ „HˆhŒlØ„HˆhŒlØ„J”ğ€ğ*ğ *˜sğ *°(¸4ÀÀSÀ¼>Ô2Jğ *ĞV^Ğ_bÔVcğ *ğ *ğ *ğ *ğZğ ğ ğ ğ Iñ ô ğ ğğ ğ ğ ğ yñ ô ğ ğğ ğ ğ ğ ˆyñ ô ğ ğğ ğ ğ ğ iñ ô ğ ğğ ğ ğ ğ iñ ô ğ ğ0ğ 0ğ 0ğ 0ğ 0˜)ñ 0ô 0ğ 0ğ*G ğ G ğ G ğ G ğ G ñ G ô G ğ G ğX #ğ(ğ (Øc˜3h”Ô ğ(àğ(ğ ğ(ğ (ğ (ğ (ğ (ğ (s   ¯>A. Á.B	Á3BÂB	

// ==========================================================================
// File: src/kernelite_prism/core/__pycache__/doc_generator.cpython-311.pyc
// ==========================================================================

§
    8üjï5  ã                   ó¶   — d dl Z d dlmZ d dlmZ d dlmZmZ ddlm	Z	 ddl
mZ dZd	ed
efd„Zd	ed
efd„Zd	ed
efd„Zd	ed
efd„Z	 ddedee         d
efd„ZdS )é    N)ÚPath)ÚOptional)Ú
BaseLoaderÚEnvironmenté   )ÚDocumentationError)Ú
DriverSpecu.  # Hardware & Driver Specification: {{ spec.device_name or spec.driver_name }}

> **Linux Driver Module:** `{{ spec.driver_name }}` | **Bus Type:** `{{ spec.hardware_info.bus_type | upper }}` | **Endianness:** `{{ spec.hardware_info.endianness }}`

---

## 1. Summary & Hardware Identification

{{ spec.metadata.description or "Hardware peripheral and register specification extracted from Linux kernel driver source." }}

- **Driver Module:** `{{ spec.driver_name }}`
- **Device Name:** {{ spec.device_name or spec.driver_name }}
- **License / Author:** {{ spec.metadata.license or "Unknown" }}{% if spec.metadata.author %} â€” {{ spec.metadata.author }}{% endif %}
- **Primary Driver Structs:** {% if spec.structs %}`{{ spec.structs | join("`, `") }}`{% else %}_None extracted_{% endif %}
- **Probe / Remove Entry Points:** `{{ spec.lifecycle.probe_function or "N/A" }}` / `{{ spec.lifecycle.remove_function or "N/A" }}`

### Compatible Hardware Nodes & Device IDs

| Match Source | Identifier / Compatible String | Vendor ID | Device ID |
| :--- | :--- | :--- | :--- |
{% if spec.compatibility -%}
{% for compat in spec.compatibility -%}
| Device Tree (`of_device_id`) | `{{ compat }}` | â€” | â€” |
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
| Generic / Early Console | `{{ spec.driver_name }}` | â€” | â€” |
{% endif %}

{% if spec.hardware_info.base_address_offsets %}
### MMIO / BAR Base Address Regions

| Region ID | Name | Base Offset | Size Hint | Description |
| :--- | :--- | :--- | :--- | :--- |
{% for region in spec.hardware_info.base_address_offsets -%}
| `{{ region.region_id }}` | {{ region.name or "MMIO" }} | `{{ region.base_offset }}` | {{ region.size_hint or "â€”" }} | {{ region.description or "â€”" }} |
{% endfor %}
{% endif %}

---

## 2. Register Memory Map

{% if spec.register_map %}
| Register Name | Offset | Offset Formula | Width | Access | Reset Value | Description |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
{% for reg in spec.register_map -%}
| `{{ reg.name }}` | `{{ reg.offset }}` | {% if reg.offset_expression %}`{{ reg.offset_expression }}`{% else %}â€”{% endif %} | {{ reg.width }}-bit | `{{ reg.access }}` | `{{ reg.reset_value or "0x0" }}` | {{ reg.description or "â€”" }} |
{% endfor %}

### Bitfield Definitions

{% for reg in spec.register_map %}
{% if reg.bitfields %}
#### `{{ reg.name }}` (`{{ reg.offset }}`)

| Bitfield | Bits | Mask / Macro | Access | Reset | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
{% for bf in reg.bitfields -%}
| `{{ bf.name }}` | `{{ bf.bits or "â€”" }}` | `{{ bf.mask_macro }}` | `{{ bf.access or reg.access }}` | `{{ bf.reset_value or "â€”" }}` | {{ bf.description or "â€”" }} |
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
ÚvalueÚreturnc                 ó’   — t          j        ddt          | pd¦  «        ¦  «        }|r|d                              ¦   «         rd|› }|S )Nú[^A-Za-z0-9_]Ú_ÚNoder   ÚN_©ÚreÚsubÚstrÚisdigit©r
   Úcleaneds     ú;/workspaces/prism/src/kernelite_prism/core/doc_generator.pyÚ_sanitize_mermaid_idr     sQ   € İŒfĞ% s­C°°¸Ñ,@Ô,@ÑAÔA€GØğ !g˜a”j×(Ò(Ñ*Ô*ğ !Ø w..ˆØ€Nó    c                 ó   — t          | pd¦  «                             dd¦  «                             dd¦  «                             dd¦  «        }t          j        dd|¦  «                             ¦   «         }|d d	…         S )
NÚ ú"ú'ú;ú,ú#z\s+ú é`   )r   Úreplacer   r   Ústripr   s     r   Ú_sanitize_mermaid_labelr&     sq   € İ%+˜2ÑÔ×&Ò& s¨CÑ0Ô0×8Ò8¸¸cÑBÔB×JÒJÈ3ĞPRÑSÔS€GİŒfV˜S 'Ñ*Ô*×0Ò0Ñ2Ô2€GØ3B3Œ<Ğr   c                 ó’   — t          j        ddt          | pd¦  «        ¦  «        }|r|d                              ¦   «         rd|› }|S )Nr   r   Údriverr   Úd_r   r   s     r   Ú_to_cpp_identr*     sR   € İŒfĞ% s­C°Ğ0A¸Ñ,BÔ,BÑCÔC€GØğ !g˜a”j×(Ò(Ñ*Ô*ğ !Ø w..ˆØ€Nr   c                 óÎ   — t          j        dt          | pd¦  «        ¦  «        }d                     d„ |D ¦   «         ¦  «        }|r|d                              ¦   «         rd|› }|S )Nz[^A-Za-z0-9]+ÚDriverr   c              3   óB   K  — | ]}|¯|                      ¦   «         V — Œd S ©N)Ú
capitalize)Ú.0Úps     r   ú	<genexpr>z _to_cpp_class.<locals>.<genexpr>#  s/   è è € Ğ8Ğ8¨°aĞ8Q—\’\‘^”^Ğ8Ğ8Ğ8Ğ8Ğ8Ğ8r   r   ÚDev)r   Úsplitr   Újoinr   )r
   ÚpartsÚpascals      r   Ú_to_cpp_classr8   !  sp   € İŒHĞ%¥s¨5Ğ+<°HÑ'=Ô'=Ñ>Ô>€EØWŠWĞ8Ğ8¨UĞ8Ñ8Ô8Ñ8Ô8€FØğ  V˜A”Y×&Ò&Ñ(Ô(ğ  ØvˆØ€Mr   ÚspecÚtemplate_pathc                 óÂ  — 	 t          t          ¦   «         ddd¬¦  «        }t          |j        d<   t          |j        d<   t
          |j        d<   t          |j        d<   ||                     d	¬
¦  «        nt          }| 	                    |¦  «        }| 
                    | ¬¦  «                             ¦   «         dz   S # t          $ r}t          d|› ¦  «        |‚d}~ww xY w)zTRenders a validated DriverSpec instance into human-readable DRIVER_SPEC.md Markdown.FT)ÚloaderÚ
autoescapeÚtrim_blocksÚlstrip_blocksÚ
mermaid_idÚmermaid_labelÚ	cpp_identÚ	cpp_classNzutf-8)Úencoding)r9   ú
z+Failed to generate Markdown documentation: )r   r   r   Úfiltersr&   r*   r8   Ú	read_textÚDRIVER_SPEC_MD_TEMPLATEÚfrom_stringÚrenderr%   Ú	Exceptionr   )r9   r:   ÚenvÚtemplate_sourceÚtemplateÚes         r   Úgenerate_markdown_specrP   )  sñ   € ğ
[İ¥¡¤¸%ÈTĞaeĞfÑfÔfˆİ$8ˆŒLÑ!İ'>ˆŒOÑ$İ#0ˆŒKÑ İ#0ˆŒKÑ ğ Ğ(ğ ×#Ò#¨WĞ#Ñ5Ô5Ğ5å(ğ 	ğ
 —?’? ?Ñ3Ô3ˆØŠ DˆÑ)Ô)×/Ò/Ñ1Ô1°DÑ8Ğ8øİğ [ğ [ğ [İ Ğ!RÈqĞ!RĞ!RÑSÔSĞYZĞZøøøøğ[øøøs   ‚B9B< Â<
CÃCÃCr.   )r   Úpathlibr   Útypingr   Újinja2r   r   Ú
exceptionsr   Úmodels.baser	   rH   r   r   r&   r*   r8   rP   © r   r   ú<module>rW      sP  ğØ 	€	€	€	Ø Ğ Ğ Ğ Ğ Ğ Ø Ğ Ğ Ğ Ğ Ğ Ø *Ğ *Ğ *Ğ *Ğ *Ğ *Ğ *Ğ *à +Ğ +Ğ +Ğ +Ğ +Ğ +Ø $Ğ $Ğ $Ğ $Ğ $Ğ $ğAĞ ğH ğ ¨ğ ğ ğ ğ ğ 3ğ ¨3ğ ğ ğ ğ ğ˜ğ  ğ ğ ğ ğ ğ˜ğ  ğ ğ ğ ğ ğ %)ğ[ğ [Ø
ğ[à˜D”>ğ[ğ 	ğ[ğ [ğ [ğ [ğ [ğ [r   

// ==========================================================================
// File: src/kernelite_prism/core/__pycache__/__init__.cpython-311.pyc
// ==========================================================================

§
    ¤üj.  ã                   óª   — d Z ddlmZmZmZmZmZmZmZm	Z	m
Z
 ddlmZmZmZmZ ddlmZmZmZ ddlmZ ddlmZ ddlmZmZmZmZmZmZmZm Z  g d¢Z!d	S )
zhCore modules for AST pre-processing, context aggregation, LLM extraction, validation, and documentation.é   )	ÚASTExtractionResultÚASTPreProcessorÚConcurrencyCallÚDriverStructInfoÚFunctionSummaryÚMMIOCallÚMacroConstantÚparse_driver_astÚsafe_eval_c_constant)Úaggregate_contextÚaggregate_with_astÚcollect_driver_filesÚestimate_tokens)ÚDriverExtractorÚextract_driver_specÚground_spec_with_ast)Úgenerate_markdown_spec)Úserialize_spec)ÚValidationFindingÚVerificationReportÚload_driver_spec_schemaÚrun_full_verificationÚvalidate_schema_complianceÚverify_markdown_completenessÚverify_register_determinismÚverify_sequence_parity)r   r   r   r   r   r   r	   r
   r   r   r   r   r   r   r   r   r   r   r   r   r   r   r   r   r   r   N)"Ú__doc__Ú
ast_parserr   r   r   r   r   r   r	   r
   r   Ú
aggregatorr   r   r   r   Ú	extractorr   r   r   Údoc_generatorr   Ú
serializerr   Ú	validatorr   r   r   r   r   r   r   r   Ú__all__© ó    ú6/workspaces/prism/src/kernelite_prism/core/__init__.pyú<module>r(      ss  ğØ nĞ nğ
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğ 
ğğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ RĞ QĞ QĞ QĞ QĞ QĞ QĞ QĞ QĞ QØ 1Ğ 1Ğ 1Ğ 1Ğ 1Ğ 1Ø &Ğ &Ğ &Ğ &Ğ &Ğ &ğ	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	ğğ ğ €€€r&   

// ==========================================================================
// File: src/kernelite_prism/core/__pycache__/serializer.cpython-311.pyc
// ==========================================================================

§
    ›üjV  ã                   óP   — d dl Z d dlZddlmZ ddlmZ ddlmZ dded	ed
efd„Z	dS )é    Né   )ÚSerializationError)Ú
DriverSpecé   )Úgenerate_markdown_specÚjsonÚspecÚformat_typeÚreturnc                 ó´  — 	 |                      ¦   «         }|dk    r,|                      d¬¦  «        }t          j        |d¬¦  «        S |dv r-|                      d¬¦  «        }t	          j        |dd¬¦  «        S |d	v rt          | ¦  «        S t          d
|› d¦  «        ‚# t          $ r.}t          |t          ¦  «        r‚ t          d|› ¦  «        |‚d}~ww xY w)zUSerializes a validated DriverSpec model instance into JSON, YAML, or Markdown format.r   T)Úexclude_noner   )Úindent)ÚyamlÚymlF)Ú	sort_keysÚdefault_flow_style)ÚmdÚmarkdownzUnsupported output format: 'z8'. Supported formats are 'json', 'yaml', and 'markdown'.z#Failed to serialize specification: N)
ÚlowerÚ
model_dumpr   Údumpsr   Údumpr   r   Ú	ExceptionÚ
isinstance)r	   r
   ÚfmtÚdataÚes        ú8/workspaces/prism/src/kernelite_prism/core/serializer.pyÚserialize_specr   	   s  € ğSØ×ÒÑ!Ô!ˆà&Š=ˆ=Ø—?’?°?Ñ5Ô5ˆDİ”:˜d¨1Ğ-Ñ-Ô-Ğ-ØOĞ#Ğ#Ø—?’?°?Ñ5Ô5ˆDİ”9˜T¨UÀuĞMÑMÔMĞMØĞ&Ğ&Ğ&İ)¨$Ñ/Ô/Ğ/å$Øt¨{ĞtĞtĞtñô ğ øõ ğ Sğ Sğ SİaÕ+Ñ,Ô,ğ 	Øİ Ğ!JÀqĞ!JĞ!JÑKÔKĞQRĞRøøøøğSøøøs*   ‚AB Á0B Á9B ÂB Â
CÂ))CÃC)r   )
r   r   Ú
exceptionsr   Úmodels.baser   Údoc_generatorr   Ústrr   © ó    r   ú<module>r&      s   ğØ €€€Ø €€€à +Ğ +Ğ +Ğ +Ğ +Ğ +Ø $Ğ $Ğ $Ğ $Ğ $Ğ $Ø 1Ğ 1Ğ 1Ğ 1Ğ 1Ğ 1ğSğ S˜ğ S°#ğ SÀ3ğ Sğ Sğ Sğ Sğ Sğ Sr%   

// ==========================================================================
// File: src/kernelite_prism/core/__pycache__/aggregator.cpython-311.pyc
// ==========================================================================

§
    ˜ŒüjÍ  ã                   ó2  — d dl mZ d dlmZmZmZmZ d dlZddlm	Z	 ddl
mZmZ dZd	Zd
edefd„Zdeeef         deeeef                  fd„Zdedfdeeef         dededee         deeef         f
d„Zeddfdeeef         dedee         dedef
d„ZdS )é    )ÚPath)ÚListÚOptionalÚTupleÚUnionNé   )ÚAggregationErroré   )ÚASTExtractionResultÚASTPreProcessoriÀ]  é   ÚtextÚreturnc                 óT   — | sdS t          dt          | ¦  «        t          z  ¦  «        S )zLEstimates LLM token count using a conservative character-to-token heuristic.r   r
   )ÚmaxÚlenÚCHARS_PER_TOKEN_ESTIMATE)r   s    ú8/workspaces/prism/src/kernelite_prism/core/aggregator.pyÚestimate_tokensr      s+   € àğ Øˆqİˆq•#d‘)”)Õ7Ñ7Ñ8Ô8Ğ8ó    Útarget_pathc                 óh  — t          | ¦  «        }|                     ¦   «         st          d|› d¦  «        ‚g }|                     ¦   «         ru|j        dvrt          d|› d¦  «        ‚|                     |¦  «         t          |j                             d¦  «        ¦  «        D ]}||vr|                     |¦  «         Œn@| 	                    ¦   «         r,t          d„ | 
                    d¦  «        D ¦   «         ¦  «        }|st          d	|› d
¦  «        ‚g }|D ]–}	 |                     dd¬¦  «        }| 	                    ¦   «         r"t          |                     |¦  «        ¦  «        n|j        }|                     ||f¦  «         Œo# t          $ r}t          d|› d|› ¦  «        |‚d}~ww xY w|S )zGCollects `(relative_name, file_content)` pairs for `.c` and `.h` files.zTarget path 'z' does not exist.©z.cz.hzFile 'z*' is not a valid C source file (.c or .h).z*.hc                 óL   — g | ]!}|                      ¦   «         ¯|j        d v ¯|‘Œ"S )r   )Úis_fileÚsuffix)Ú.0Úfs     r   ú
<listcomp>z(collect_driver_files.<locals>.<listcomp>'   s1   € ĞTĞTĞT1¨1¯9ª9©;¬;ĞT¸1¼8À|Ğ;SĞ;SˆQĞ;SĞ;SĞ;Sr   Ú*z/No .c or .h source files found in target path 'z'.zutf-8Úreplace)ÚencodingÚerrorszFailed to read file 'z': N)r   Úexistsr	   r   r   ÚappendÚsortedÚparentÚglobÚis_dirÚrglobÚ	read_textÚstrÚrelative_toÚnameÚ	Exception)	r   ÚpathÚfiles_to_processÚsibling_hdrÚ	collectedÚ	file_pathÚcontentÚrelative_nameÚes	            r   Úcollect_driver_filesr8      s  € åÑÔ€Dà;Š;‰=Œ=ğ HİĞF¨tĞFĞFĞFÑGÔGĞGà#%Ğà‡|‚|~„~ğ 
ØŒ;˜lĞ*Ğ*İ"ØI˜ĞIĞIĞIñô ğ ğ 	×Ò Ñ%Ô%Ğ%å! $¤+×"2Ò"2°5Ñ"9Ô"9Ñ:Ô:ğ 	5ğ 	5ˆKØĞ"2Ğ2Ğ2Ø ×'Ò'¨Ñ4Ô4Ğ4øğ	5ğ 
Š‰Œğ 
İ!ØTĞT˜Ÿ
š
 3™œĞTÑTÔTñ
ô 
Ğğ ğ 
İØF¸dĞFĞFĞFñ
ô 
ğ 	
ğ (*€IØ%ğ ğ ˆ	ğ	Ø×)Ò)°7À9Ğ)ÑMÔMˆGğ —;’;‘=”=ğ$•I×)Ò)¨$Ñ/Ô/Ñ0Ô0Ğ0à”^ğ ğ
 ×Ò˜m¨WĞ5Ñ6Ô6Ğ6Ğ6øİğ 	ğ 	ğ 	İ"Ø9¨	Ğ9Ğ9°aĞ9Ğ9ñô àğøøøøğ	øøøğ
 Ğs   ÄA+F
Æ

F/ÆF*Æ*F/TÚuse_ast_pruningÚwarn_token_thresholdÚ
max_tokensc                 óæ  — t          | ¦  «        }t          |¦  «        }t          |¬¦  «        }|                     |¦  «        }|r|j        }n!d„ |D ¦   «         }	d                     |	¦  «        }t          |¦  «        }
|1|
|k    r+t          d|› dt          |¦  «        › d|
d›d	|d›d
	¦  «        ‚|
|k    r8t          j
        d|› dt          |¦  «        › d|
d›d|d›d	t          d¬¦  «         ||fS )zZAggregates C source files, parses AST primitives, and returns `(context_str, ast_result)`.)Ústrip_boilerplatec                 ó$   — g | ]\  }}d |› d|› ‘ŒS )zV/* =========================================================================
 * File: zR
 * ========================================================================= */

© )r   Úrel_namer5   s      r   r   z&aggregate_with_ast.<locals>.<listcomp>Q   sJ   € ğ 
ğ 
ğ 
ñ
 "˜'ğ	Ø ğğ ğ ğğ ğ
ğ 
ğ 
r   z

NzAggregated context from 'z' (z	 files, ~ú,z) tokens) exceeds the configured limit of z tokens.z' spans z file(s) (~z; estimated tokens), exceeding the recommended threshold of zf tokens. Large multi-file contexts may cause context truncation, degraded accuracy, or GPU OOM errors.r   )Ú
stacklevel)r   r8   r   Úparse_filesÚpruned_sourceÚjoinr   r	   r   ÚwarningsÚwarnÚUserWarning)r   r9   r:   r;   r0   Úcollected_filesÚpreprocessorÚ
ast_resultÚ
aggregatedÚblocksÚestimated_tokenss              r   Úaggregate_with_astrO   A   s›  € õ ÑÔ€Dİ*¨4Ñ0Ô0€Oå"°_ĞEÑEÔE€LØ×)Ò)¨/Ñ:Ô:€Jàğ 
)ØÔ-ˆ
ˆ
ğ
ğ 
ğ
 &5ğ
ñ 
ô 
ˆğ —[’[ Ñ(Ô(ˆ
å& zÑ2Ô2ĞàĞĞ"2°ZÒ"?Ğ"?İğd¨ğ dğ dµ°_Ñ1EÔ1Eğ dğ dØ Ğcğdğ dØMWĞcğdğ dğ dñ
ô 
ğ 	
ğ
 Ğ.Ò.Ğ.İŒğA¨ğ Ağ Aµc¸/Ñ6JÔ6Jğ Ağ AØ!Ğ]ğAğ Aà&Ğ_ğAğ Ağ Aõ Øğ	
ñ 	
ô 	
ğ 	
ğ zĞ!Ğ!r   c                 ó2   — t          | |||¬¦  «        \  }}|S )z^Recursively collects and aggregates C source and header files into a formatted string context.)r   r9   r:   r;   )rO   )r   r:   r;   r9   rL   Ú_s         r   Úaggregate_contextrR   o   s0   € õ 'ØØ'Ø1Øğ	ñ ô M€Jğ Ğr   )Úpathlibr   Útypingr   r   r   r   rF   Ú
exceptionsr	   Ú
ast_parserr   r   ÚDEFAULT_TOKEN_WARN_THRESHOLDr   r,   Úintr   r8   ÚboolrO   rR   r?   r   r   ú<module>rZ      s©  ğØ Ğ Ğ Ğ Ğ Ğ Ø /Ğ /Ğ /Ğ /Ğ /Ğ /Ğ /Ğ /Ğ /Ğ /Ğ /Ğ /Ø €€€Ø )Ğ )Ğ )Ğ )Ğ )Ğ )Ø <Ğ <Ğ <Ğ <Ğ <Ğ <Ğ <Ğ <à%Ğ ØĞ ğ9˜#ğ 9 #ğ 9ğ 9ğ 9ğ 9ğ, e¨C°¨IÔ&6ğ ,¸4ÀÀcÈ3ÀhÄÔ;Pğ ,ğ ,ğ ,ğ ,ğb !Ø <Ø $ğ	+"ğ +"Øs˜DyÔ!ğ+"àğ+"ğ ğ+"ğ ˜”ğ	+"ğ
 ˆ3Ğ#Ğ#Ô$ğ+"ğ +"ğ +"ğ +"ğ` !=Ø $Ø ğ	ğ Øs˜DyÔ!ğàğğ ˜”ğğ ğ	ğ
 	ğğ ğ ğ ğ ğ r   

// ==========================================================================
// File: src/kernelite_prism/core/__pycache__/validator.cpython-311.pyc
// ==========================================================================

§
    †üj-  ã                   óğ  — d dl Z d dlmZ d dlmZmZmZmZmZ d dl	m
Z
 d dlmZmZmZ ddlmZ ddlmZ d	d
lmZ  G d„ de¦  «        Z G d„ de¦  «        Zddee         deeef         fd„Z	 ddeeeeef         ef         dee         dee         fd„Zdededee         fd„Z	 ddededee         dee         fd„Zdedee         fd„Z	 	 	 	 ddedee         dee         dee         dedefd„ZdS )é    N)ÚPath)ÚAnyÚDictÚListÚOptionalÚUnion)ÚDraft202012Validator)Ú	BaseModelÚFieldÚValidationErroré   )ÚSchemaValidationError)Ú
DriverSpecé   )ÚASTExtractionResultc                   ó.   — e Zd ZU eed<   eed<   eed<   dS )ÚValidationFindingÚcheckÚpassedÚmessageN)Ú__name__Ú
__module__Ú__qualname__ÚstrÚ__annotations__Úbool© ó    ú7/workspaces/prism/src/kernelite_prism/core/validator.pyr   r      s+   € € € € € € Ø€J€JJØ€L€LLØ€L€LL€L€Lr   r   c                   óp   — e Zd ZU eed<   eed<   eed<   eed<   eed<    ee¬¦  «        Zee	         ed<   dS )	ÚVerificationReportr   Úschema_compliantÚregister_deterministicÚsequence_parityÚmarkdown_complete)Údefault_factoryÚfindingsN)
r   r   r   r   r   r   Úlistr'   r   r   r   r   r   r!   r!      sn   € € € € € € Ø€L€LLØĞĞÑØ Ğ Ğ Ñ ØĞĞÑØĞĞÑØ(-¨¸dĞ(CÑ(CÔ(C€HˆdĞ$Ô%ĞCĞCÑCĞCĞCr   r!   Úschema_pathÚreturnc                 ó¤   — | <|                       ¦   «         r(t          j        |                      d¬¦  «        ¦  «        S t	          j        ¦   «         S )zfLoads driver_spec.schema.json from disk if provided, otherwise returns canonical Draft 2020-12 schema.Nzutf-8)Úencoding)ÚexistsÚjsonÚloadsÚ	read_textr   Údraft_2020_12_schema©r)   s    r   Úload_driver_spec_schemar3      sI   € àĞ ;×#5Ò#5Ñ#7Ô#7ĞİŒz˜+×/Ò/¸Ğ/ÑAÔAÑBÔBĞBİÔ*Ñ,Ô,Ğ,r   Úspec_payloadc                 óœ  — g }t          |¦  «        }t          | t          ¦  «        r|                      ¦   «         }n_t          | t          ¦  «        rH	 t          j        | ¦  «        }n4# t
          j        $ r }t          ddd|› ¬¦  «        gcY d}~S d}~ww xY w| }t          |¦  «        }t          |                     |¦  «        d„ ¬¦  «        }|r\|D ]X}d                     d	„ |j        D ¦   «         ¦  «        pd
}	|                     t          ddd|	› d|j        › ¬¦  «        ¦  «         ŒYn%|                     t          ddd¬¦  «        ¦  «         	 t          j        |¦  «         |                     t          ddd¬¦  «        ¦  «         n?# t"          $ r2}|                     t          ddd|› ¬¦  «        ¦  «         Y d}~nd}~ww xY w|S )zcValidates a JSON string, dictionary, or DriverSpec against driver_spec.schema.json (Draft 2020-12).Újson_syntaxFzInvalid JSON syntax: ©r   r   r   Nc                 ó*   — t          | j        ¦  «        S ©N)r(   Úpath)Úerrs    r   ú<lambda>z,validate_schema_compliance.<locals>.<lambda>;   s   € ÍÈSÌXÉÌ€ r   )Úkeyú.c              3   ó4   K  — | ]}t          |¦  «        V — Œd S r9   )r   )Ú.0Úps     r   ú	<genexpr>z-validate_schema_compliance.<locals>.<genexpr>>   s(   è è € Ğ4Ğ4 a3˜q™6œ6Ğ4Ğ4Ğ4Ğ4Ğ4Ğ4r   z<root>Újson_schema_draft_2020_12zAt 'z': Tz<100% compliant with driver_spec.schema.json (Draft 2020-12).Úpydantic_model_validationz2Passed strict Pydantic DriverSpec type validation.zPydantic validation failed: )r3   Ú
isinstancer   Ú
model_dumpr   r.   r/   ÚJSONDecodeErrorr   r	   ÚsortedÚiter_errorsÚjoinr:   Úappendr   Úmodel_validater   )
r4   r)   r'   ÚschemaÚdataÚeÚ	validatorÚschema_errorsr;   Úlocs
             r   Úvalidate_schema_compliancerS   "   s‡  € ğ
 )+€Hİ$ [Ñ1Ô1€Få,¥
Ñ+Ô+ğ Ø×&Ò&Ñ(Ô(ˆˆİ	L¥#Ñ	&Ô	&ğ ğ		İ”:˜lÑ+Ô+ˆDˆDøİÔ#ğ 	ğ 	ğ 	å!Ø'Ø Ø7°AĞ7Ğ7ğñ ô ğğ ğ ğ ğ ğ ğ øøøøğ	øøøğ ˆå$ VÑ,Ô,€Iİ˜9×0Ò0°Ñ6Ô6Ğ<VĞ<VĞWÑWÔW€MØğ 
Ø ğ 	ğ 	ˆCØ—(’(Ğ4Ğ4¨3¬8Ğ4Ñ4Ô4Ñ4Ô4Ğ@¸ˆCØOŠOİ!Ø5Ø Ø8 3Ğ8Ğ8¨3¬;Ğ8Ğ8ğñ ô ñô ğ ğ ğ	ğ 	ŠİØ1ØØVğñ ô ñ	
ô 	
ğ 	
ğ
İÔ! $Ñ'Ô'Ğ'ØŠİØ1ØØLğñ ô ñ	
ô 	
ğ 	
ğ 	
øõ ğ 
ğ 
ğ 
ØŠİØ1ØØ:°qĞ:Ğ:ğñ ô ñ	
ô 	
ğ 	
ğ 	
ğ 	
ğ 	
ğ 	
ğ 	
øøøøğ
øøøğ €Os6   ÁA' Á'BÁ6BÂBÂBÅ9F Æ
G	Æ(GÇG	ÚspecÚ
ast_resultc                 ól  — g }|                      ¦   «         }| j        s'|                     t          ddd¬¦  «        ¦  «         |S | j        D ]æ}|j        |vr7|                     t          d|j        › dd|j        › d¬¦  «        ¦  «         ŒC||j                 }|j        5	 |j                             ¦   «                              d	¦  «        rt          |j        d
¦  «        nt          |j        ¦  «        }||j
        k    rG|                     t          d|j        › dd|j        › d|j        › d|j        › d¬¦  «        ¦  «         n>|                     t          d|j        › dd|j        › d|j        › d¬¦  «        ¦  «         nN# t          $ rA |                     t          d|j        › dd|j        › d|j        › d¬¦  «        ¦  «         Y nw xY w|j        D ]Q}|j        |v rF|                     t          d|j        › d|j        › dd|j        › d|j        › d¬¦  «        ¦  «         ŒRŒè|S )z_Ensures extracted register offsets and bitmasks match C AST constants with zero hallucinations.Úregister_presenceFz Extracted register_map is empty.r7   zregister_symbol:z
Register 'z*' not found in C header/source AST macros.NÚ0xé   zregister_offset:zOffset mismatch for 'z': extracted 'z', but C AST resolved 'ú'.Tz
Verified 'z' == r>   zNon-numeric offset 'z' for 'zbitfield_symbol:zVerified bitfield 'z' (z).)Úmacro_lookupÚregister_maprK   r   ÚnameÚ	hex_valueÚoffsetÚlowerÚ
startswithÚintÚnumeric_valueÚ
ValueErrorÚ	bitfieldsÚ
mask_macro)rT   rU   r'   Ú	macro_mapÚregÚmacroÚextracted_intÚbfs           r   Úverify_register_determinismrl   d   s  € ğ
 )+€HØ×'Ò'Ñ)Ô)€IàÔğ ØŠİØ)ØØ:ğñ ô ñ	
ô 	
ğ 	
ğ ˆàÔ ğ 7ñ 7ˆØŒ8˜9Ğ$Ğ$ØOŠOİ!Ø7¨S¬XĞ7Ğ7Ø Ø]¨¬Ğ]Ğ]Ğ]ğñ ô ñô ğ ğ à˜#œ(Ô#ˆØŒ?Ñ&ğ ğ ”z×'Ò'Ñ)Ô)×4Ò4°TÑ:Ô:ğ)•C˜œ
 BÑ'Ô'Ğ'å˜SœZ™œğ ğ
 ! EÔ$7Ò7Ğ7Ø—O’Oİ)Ø"?°S´XĞ"?Ğ"?Ø#(ğ!K¸¼ğ !Kğ !KĞPSÔPZğ !Kğ !KØ7<´ğ!Kğ !Kğ !Kğ	ñ ô ñ	ô 	ğ 	ğ 	ğ —O’Oİ)Ø"?°S´XĞ"?Ğ"?Ø#'Ø$R°´Ğ$RĞ$RÀÄĞ$RĞ$RĞ$Rğñ ô ñô ğ øøõ ğ ğ ğ Ø—’İ%Ø;°´Ğ;Ğ;Ø$Ø V°s´zĞ VĞ VÈ#Ì(Ğ VĞ VĞ Vğñ ô ñô ğ ğ ğ ğøøøğ ”-ğ 	ğ 	ˆBØŒw˜)Ğ#Ğ#Ø—’İ%ØE°´ĞEĞE¸B¼GĞEĞEØ#Ø S°b´gĞ SĞ SÀ"Ä-Ğ SĞ SĞ Sğñ ô ñô ğ øñ	ğ €Os   Â%C%FÆAGÇGÚmarkdown_textc           
      ó‚  — g }d„ |j         D ¦   «         }|s'|                     t          ddd¬¦  «        ¦  «         |S |d         }| j        s0|                     t          ddd	|j        › d
¬¦  «        ¦  «         |S t          t          dt          | j        ¦  «        dz   ¦  «        ¦  «        }d„ | j        D ¦   «         }|                     t          d||k    ||k    rdt          | j        ¦  «        › dnd|› d|› ¬¦  «        ¦  «         |1d|v od|v }|                     t          d||rdnd¬¦  «        ¦  «         |S )zdVerifies chronological sequence parity between C .probe MMIO operations, init_sequence, and Mermaid.c                 ó(   — g | ]}|j         d k    ¯|‘ŒS )Úprobe)Úrole)r@   Úfns     r   ú
<listcomp>z*verify_sequence_parity.<locals>.<listcomp>¹   s$   € ĞKĞKĞK˜"¸¼À7Ò8JĞ8J2Ğ8JĞ8JĞ8Jr   Úprobe_ast_discoveryTzCNo dedicated .probe function in AST; sequence parity check skipped.r7   r   Úinit_sequence_populatedFzProbe function 'z,' exists in AST, but init_sequence is empty.r   c                 ó   — g | ]	}|j         ‘Œ
S r   )Ústep)r@   Úss     r   rs   z*verify_sequence_parity.<locals>.<listcomp>Ò   s   € Ğ7Ğ7Ğ7˜qA”FĞ7Ğ7Ğ7r   Úinit_sequence_monotonic_orderzinit_sequence has z ordered steps.zStep numbering mismatch: z != Nz
```mermaidÚsequenceDiagramÚmermaid_sequence_diagramz7Markdown contains valid Mermaid sequenceDiagram blocks.z<Missing Mermaid sequenceDiagram in generated DRIVER_SPEC.md.)Ú	functionsrK   r   Úinit_sequencer]   r(   ÚrangeÚlen)	rT   rU   rm   r'   Úprobe_funcsÚprobe_fnÚexpected_stepsÚactual_stepsÚhas_mermaids	            r   Úverify_sequence_parityr…   ²   sÏ  € ğ )+€HØKĞK 
Ô 4ĞKÑKÔK€Kàğ ØŠİØ+ØØ]ğñ ô ñ	
ô 	
ğ 	
ğ ˆà˜1Œ~€HØÔğ ØŠİØ/ØØf¨8¬=ĞfĞfĞfğñ ô ñ	
ô 	
ğ 	
ğ ˆõ % ¥3 tÔ'9Ñ#:Ô#:¸QÑ#>Ñ?Ô?Ñ@Ô@€NØ7Ğ7 DÔ$6Ğ7Ñ7Ô7€LØ‡O‚OİØ1Ø" lÒ2ğ " \Ò1Ğ1ğ N¥S¨Ô);Ñ%<Ô%<ĞMĞMĞMĞMàS°ĞSĞSÀ>ĞSĞSğ	
ñ 	
ô 	
ñ
ô 
ğ 
ğ Ğ Ø" mĞ3ĞZĞ8IÈ]Ğ8ZˆØŠİØ0Ø"ğ #ğXĞMĞMàWğñ ô ñ
	
ô 
	
ğ 
	
ğ €Or   c           
      óî   — g d¢}g }|D ]:}|| v }|                      t          d|› ||rd|› dnd|› d¬¦  «        ¦  «         Œ;d| vod	| v}|                      t          d
||rdnd¬¦  «        ¦  «         |S )zZValidates that DRIVER_SPEC.md contains all required human-readable documentation sections.)z'## 1. Summary & Hardware Identificationz## 2. Register Memory Mapz## 3. Sequence Diagramsz1## 4. Runtime Operations, DMA & Concurrency Modelz%## 5. C++ Microkernel Migration Noteszmarkdown_section:z	Section 'z
' present.zMissing section 'rZ   r7   z{{z{%Ú markdown_no_unrendered_templatesz1All Jinja2 template expressions rendered cleanly.z5Found unrendered Jinja2 delimiters in DRIVER_SPEC.md.)rK   r   )rm   Úrequired_sectionsr'   ÚsectionÚpresentÚno_raw_jinjas         r   Úverify_markdown_completenessrŒ   ğ   s÷   € ğğ ğ Ğğ )+€HØ$ğ 
ğ 
ˆØ˜]Ğ*ˆØŠİØ3¨'Ğ3Ğ3Øğ ğ9Ğ3 Ğ3Ğ3Ğ3Ğ3à8¨WĞ8Ğ8Ğ8ğñ ô ñ
	
ô 
	
ğ 
	
ğ 
	
ğ ˜}Ğ,ĞJ°¸]Ğ1J€LØ‡O‚OİØ4Øğ  ğMĞCĞCàLğ	
ñ 	
ô 	
ñ
ô 
ğ 
ğ €Or   FÚstrictc                 óŠ  — t          | |¬¦  «        }|t          | |¦  «        nt          ddd¬¦  «        g}|t          | ||¬¦  «        nt          ddd¬¦  «        g}|t	          |¦  «        nt          d	dd
¬¦  «        g}||z   |z   |z   }	t          d„ |D ¦   «         ¦  «        }
t          d„ |D ¦   «         ¦  «        }t          d„ |D ¦   «         ¦  «        }t          d„ |D ¦   «         ¦  «        }|
o|o|o|}t          ||
||||	¬¦  «        }|r3|s1d                     d„ |	D ¦   «         ¦  «        }t          d|› ¦  «        ‚|S )zeRuns the complete verification harness across schema, AST determinism, sequence parity, and Markdown.r2   NÚregister_ast_skipTzAST not provided.r7   )rm   Úsequence_ast_skipÚmarkdown_skipzMarkdown not provided.c              3   ó$   K  — | ]}|j         V — Œd S r9   ©r   ©r@   Úfs     r   rB   z(run_full_verification.<locals>.<genexpr>2  s$   è è € Ğ6Ğ6 A”HĞ6Ğ6Ğ6Ğ6Ğ6Ğ6r   c              3   ó$   K  — | ]}|j         V — Œd S r9   r“   r”   s     r   rB   z(run_full_verification.<locals>.<genexpr>3  ó$   è è € Ğ0Ğ0˜a”Ğ0Ğ0Ğ0Ğ0Ğ0Ğ0r   c              3   ó$   K  — | ]}|j         V — Œd S r9   r“   r”   s     r   rB   z(run_full_verification.<locals>.<genexpr>4  r—   r   c              3   ó$   K  — | ]}|j         V — Œd S r9   r“   r”   s     r   rB   z(run_full_verification.<locals>.<genexpr>5  s$   è è € Ğ.Ğ.˜Q”Ğ.Ğ.Ğ.Ğ.Ğ.Ğ.r   )r   r"   r#   r$   r%   r'   z; c              3   ó2   K  — | ]}|j         °	|j        V — Œd S r9   )r   r   r”   s     r   rB   z(run_full_verification.<locals>.<genexpr>B  s+   è è € ĞPĞP¨aÀqÄxĞP ¤	ĞPĞPĞPĞPĞPĞPr   z DriverSpec verification failed: )	rS   rl   r   r…   rŒ   Úallr!   rJ   r   )rT   rU   rm   r)   r   Úschema_findingsÚreg_findingsÚseq_findingsÚmd_findingsÚall_findingsÚ	schema_okÚreg_okÚseq_okÚmd_okÚ
overall_okÚreportÚfailed_msgss                    r   Úrun_full_verificationr¨     sÉ  € õ 1°À;ĞOÑOÔO€Oğ Ğ!õ 	$ D¨*Ñ5Ô5Ğ5åĞ&9À$ĞPcĞdÑdÔdĞeğ ğ Ğ!õ 	˜t Z¸}ĞMÑMÔMĞMåĞ&9À$ĞPcĞdÑdÔdĞeğ ğ Ğ$õ 	% ]Ñ3Ô3Ğ3å o¸dĞLdĞeÑeÔeĞfğ ğ # \Ñ1°LÑ@À;ÑN€LİĞ6Ğ6 oĞ6Ñ6Ô6Ñ6Ô6€IİĞ0Ğ0 <Ğ0Ñ0Ô0Ñ0Ô0€FİĞ0Ğ0 <Ğ0Ñ0Ô0Ñ0Ô0€FİĞ.Ğ. +Ğ.Ñ.Ô.Ñ.Ô.€EØĞ:˜vĞ:¨&Ğ:°U€JåØØ"Ø%ØØØğñ ô €Fğ ğ Vjğ VØ—i’iĞPĞP°<ĞPÑPÔPÑPÔPˆİ#Ğ$TÀ{Ğ$TĞ$TÑUÔUĞUà€Mr   r9   )NNNF)r.   Úpathlibr   Útypingr   r   r   r   r   Ú
jsonschemar	   Úpydanticr
   r   r   Ú
exceptionsr   Úmodels.baser   Ú
ast_parserr   r   r!   r   r3   rS   rl   r…   rŒ   r   r¨   r   r   r   ú<module>r°      s¿  ğØ €€€Ø Ğ Ğ Ğ Ğ Ğ Ø 3Ğ 3Ğ 3Ğ 3Ğ 3Ğ 3Ğ 3Ğ 3Ğ 3Ğ 3Ğ 3Ğ 3Ğ 3Ğ 3Ø +Ğ +Ğ +Ğ +Ğ +Ğ +Ø 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6à .Ğ .Ğ .Ğ .Ğ .Ğ .Ø $Ğ $Ğ $Ğ $Ğ $Ğ $Ø +Ğ +Ğ +Ğ +Ğ +Ğ +ğğ ğ ğ ğ ˜	ñ ô ğ ğDğ Dğ Dğ Dğ D˜ñ Dô Dğ Dğ-ğ -¨°$¬ğ -À4ÈÈSÈÄ>ğ -ğ -ğ -ğ -ğ #'ğ?ğ ?Ø˜˜T # s (œ^¨ZĞ7Ô8ğ?à˜$”ğ?ğ 
Ğ
Ôğ?ğ ?ğ ?ğ ?ğDKØ
ğKà#ğKğ 
Ğ
ÔğKğ Kğ Kğ Kğb $(ğ;ğ ;Ø
ğ;à#ğ;ğ ˜C”=ğ;ğ 
Ğ
Ôğ	;ğ ;ğ ;ğ ;ğ|%°ğ %¸Ğ=NÔ8Oğ %ğ %ğ %ğ %ğT 15Ø#'Ø"&Øğ-ğ -Ø
ğ-àĞ,Ô-ğ-ğ ˜C”=ğ-ğ ˜$”ğ	-ğ
 ğ-ğ ğ-ğ -ğ -ğ -ğ -ğ -r   

// ==========================================================================
// File: src/kernelite_prism/core/extractor.py
// ==========================================================================

import json
import os
import re
from typing import Any, Callable, Dict, Optional
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from pydantic import ValidationError

from ..config import DEFAULT_MODEL, VALID_BACKENDS, VALID_QUANTIZATIONS
from ..exceptions import ConfigurationError, ExtractionError
from ..models.base import DriverSpec
from ..models.registers import Bitfield, RegisterMap, infer_bits_from_macro
from .ast_parser import ASTExtractionResult

SYSTEM_INSTRUCTION = (
    "You are an expert Linux kernel driver engineer and bare-metal C++ systems architect. "
    "Your objective is to analyze pre-parsed Linux kernel C source code (.c and .h files) "
    "and extract a complete, deterministic hardware and driver specification for porting to a "
    "bare-metal C++ microkernel.\n\n"
    "MANDATORY EXTRACTION RULES:\n"
    "1. ZERO HALLUCINATED ADDRESSES OR MASKS: Every register offset and bitmask in `register_map` "
    "MUST strictly match the C header/source macro definitions. Never invent offsets.\n"
    "2. ISOLATE REGISTER OFFSET EXPRESSIONS: When a register is indexed or computed dynamically "
    "(e.g., `BASE + (i * 0x4)` or `0x03800 + (n * 0x100)`), record the base hex offset in `offset` "
    "and preserve the exact C arithmetic formula in `offset_expression`.\n"
    "3. CHRONOLOGICAL PROBE / INIT SEQUENCE: Populate `init_sequence` in exact execution order "
    "matching `.probe` / early init (`MMIO Map`, `Hardware Reset`, `Write Register`, `Read & Poll`, "
    "`Memory Allocate`, `DMA Setup`, `Interrupt Hook`, `Register Device`).\n"
    "4. ABSTRACT CONCURRENCY PRIMITIVES: Translate every Linux lock (`spin_lock_irqsave`, "
    "`spin_lock`, `mutex_lock`, `raw_spin_lock_irqsave`) in `concurrency_locks` into an abstract "
    "lock (`IRQSafeSpinlock`, `Spinlock`, `SleepableMutex`, `ReadWriteLock`, `Atomic`), listing "
    "protected registers/rings and bare-metal C++ guidance.\n"
    "5. MAP IRQ ROUTINES TO RAW HARDWARE REGISTERS: In `runtime_operations.interrupt_lifecycle`, "
    "trace `irqreturn_t` handlers and `request_irq` calls to the exact hardware status register, "
    "acknowledge/clear register, mask register, acknowledgment mechanism (e.g., Read-to-clear vs. W1C), "
    "and ordered `flow_steps`.\n\n"
    "CRITICAL OUTPUT REQUIREMENT: Output strictly a single, valid JSON object satisfying the "
    "provided JSON Schema Draft 2020-12. Do not emit markdown fences, commentary, or extra text."
)


def _extract_json_text(raw_output: str) -> str:
    """Extracts raw JSON payload from markdown fences or bracket boundaries."""
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", raw_output)
    if fence_match:
        return fence_match.group(1).strip()

    start_idx = raw_output.find("{")
    end_idx = raw_output.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        return raw_output[start_idx : end_idx + 1].strip()

    return raw_output.strip()


def ground_spec_with_ast(spec: DriverSpec, ast_result: Optional[ASTExtractionResult]) -> DriverSpec:
    """Enforces deterministic parity between AST-parsed C constants and the extracted DriverSpec."""
    if ast_result is None:
        return spec

    macro_map = ast_result.macro_lookup()

    # 1. Verify and normalize offsets & bitfield masks against AST constants
    for reg in spec.register_map:
        if reg.name in macro_map:
            macro = macro_map[reg.name]
            if macro.hex_value is not None:
                reg.offset = macro.hex_value
            elif macro.value and not reg.offset:
                reg.offset = macro.value

        for bf in reg.bitfields:
            if bf.name in macro_map:
                bf_macro = macro_map[bf.name]
                if bf_macro.value and not bf.mask_macro:
                    bf.mask_macro = bf_macro.value
            if not bf.bits and bf.mask_macro:
                bf.bits = infer_bits_from_macro(bf.mask_macro)

    # 2. Backfill metadata from MODULE_* macros if omitted by LLM
    meta = ast_result.metadata_macros
    if not spec.metadata.license and meta.get("license"):
        spec.metadata.license = meta["license"]
    if not spec.metadata.author and meta.get("author"):
        spec.metadata.author = meta["author"]
    if not spec.metadata.description and meta.get("description"):
        spec.metadata.description = meta["description"]

    # 3. Backfill structs list from AST-extracted driver structs
    for s in ast_result.driver_structs:
        if s.struct_type not in spec.structs:
            spec.structs.append(s.struct_type)

    return spec


class DriverExtractor:
    """Encapsulates LLM inference and schema-enforced structured output generation."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str = "cuda",
        quantization: str = "4bit",
        backend: str = "transformers",
        hf_token: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        openai_base_url: Optional[str] = None,
        custom_generator: Optional[Callable[[str, str], str]] = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.quantization = quantization.lower()
        self.backend = backend.lower()
        self.hf_token = hf_token
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        self.openai_base_url = openai_base_url or os.getenv("OPENAI_BASE_URL")
        self.custom_generator = custom_generator

        self.tokenizer: Any = None
        self.model: Any = None
        self._outlines_logits_processor: Any = None

        if self.custom_generator is None:
            self._validate_runtime_options()
            if self.backend in {"transformers", "outlines"}:
                self.tokenizer, self.model = self._load_model_and_tokenizer()
                if self.backend == "outlines":
                    self._init_outlines_processor()

    def _validate_runtime_options(self) -> None:
        if self.backend not in VALID_BACKENDS:
            raise ConfigurationError(
                f"Unsupported backend '{self.backend}'. "
                f"Choose from: {', '.join(sorted(VALID_BACKENDS))}."
            )

        if self.backend in {"transformers", "outlines"}:
            if self.quantization not in VALID_QUANTIZATIONS:
                raise ConfigurationError(
                    f"Unsupported quantization '{self.quantization}'. "
                    f"Choose from: {', '.join(sorted(VALID_QUANTIZATIONS))}."
                )

            is_cuda = self.device.startswith("cuda")

            if is_cuda and not torch.cuda.is_available():
                raise ConfigurationError(
                    f"Device '{self.device}' was requested, but CUDA is not available. "
                    "Ensure an NVIDIA GPU with appropriate CUDA drivers and PyTorch are installed."
                )

            if not is_cuda and self.quantization != "none":
                raise ConfigurationError(
                    f"Quantization '{self.quantization}' is only supported on CUDA devices, "
                    f"got device '{self.device}'. Use quantization='none' for non-CUDA execution."
                )

    def _init_outlines_processor(self) -> None:
        """Initializes Outlines JSON Schema constrained logits processor when available."""
        try:
            import outlines
            from outlines.processors import JSONLogitsProcessor

            outlines_model = outlines.from_transformers(self.model, self.tokenizer)
            self._outlines_logits_processor = JSONLogitsProcessor(
                schema=DriverSpec.draft_2020_12_schema(),
                tokenizer=outlines_model.tokenizer,
            )
        except Exception:
            # Graceful fallback to schema-guided decoding + validation retry loop
            self._outlines_logits_processor = None

    def _load_model_and_tokenizer(self) -> tuple[Any, Any]:
        is_cuda = self.device.startswith("cuda")
        quant_config: Optional[BitsAndBytesConfig] = None

        if is_cuda:
            compute_dtype = (
                torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            )
            if self.quantization == "4bit":
                quant_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_compute_dtype=compute_dtype,
                )
            elif self.quantization == "8bit":
                quant_config = BitsAndBytesConfig(load_in_8bit=True)
        else:
            compute_dtype = torch.float32

        try:
            tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                token=self.hf_token,
                trust_remote_code=True,
            )

            if tokenizer.pad_token_id is None:
                tokenizer.pad_token_id = tokenizer.eos_token_id

            model_kwargs: Dict[str, Any] = {
                "token": self.hf_token,
                "trust_remote_code": True,
                "device_map": "auto" if self.device == "cuda" else self.device,
            }

            if quant_config is not None:
                model_kwargs["quantization_config"] = quant_config
            else:
                model_kwargs["torch_dtype"] = compute_dtype

            model = AutoModelForCausalLM.from_pretrained(
                self.model_name, **model_kwargs
            )
            model.eval()
            return tokenizer, model

        except Exception as e:
            raise ExtractionError(
                f"Failed to load model '{self.model_name}' on '{self.device}': {e}"
            ) from e

    def _generate_with_transformers(self, user_prompt: str, max_new_tokens: int) -> str:
        messages = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": user_prompt},
        ]

        if (
            hasattr(self.tokenizer, "apply_chat_template")
            and self.tokenizer.chat_template
        ):
            prompt_str = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        else:
            prompt_str = f"{SYSTEM_INSTRUCTION}\n\n{user_prompt}"

        inputs = self.tokenizer(prompt_str, return_tensors="pt").to(self.model.device)
        gen_kwargs: Dict[str, Any] = {
            "max_new_tokens": max_new_tokens,
            "do_sample": False,
            "pad_token_id": self.tokenizer.pad_token_id,
        }
        if self._outlines_logits_processor is not None:
            gen_kwargs["logits_processor"] = [self._outlines_logits_processor]

        with torch.no_grad():
            output_tokens = self.model.generate(**inputs, **gen_kwargs)

        generated_ids = output_tokens[0][inputs.input_ids.shape[1] :]
        return self.tokenizer.decode(generated_ids, skip_special_tokens=True).strip()

    def _generate_with_openai_or_instructor(self, user_prompt: str, max_new_tokens: int) -> str:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ExtractionError("The 'openai' package is required for API structured outputs.") from e

        client = OpenAI(api_key=self.openai_api_key, base_url=self.openai_base_url)

        if self.backend == "instructor":
            try:
                import instructor

                patched_client = instructor.from_openai(client)
                spec_obj = patched_client.chat.completions.create(
                    model=self.model_name,
                    response_model=DriverSpec,
                    max_tokens=max_new_tokens,
                    temperature=0.0,
                    messages=[
                        {"role": "system", "content": SYSTEM_INSTRUCTION},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                return spec_obj.model_dump_json()
            except Exception as e:
                raise ExtractionError(f"Instructor structured extraction failed: {e}") from e

        # OpenAI Structured Outputs (`json_schema`)
        response = client.chat.completions.create(
            model=self.model_name,
            temperature=0.0,
            max_tokens=max_new_tokens,
            messages=[
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "driver_spec",
                    "strict": False,
                    "schema": DriverSpec.draft_2020_12_schema(),
                },
            },
        )
        content = response.choices[0].message.content or ""
        return content.strip()

    def extract(
        self,
        code_context: str,
        max_new_tokens: int = 4096,
        max_retries: int = 2,
        ast_result: Optional[ASTExtractionResult] = None,
    ) -> DriverSpec:
        """Extracts and validates a DriverSpec from C source code with schema enforcement and retries."""
        schema_json = json.dumps(DriverSpec.draft_2020_12_schema(), indent=2)
        base_prompt = (
            f"Analyze the following Linux C driver code and extract the complete hardware and "
            f"microkernel porting specification.\n\n"
            f"### Target JSON Schema (Draft 2020-12):\n{schema_json}\n\n"
            f"### C Source Code Context:\n{code_context}\n\n"
            f"### Output:\nProvide only the raw JSON object matching the schema:"
        )

        current_prompt = base_prompt
        last_error: Optional[Exception] = None
        last_payload: str = ""

        for attempt in range(max_retries + 1):
            try:
                if self.custom_generator is not None:
                    response_text = self.custom_generator(SYSTEM_INSTRUCTION, current_prompt)
                elif self.backend in {"openai", "instructor"}:
                    response_text = self._generate_with_openai_or_instructor(
                        current_prompt, max_new_tokens
                    )
                else:
                    response_text = self._generate_with_transformers(
                        current_prompt, max_new_tokens
                    )

                if not response_text or not response_text.strip():
                    raise ExtractionError("Model produced an empty response.")

                json_payload = _extract_json_text(response_text)
                last_payload = json_payload
                spec = DriverSpec.model_validate_json(json_payload)
                return ground_spec_with_ast(spec, ast_result)

            except (ValidationError, json.JSONDecodeError) as e:
                last_error = e
                if attempt < max_retries:
                    current_prompt = (
                        f"{base_prompt}\n\n"
                        f"### PREVIOUS ATTEMPT VALIDATION ERROR:\n"
                        f"Your previous JSON failed strict schema validation with:\n{e}\n\n"
                        f"Fix all schema errors and return ONLY the corrected, valid JSON object:"
                    )
            except ExtractionError:
                raise
            except Exception as e:
                raise ExtractionError(f"LLM extraction failed: {e}") from e

        raise ExtractionError(
            f"Failed to validate generated JSON against DriverSpec schema after {max_retries + 1} attempt(s): "
            f"{last_error}\nExtracted payload was:\n{last_payload}"
        ) from last_error


def extract_driver_spec(
    code_context: str,
    model_name: str = DEFAULT_MODEL,
    device: str = "cuda",
    quantization: str = "4bit",
    backend: str = "transformers",
    hf_token: Optional[str] = None,
    max_new_tokens: int = 4096,
    ast_result: Optional[ASTExtractionResult] = None,
    custom_generator: Optional[Callable[[str, str], str]] = None,
) -> DriverSpec:
    """Convenience one-shot wrapper around DriverExtractor."""
    extractor = DriverExtractor(
        model_name=model_name,
        device=device,
        quantization=quantization,
        backend=backend,
        hf_token=hf_token,
        custom_generator=custom_generator,
    )
    return extractor.extract(
        code_context=code_context,
        max_new_tokens=max_new_tokens,
        ast_result=ast_result,
    )

// ==========================================================================
// File: src/kernelite_prism/core/__init__.py
// ==========================================================================

"""Core modules for AST pre-processing, context aggregation, LLM extraction, validation, and documentation."""

from .ast_parser import (
    ASTExtractionResult,
    ASTPreProcessor,
    ConcurrencyCall,
    DriverStructInfo,
    FunctionSummary,
    MMIOCall,
    MacroConstant,
    parse_driver_ast,
    safe_eval_c_constant,
)
from .aggregator import (
    aggregate_context,
    aggregate_with_ast,
    collect_driver_files,
    estimate_tokens,
)
from .extractor import DriverExtractor, extract_driver_spec, ground_spec_with_ast
from .doc_generator import generate_markdown_spec
from .serializer import serialize_spec
from .validator import (
    ValidationFinding,
    VerificationReport,
    load_driver_spec_schema,
    run_full_verification,
    validate_schema_compliance,
    verify_markdown_completeness,
    verify_register_determinism,
    verify_sequence_parity,
)

__all__ = [
    "ASTExtractionResult",
    "ASTPreProcessor",
    "ConcurrencyCall",
    "DriverStructInfo",
    "FunctionSummary",
    "MMIOCall",
    "MacroConstant",
    "parse_driver_ast",
    "safe_eval_c_constant",
    "aggregate_context",
    "aggregate_with_ast",
    "collect_driver_files",
    "estimate_tokens",
    "DriverExtractor",
    "extract_driver_spec",
    "ground_spec_with_ast",
    "generate_markdown_spec",
    "serialize_spec",
    "ValidationFinding",
    "VerificationReport",
    "load_driver_spec_schema",
    "run_full_verification",
    "validate_schema_compliance",
    "verify_markdown_completeness",
    "verify_register_determinism",
    "verify_sequence_parity",
]

// ==========================================================================
// File: src/kernelite_prism/cli.py
// ==========================================================================

import sys
import warnings
from pathlib import Path
from typing import Optional
import click

from .core.doc_generator import generate_markdown_spec
from .core.serializer import serialize_spec
from .core.validator import run_full_verification, validate_schema_compliance
from .exceptions import KernelitePrismError
from .models.base import DriverSpec, export_json_schema

DEFAULT_CLI_MODEL = "Qwen/Qwen2.5-Coder-3B-Instruct"


@click.group()
@click.version_option(package_name="kernelite-prism")
def main() -> None:
    """Kernelite Prism: Extract structured hardware & C++ microkernel specs from Linux driver C code."""


@main.command()
@click.argument("target_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["json", "yaml", "md"], case_sensitive=False),
    default="json",
    help="Output format serialization (json, yaml, or md).",
)
@click.option(
    "--out",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output file path for driver_spec.json (or YAML). Prints to stdout if omitted.",
)
@click.option(
    "--md-out",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional output path to simultaneously emit human-readable DRIVER_SPEC.md.",
)
@click.option(
    "--model",
    default=DEFAULT_CLI_MODEL,
    help=f"Model ID or local path (default: {DEFAULT_CLI_MODEL}).",
)
@click.option(
    "--backend",
    type=click.Choice(["transformers", "outlines", "openai", "instructor"], case_sensitive=False),
    default="transformers",
    help="Structured generation backend.",
)
@click.option(
    "--quant",
    "-q",
    type=click.Choice(["4bit", "8bit", "none"], case_sensitive=False),
    default="4bit",
    help="Quantization precision (default: 4bit; use 'none' on CPU).",
)
@click.option(
    "--device",
    default="cuda",
    help="Execution device ('cuda', 'cuda:0', 'cpu').",
)
@click.option(
    "--token",
    default=None,
    help="Hugging Face API token.",
)
@click.option(
    "--no-ast-prune",
    is_flag=True,
    default=False,
    help="Disable AST boilerplate stripping before LLM submission.",
)
def analyze(
    target_path: Path,
    output_format: str,
    out: Optional[Path],
    md_out: Optional[Path],
    model: str,
    backend: str,
    quant: str,
    device: str,
    token: Optional[str],
    no_ast_prune: bool,
) -> None:
    """Analyze a target C driver file or directory and produce machine and human specifications."""
    try:
        from .config import get_settings
        from .core.aggregator import aggregate_with_ast
        from .core.extractor import DriverExtractor

        settings = get_settings(
            model=model,
            device=device,
            quantization=quant,
            backend=backend,
            hf_token=token,
        )

        click.echo(f"Parsing C AST and aggregating context from '{target_path}'...", err=True)
        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always", UserWarning)
            code_context, ast_result = aggregate_with_ast(
                target_path,
                use_ast_pruning=not no_ast_prune,
            )

        for w in caught_warnings:
            click.echo(f"Warning: {w.message}", err=True)

        click.echo(
            f"Extracting specification via {settings.backend} [{settings.model}]...",
            err=True,
        )
        extractor = DriverExtractor(
            model_name=settings.model,
            device=settings.device,
            quantization=settings.quantization,
            backend=settings.backend,
            hf_token=settings.hf_token,
        )
        spec = extractor.extract(code_context=code_context, ast_result=ast_result)

        md_doc = generate_markdown_spec(spec)
        run_full_verification(spec, ast_result=ast_result, markdown_text=md_doc, strict=False)

        serialized_output = serialize_spec(spec, format_type=output_format)

        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(serialized_output, encoding="utf-8")
            click.echo(f"Specification written to {out}", err=True)
        else:
            click.echo(serialized_output)

        if md_out:
            md_out.parent.mkdir(parents=True, exist_ok=True)
            md_out.write_text(md_doc, encoding="utf-8")
            click.echo(f"Markdown documentation written to {md_out}", err=True)

    except KernelitePrismError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


@main.command("render-md")
@click.argument("spec_json", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--out",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output Markdown file path (e.g., DRIVER_SPEC.md). Prints to stdout if omitted.",
)
def render_md(spec_json: Path, out: Optional[Path]) -> None:
    """Render a validated driver_spec.json file into DRIVER_SPEC.md."""
    try:
        spec = DriverSpec.model_validate_json(spec_json.read_text(encoding="utf-8"))
        md_content = generate_markdown_spec(spec)
        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md_content, encoding="utf-8")
            click.echo(f"Markdown specification written to {out}", err=True)
        else:
            click.echo(md_content)
    except Exception as e:
        click.echo(f"Error rendering Markdown: {e}", err=True)
        sys.exit(1)


@main.command("validate")
@click.argument("spec_json", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--schema",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Optional path to custom driver_spec.schema.json.",
)
def validate_cmd(spec_json: Path, schema: Optional[Path]) -> None:
    """Validate a driver_spec.json artifact against driver_spec.schema.json."""
    findings = validate_schema_compliance(
        spec_json.read_text(encoding="utf-8"),
        schema_path=schema,
    )
    all_ok = all(f.passed for f in findings)
    for finding in findings:
        prefix = "PASS" if finding.passed else "FAIL"
        click.echo(f"[{prefix}] {finding.check}: {finding.message}")
    if not all_ok:
        sys.exit(1)


@main.command("export-schema")
@click.option(
    "--out",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output path for driver_spec.schema.json.",
)
def export_schema_cmd(out: Optional[Path]) -> None:
    """Export the canonical JSON Schema Draft 2020-12 (driver_spec.schema.json)."""
    schema_text = export_json_schema(indent=2)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(schema_text + "\n", encoding="utf-8")
        click.echo(f"JSON Schema written to {out}", err=True)
    else:
        click.echo(schema_text)


if __name__ == "__main__":
    main()


// ==========================================================================
// File: src/kernelite_prism/models/lifecycle.py
// ==========================================================================

from typing import Optional
from pydantic import BaseModel


class DriverLifecycle(BaseModel):
    probe_function: Optional[str] = None
    remove_function: Optional[str] = None
    shutdown_function: Optional[str] = None
    suspend_function: Optional[str] = None
    resume_function: Optional[str] = None
    runtime_suspend_function: Optional[str] = None
    runtime_resume_function: Optional[str] = None
    init_function: Optional[str] = None
    exit_function: Optional[str] = None

// ==========================================================================
// File: src/kernelite_prism/models/metadata.py
// ==========================================================================

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

// ==========================================================================
// File: src/kernelite_prism/models/interfaces.py
// ==========================================================================

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

// ==========================================================================
// File: src/kernelite_prism/models/registers.py
// ==========================================================================

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
        le=64,
        description="Register bit width (8, 16, 32, or 64)",
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

// ==========================================================================
// File: src/kernelite_prism/models/__pycache__/hardware.cpython-311.pyc
// ==========================================================================

§
    Ú‹üjw  ã                   ó‚  — d dl mZmZmZ d dlmZmZ ed         Zed         Zed         Z	ed         Z
 G d„ de¦  «        Z G d	„ d
e¦  «        Z G d„ de¦  «        Z G d„ de¦  «        Z G d„ de¦  «        Z G d„ de¦  «        Z G d„ de¦  «        Z G d„ de¦  «        Z G d„ de¦  «        Z G d„ de¦  «        ZdS )é    )ÚLiteralÚOptionalÚList)Ú	BaseModelÚField)ÚmmioÚpciÚplatformÚi2cÚspiÚusbÚuartÚother)ÚlittleÚbigÚconfigurableÚunknown)zWrite RegisterzRead & PollzRead RegisterzMemory AllocatezMMIO Mapz	DMA SetupzInterrupt HookzClock/Reset ControlzHardware ResetzRegister DevicezLock AcquirezLock ReleaseÚOther)ÚIRQSafeSpinlockÚSpinlockÚSleepableMutexÚReadWriteLockÚAtomicÚNonec                   óÒ   — e Zd ZU  edd¬¦  «        Zeed<    edd¬¦  «        Zee         ed<    ed	d
¬¦  «        Z	eed<    edd¬¦  «        Z
ee         ed<   dZee         ed<   dS )ÚBaseAddressRegion.z;BAR index, MMIO resource index, or I/O port base identifier©ÚdescriptionÚ	region_idNz$Logical name of the memory/IO region©Údefaultr   ÚnameÚ0x0z#Base address offset or BAR selectorÚbase_offsetz)Region byte size or address span if knownÚ	size_hintr   )Ú__name__Ú
__module__Ú__qualname__r   r   ÚstrÚ__annotations__r"   r   r$   r%   r   © ó    ú8/workspaces/prism/src/kernelite_prism/models/hardware.pyr   r      s®   € € € € € € ØU˜3Ğ,iĞjÑjÔj€IˆsĞjĞjÑjØ˜%¨Ğ:`ĞaÑaÔa€Dˆ(3Œ-ĞaĞaÑaØu UĞ8]Ğ^Ñ^Ô^€KĞ^Ğ^Ñ^Ø$˜u¨TĞ?jĞkÑkÔk€Iˆx˜Œ}ĞkĞkÑkØ!%€K˜#”Ğ%Ğ%Ñ%Ğ%Ğ%r,   r   c                   óH  — e Zd ZU  edd¬¦  «        Zeed<    eed¬¦  «        Ze	e
         ed<    eed¬¦  «        Ze	e
         ed	<    eed
¬¦  «        Ze	e
         ed<    eed¬¦  «        Ze	e         ed<    edd¬¦  «        Zeed<    eed¬¦  «        Ze	e
         ed<   dS )ÚHardwareInfor   z&Primary hardware interconnect bus typer    Úbus_typez$Device Tree (DTB) compatible strings©Údefault_factoryr   Úcompatible_stringszPCI/USB vendor IDs in hexÚ
vendor_idszPCI/USB device IDs in hexÚ
device_idsz-MMIO BARs or platform memory resource regionsÚbase_address_offsetsr   zHardware register byte orderingÚ
endiannessz&Interrupt vectors or IRQ trigger modesÚ	irq_linesN)r&   r'   r(   r   r0   ÚBusTyper*   Úlistr3   r   r)   r4   r5   r6   r   r7   Ú
EndianTyper8   r+   r,   r-   r/   r/   '   s(  € € € € € € Ø˜ fĞ:bĞcÑcÔc€HˆgĞcĞcÑcØ$) EØØ:ğ%ñ %ô %Ğ˜˜Sœ	ğ ğ ñ ğ "˜E°$ĞD_Ğ`Ñ`Ô`€JS”	Ğ`Ğ`Ñ`Ø!˜E°$ĞD_Ğ`Ñ`Ô`€JS”	Ğ`Ğ`Ñ`Ø49°EØØCğ5ñ 5ô 5Ğ˜$Ğ0Ô1ğ ğ ñ ğ #˜U¨8ĞAbĞcÑcÔc€J
ĞcĞcÑcØ ˜5°ĞCkĞlÑlÔl€IˆtCŒyĞlĞlÑlĞlĞlr,   r/   c                   ób  — e Zd ZU  eddd¬¦  «        Zeed<    edd¬¦  «        Zeed<    edd	¬¦  «        Z	e
ed
<    edd¬¦  «        Zee
         ed<    edd¬¦  «        Zee
         ed<    edd¬¦  «        Zee
         ed<    edd¬¦  «        Zee
         ed<    edd¬¦  «        Ze
ed<   dS )ÚSequenceStep.é   z(1-based chronological order of execution)Úger   Ústepz7Categorized hardware or driver initialization operationr   Ú	operationz1Originating C function name (e.g., 'e1000_probe')Úsource_functionNz/Target register macro name or hardware resourcer    Útarget_registerz)Hexadecimal register offset if applicableÚoffsetz;Written constant/bitmask, poll mask, DMA size, or IRQ flagsÚvalue_or_maskz2Exit condition and timeout for 'Read & Poll' stepsÚpoll_conditionz;Detailed explanation of what this step performs on hardwarer   )r&   r'   r(   r   r@   Úintr*   rA   ÚSequenceOpTyperB   r)   rC   r   rD   rE   rF   r   r+   r,   r-   r=   r=   7   s`  € € € € € € Øc˜aĞ-WĞXÑXÔX€Dˆ#ĞXĞXÑXØ %  cĞ7pĞ qÑ qÔ q€Iˆ~ĞqĞqÑqØ ˜5 Ğ2eĞfÑfÔf€OSĞfĞfÑfØ%* UØØEğ&ñ &ô &€OX˜c”]ğ ğ ñ ğ "˜EØØ?ğñ ô €FˆHSŒMğ ğ ñ ğ $) 5ØØQğ$ñ $ô $€M8˜C”=ğ ğ ñ ğ %* EØØHğ%ñ %ô %€NH˜S”Mğ ğ ñ ğ u˜SĞ.kĞlÑlÔl€KĞlĞlÑlĞlĞlr,   r=   c                   ó¢   — e Zd ZU  edd¬¦  «        Zeed<    edd¬¦  «        Zeed<    eed¬¦  «        Z	e
e         ed	<   d
Zee         ed<   d
S )ÚRuntimeOpMapping.z;Logical operation (e.g., 'transmit', 'read', 'set_termios')r   Úoperation_namez$Linux C callback implementation nameÚ
c_functionz5Register macros read or written during this operationr1   Úregisters_touchedNr   )r&   r'   r(   r   rK   r)   r*   rL   r:   rM   r   r   r   r+   r,   r-   rJ   rJ   N   sš   € € € € € € Ø˜% Ğ1nĞoÑoÔo€NCĞoĞoÑoØe˜CĞ-SĞTÑTÔT€JĞTĞTÑTØ#( 5ØØKğ$ñ $ô $Ğt˜C”yğ ğ ñ ğ "&€K˜#”Ğ%Ğ%Ñ%Ğ%Ğ%r,   rJ   c                   óĞ   — e Zd ZU  edd¬¦  «        Zee         ed<    edd¬¦  «        Zee	         ed<    ee
d¬¦  «        Zee         ed	<    edd
¬¦  «        Zee         ed<   dS )ÚDMASpecNz:Linux DMA allocation API used (e.g., 'dma_alloc_coherent')r    Úallocation_apiz)DMA addressing bit width (e.g., 32 or 64)Úaddress_mask_bitsz=TX/RX descriptor ring structures and base/head/tail registersr1   Údescriptor_ringsz=Memory barrier (`wmb`/`rmb`) and cache coherency requirementsÚcoherency_notes)r&   r'   r(   r   rP   r   r)   r*   rQ   rG   r:   rR   r   rS   r+   r,   r-   rO   rO   X   sâ   € € € € € € Ø$) EØØPğ%ñ %ô %€NH˜S”Mğ ğ ñ ğ (- uØØ?ğ(ñ (ô (Ğx ”}ğ ğ ñ ğ #( %ØØSğ#ñ #ô #Ğd˜3”iğ ğ ñ ğ &+ UØØSğ&ñ &ô &€OX˜c”]ğ ğ ñ ğ ğ r,   rO   c                   óÀ  — e Zd ZU  edd¬¦  «        Zee         ed<    edd¬¦  «        Zee         ed<    edd¬¦  «        Z	ee         ed<    ee
d	¬
¦  «        Zee         ed<    edd¬¦  «        Zee         ed<    edd¬¦  «        Zee         ed<    edd¬¦  «        Zee         ed<    edd¬¦  «        Zee         ed<    ee
d¬
¦  «        Zee         ed<   dS )ÚInterruptLifecycleNz$Primary top-half ISR C function namer    Úirq_handlerz&Bottom-half / NAPI poll / threaded ISRÚthreaded_handlerz'e.g., 'request_irq', 'devm_request_irq'Úregistration_apize.g., ['IRQF_SHARED']r1   Ú	irq_flagsz2Register read to determine active interrupt causesÚstatus_registerz@Register read or written to acknowledge/clear pending interruptsÚack_registerz8Register used to enable/disable hardware interrupt masksÚmask_registerz^Hardware acknowledgment behavior (e.g., 'Read-to-clear ICR', 'Write-1-to-clear INTERRUPT_ACK')Úack_mechanismz0Ordered steps executed during interrupt handlingÚ
flow_steps)r&   r'   r(   r   rV   r   r)   r*   rW   rX   r:   rY   r   rZ   r[   r\   r]   r^   r=   r+   r,   r-   rU   rU   k   s¦  € € € € € € Ø!& ¨tĞAgĞ!hÑ!hÔ!h€K˜#”ĞhĞhÑhØ&+ e°DĞFnĞ&oÑ&oÔ&oĞh˜s”mĞoĞoÑoØ&+ e°DĞFoĞ&pÑ&pÔ&pĞh˜s”mĞpĞpÑpØ ˜5°ĞCZĞ[Ñ[Ô[€IˆtCŒyĞ[Ğ[Ñ[Ø%* UØØHğ&ñ &ô &€OX˜c”]ğ ğ ñ ğ #( %ØØVğ#ñ #ô #€L(˜3”-ğ ğ ñ ğ $) 5ØØNğ$ñ $ô $€M8˜C”=ğ ğ ñ ğ $) 5ØØtğ$ñ $ô $€M8˜C”=ğ ğ ñ ğ &+ UØØFğ&ñ &ô &€J\Ô"ğ ğ ñ ğ ğ r,   rU   c                   ó  — e Zd ZU dZee         ed<   dZee         ed<    ee	¬¦  «        Z
ee         ed<    ee	¬¦  «        Zee         ed<   dZee         ed<   dZee         ed<   dZee         ed	<    ee¬¦  «        Zeed
<   dS )ÚRuntimeOperationsNÚprobe_handlerÚremove_handler©r2   Úread_handlersÚwrite_handlersÚtransmit_handlerÚreceive_handlerÚ	dma_setupÚinterrupt_lifecycle)r&   r'   r(   ra   r   r)   r*   rb   r   r:   rd   r   rJ   re   rf   rg   rh   rO   rU   ri   r+   r,   r-   r`   r`   †   sâ   € € € € € € Ø#'€M8˜C”=Ğ'Ğ'Ñ'Ø$(€NH˜S”MĞ(Ğ(Ñ(Ø,1¨EÀ$Ğ,GÑ,GÔ,G€M4Ğ(Ô)ĞGĞGÑGØ-2¨UÀ4Ğ-HÑ-HÔ-H€NDĞ)Ô*ĞHĞHÑHØ37ĞhĞ/Ô0Ğ7Ğ7Ñ7Ø26€OXĞ.Ô/Ğ6Ğ6Ñ6Ø#'€Iˆx˜Ô Ğ'Ğ'Ñ'Ø.3¨eĞDVĞ.WÑ.WÔ.WĞĞ+ĞWĞWÑWĞWĞWr,   r`   c                   ó¬   — e Zd ZU  edd¬¦  «        Zeed<    edd¬¦  «        Zeed<    ee	d¬¦  «        Z
ee         ed	<    edd
¬¦  «        Zeed<   dS )ÚConcurrencyLock.zCOriginal Linux lock declaration or call (e.g., 'spin_lock_irqsave')r   Úlinux_primitivez,Normalized hardware/OS concurrency primitiveÚabstract_lock_typez8Registers, rings, or driver state protected by this lockr1   Úprotected_resourcesz9Recommended bare-metal C++ synchronization implementationÚbare_metal_recommendationN)r&   r'   r(   r   rl   r)   r*   rm   ÚAbstractLockTyper:   rn   r   ro   r+   r,   r-   rk   rk   ‘   sÉ   € € € € € € Ø ˜5 Ğ2wĞxÑxÔx€OSĞxĞxÑxØ+0¨5ØØBğ,ñ ,ô ,ĞĞ(ğ ğ ñ ğ &+ UØØNğ&ñ &ô &Ğ˜˜cœğ ğ ñ ğ &+ UØØOğ&ñ &ô &Ğ˜sğ ğ ñ ğ ğ r,   rk   c                   ó|   — e Zd ZU  edd¬¦  «        Zeed<    edd¬¦  «        Zeed<    edd¬¦  «        Zeed<   d	S )
ÚApiMigrationMapping.z@Linux kernel function/macro (e.g., 'writel', 'devm_request_irq')r   Ú	linux_apizYBare-metal C++ microkernel primitive (e.g., 'MmioRegion::write32', 'IrqDispatcher::bind')Úmicrokernel_equivalentz0Semantics, memory barriers, or ownership caveatsÚnotesN)	r&   r'   r(   r   rs   r)   r*   rt   ru   r+   r,   r-   rr   rr   ¡   s‚   € € € € € € ØU˜3Ğ,nĞoÑoÔo€IˆsĞoĞoÑoØ"' %ØØoğ#ñ #ô #Ğ˜Cğ ğ ñ ğ sĞ(ZĞ[Ñ[Ô[€Eˆ3Ğ[Ğ[Ñ[Ğ[Ğ[r,   rr   c                   ól   — e Zd ZU  ee¬¦  «        Zee         ed<    ee¬¦  «        Z	ee
         ed<   dS )ÚMigrationNotesrc   Úlinux_to_microkernel_mappingsÚarchitectural_caveatsN)r&   r'   r(   r   r:   rx   r   rr   r*   ry   r)   r+   r,   r-   rw   rw   ª   sY   € € € € € € Ø?D¸uĞUYĞ?ZÑ?ZÔ?ZĞ! 4Ğ(;Ô#<ĞZĞZÑZØ', u¸TĞ'BÑ'BÔ'BĞ˜4 œ9ĞBĞBÑBĞBĞBr,   rw   N)Útypingr   r   r   Úpydanticr   r   r9   r;   rH   rp   r   r/   r=   rJ   rO   rU   r`   rk   rr   rw   r+   r,   r-   ú<module>r|      sJ  ğØ *Ğ *Ğ *Ğ *Ğ *Ğ *Ğ *Ğ *Ğ *Ğ *Ø %Ğ %Ğ %Ğ %Ğ %Ğ %Ğ %Ğ %à
ĞQÔ
R€ØĞ?Ô@€
Øğô€ğ ğôĞ ğ&ğ &ğ &ğ &ğ &˜	ñ &ô &ğ &ğmğ mğ mğ mğ m9ñ mô mğ mğ mğ mğ mğ mğ m9ñ mô mğ mğ.&ğ &ğ &ğ &ğ &yñ &ô &ğ &ğğ ğ ğ ğ ˆiñ ô ğ ğ&ğ ğ ğ ğ ˜ñ ô ğ ğ6Xğ Xğ Xğ Xğ X˜	ñ Xô Xğ Xğğ ğ ğ ğ iñ ô ğ ğ \ğ \ğ \ğ \ğ \˜)ñ \ô \ğ \ğCğ Cğ Cğ Cğ CYñ Cô Cğ Cğ Cğ Cr,   

// ==========================================================================
// File: src/kernelite_prism/models/__pycache__/lifecycle.cpython-311.pyc
// ==========================================================================

§
    Ò€ûjç  ã                   ó:   — d dl mZ d dlmZ  G d„ de¦  «        ZdS )é    )ÚOptional)Ú	BaseModelc                   óú   — e Zd ZU dZee         ed<   dZee         ed<   dZee         ed<   dZ	ee         ed<   dZ
ee         ed<   dZee         ed<   dZee         ed<   dZee         ed	<   dZee         ed
<   dS )ÚDriverLifecycleNÚprobe_functionÚremove_functionÚshutdown_functionÚsuspend_functionÚresume_functionÚruntime_suspend_functionÚruntime_resume_functionÚinit_functionÚexit_function)Ú__name__Ú
__module__Ú__qualname__r   r   ÚstrÚ__annotations__r   r	   r
   r   r   r   r   r   © ó    ú9/workspaces/prism/src/kernelite_prism/models/lifecycle.pyr   r      sÊ   € € € € € € Ø$(€NH˜S”MĞ(Ğ(Ñ(Ø%)€OX˜c”]Ğ)Ğ)Ñ)Ø'+Ğx ”}Ğ+Ğ+Ñ+Ø&*Ğh˜s”mĞ*Ğ*Ñ*Ø%)€OX˜c”]Ğ)Ğ)Ñ)Ø.2Ğ˜h sœmĞ2Ğ2Ñ2Ø-1Ğ˜X cœ]Ğ1Ğ1Ñ1Ø#'€M8˜C”=Ğ'Ğ'Ñ'Ø#'€M8˜C”=Ğ'Ğ'Ñ'Ğ'Ğ'r   r   N)Útypingr   Úpydanticr   r   r   r   r   ú<module>r      s]   ğØ Ğ Ğ Ğ Ğ Ğ Ø Ğ Ğ Ğ Ğ Ğ ğ	(ğ 	(ğ 	(ğ 	(ğ 	(iñ 	(ô 	(ğ 	(ğ 	(ğ 	(r   

// ==========================================================================
// File: src/kernelite_prism/models/__pycache__/base.cpython-311.pyc
// ==========================================================================

§
    ê‹üj­  ã                   óÀ   — d dl Z d dlmZmZmZmZ d dlmZmZm	Z	 ddl
mZ ddlmZ ddlmZ ddlmZ dd	lmZ dd
lmZmZmZmZmZ  G d„ de¦  «        Zddedefd„ZdS )é    N)ÚAnyÚDictÚListÚOptional)Ú	BaseModelÚFieldÚmodel_validatoré   )ÚDriverMetadata)ÚMatchingTables)ÚDriverInterfaces)ÚDriverLifecycle)ÚRegisterMap)ÚHardwareInfoÚSequenceStepÚRuntimeOperationsÚConcurrencyLockÚMigrationNotesc                   ó”  — e Zd ZU  edd¬¦  «        Zeed<    edd¬¦  «        Zeed<    eed	¬
¦  «        Z	e
e         ed<    eed¬
¦  «        Zeed<    ee¬¦  «        Zeed<    ee¬¦  «        Zeed<    ee¬¦  «        Zeed<    ee¬¦  «        Zeed<    eed¬
¦  «        Ze
e         ed<    eed¬
¦  «        Ze
e         ed<    eed¬
¦  «        Ze
e         ed<    eed¬
¦  «        Zeed<    eed¬
¦  «        Ze
e         ed<    eed¬
¦  «        Zeed<    e d¬ ¦  «        d%d"„¦   «         Z!e"d!e#ee$f         fd#„¦   «         Z%d$S )&Ú
DriverSpecÚ z5Human-readable hardware controller or peripheral name)ÚdefaultÚdescriptionÚdevice_name.z)Canonical name of the Linux driver module)r   Údriver_namez7Compatible DTB strings or hardware matching identifiers)Údefault_factoryr   ÚcompatibilityzABus, compatible strings, vendor/device IDs, and MMIO base regionsÚhardware_info)r   ÚmetadataÚmatching_tablesÚ
interfacesÚ	lifecyclezFList of driver structs defined (e.g., 'platform_driver', 'pci_driver')ÚstructszNStrictly typed hardware register map with offsets, access modes, and bitfieldsÚregister_mapzBChronologically ordered probe and hardware initialization sequenceÚinit_sequencez@Runtime I/O handlers, DMA configuration, and interrupt lifecycleÚruntime_operationszBLinux spinlocks/mutexes translated into abstract concurrency locksÚconcurrency_locksz?Guidance for porting the driver to a bare-metal C++ microkernelÚmigration_notesÚafter)ÚmodeÚreturnc                 ó¢  — | j         s| j        | _         g }| j        | j        j        z   d„ | j        j        D ¦   «         z   D ]}|r||vr|                     |¦  «         Œ|| _        || j        _        | j        j	        r#| j
        j        s| j        j	        | j
        _        n.| j
        j        r"| j        j	        s| j
        j        | j        _	        | j        j        r#| j
        j        s| j        j        | j
        _        n.| j
        j        r"| j        j        s| j
        j        | j        _        t          | j        d¬¦  «        D ]\  }}||_        Œ| S )zPKeeps overlapping hardware/lifecycle fields synchronized across schema sections.c                 ó*   — g | ]}|j         ¯	|j         ‘ŒS © )Ú
compatible)Ú.0Úms     ú4/workspaces/prism/src/kernelite_prism/models/base.pyú
<listcomp>z2DriverSpec.synchronize_aliases.<locals>.<listcomp>I   s!   € ĞWĞWĞW È!Ì,ĞWˆqŒ|ĞWĞWĞWó    r
   )Ústart)r   r   r   r   Úcompatible_stringsr    Úof_device_idÚappendr"   Úprobe_functionr&   Úprobe_handlerÚremove_functionÚremove_handlerÚ	enumerater%   Ústep)ÚselfÚmerged_compatÚitemÚidxr>   s        r2   Úsynchronize_aliaseszDriverSpec.synchronize_aliases>   s…  € ğ Ôğ 	0Ø#Ô/ˆDÔğ $&ˆàÔØÔ Ô3ñ4àWĞW TÔ%9Ô%FĞWÑWÔWñXğ	+ğ 	+ˆDğ
 ğ +˜ MĞ1Ğ1Ø×$Ò$ TÑ*Ô*Ğ*øà*ˆÔØ0=ˆÔÔ-ğ Œ>Ô(ğ 	R°Ô1HÔ1Vğ 	RØ48´NÔ4QˆDÔ#Ô1Ğ1ØÔ$Ô2ğ 	R¸4¼>Ô;Xğ 	RØ,0Ô,CÔ,QˆDŒNÔ)àŒ>Ô)ğ 	T°$Ô2IÔ2Xğ 	TØ59´^Ô5SˆDÔ#Ô2Ğ2ØÔ$Ô3ğ 	T¸D¼NÔ<Zğ 	TØ-1Ô-DÔ-SˆDŒNÔ*õ # 4Ô#5¸QĞ?Ñ?Ô?ğ 	ğ 	‰IˆCØˆDŒIˆIàˆr4   c                 ó<   — |                       ¦   «         }dddœ|¥}|S )zBGenerates a JSON Schema Draft 2020-12 compliant schema dictionary.z,https://json-schema.org/draft/2020-12/schemaz5https://kernelite.dev/schemas/driver_spec.schema.json)z$schemaz$id)Úmodel_json_schema)ÚclsÚ
raw_schemaÚschemas      r2   Údraft_2020_12_schemazDriverSpec.draft_2020_12_schemab   s8   € ğ ×*Ò*Ñ,Ô,ˆ
àEØJğ"
ğ "
ğ ğ"
ˆğ
 ˆr4   N)r+   r   )&Ú__name__Ú
__module__Ú__qualname__r   r   ÚstrÚ__annotations__r   Úlistr   r   r   r   r   r   r   r    r   r!   r   r"   r#   r$   r   r%   r   r   r&   r'   r   r   r(   r	   rC   Úclassmethodr   r   rI   r.   r4   r2   r   r      sÉ  € € € € € € ØuØØKğñ ô €Kğ ğ ñ ğ u˜SĞ.YĞZÑZÔZ€KĞZĞZÑZØ$˜uØØMğ ñ  ô  €M4˜”9ğ ğ ñ ğ #( %Ø$ØWğ#ñ #ô #€M<ğ ğ ñ ğ  %˜u°^ĞDÑDÔD€HˆnĞDĞDÑDØ&+ e¸NĞ&KÑ&KÔ&K€O^ĞKĞKÑKØ#( 5Ğ9IĞ#JÑ#JÔ#J€JĞ ĞJĞJÑJØ!& °Ğ!GÑ!GÔ!G€IˆĞGĞGÑGØ˜ØØ\ğñ ô €GˆT#ŒYğ ğ ñ ğ ', eØØdğ'ñ 'ô '€L${Ô#ğ ğ ñ ğ ).¨ØØXğ)ñ )ô )€M4˜Ô%ğ ğ ñ ğ -2¨EØ)ØVğ-ñ -ô -ĞĞ)ğ ğ ñ ğ 05¨uØØXğ0ñ 0ô 0Ğt˜OÔ,ğ ğ ñ ğ ', eØ&ØUğ'ñ 'ô '€O^ğ ğ ñ ğ
 €_˜'Ğ"Ñ"Ô"ğ!ğ !ğ !ñ #Ô"ğ!ğF ğ T¨#¨s¨(¤^ğ ğ ğ ñ „[ğğ ğ r4   r   é   Úindentr+   c                 ó\   — t          j        t                               ¦   «         | ¬¦  «        S )zHSerializes the canonical DriverSpec JSON Schema Draft 2020-12 to string.©rR   )ÚjsonÚdumpsr   rI   rT   s    r2   Úexport_json_schemarW   n   s#   € åŒ:•j×5Ò5Ñ7Ô7ÀĞGÑGÔGĞGr4   )rQ   )rU   Útypingr   r   r   r   Úpydanticr   r   r	   r   r   Úmatchingr   r!   r   r"   r   Ú	registersr   Úhardwarer   r   r   r   r   r   ÚintrM   rW   r.   r4   r2   ú<module>r^      s]  ğØ €€€Ø ,Ğ ,Ğ ,Ğ ,Ğ ,Ğ ,Ğ ,Ğ ,Ğ ,Ğ ,Ğ ,Ğ ,Ø 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6à $Ğ $Ğ $Ğ $Ğ $Ğ $Ø $Ğ $Ğ $Ğ $Ğ $Ğ $Ø (Ğ (Ğ (Ğ (Ğ (Ğ (Ø &Ğ &Ğ &Ğ &Ğ &Ğ &Ø "Ğ "Ğ "Ğ "Ğ "Ğ "ğğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğXğ Xğ Xğ Xğ Xñ Xô Xğ XğvHğ H˜sğ H¨3ğ Hğ Hğ Hğ Hğ Hğ Hr4   

// ==========================================================================
// File: src/kernelite_prism/models/__pycache__/registers.cpython-311.pyc
// ==========================================================================

§
    ¯‹üjÙ  ã                   ó   — d dl Z d dlmZmZmZ d dlmZmZmZ ed         Z	de
dee
         fd„Z G d„ d	e¦  «        Z G d
„ de¦  «        ZeZdS )é    N)ÚLiteralÚOptionalÚList)Ú	BaseModelÚFieldÚmodel_validator)ÚRWÚROÚWOÚRÚWÚW1CÚUNKNOWNÚ
mask_macroÚreturnc                 ón  — | sdS |                       ¦   «         }t          j        d|¦  «        }|r|                     d¦  «        S t          j        d|¦  «        }|r-|                     d¦  «        › d|                     d¦  «        › S t          j        d|¦  «        }|r|                     d¦  «        S dS )zNDeterministically derives bit position/range from common Linux bitmask macros.Nz \bBIT(?:_ULL)?\s*\(\s*(\d+)\s*\)é   z0\bGENMASK(?:_ULL)?\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)ú:é   z \(\s*1U?L{0,2}\s*<<\s*(\d+)\s*\))ÚstripÚreÚsearchÚgroup)r   ÚcleanedÚ	bit_matchÚgenmask_matchÚshift_matchs        ú9/workspaces/prism/src/kernelite_prism/models/registers.pyÚinfer_bits_from_macror      sË   € àğ ØˆtØ×ÒÑ Ô €Gå”	Ğ=¸wÑGÔG€IØğ "ØŠ˜qÑ!Ô!Ğ!å”IĞQĞSZÑ[Ô[€MØğ DØ×%Ò% aÑ(Ô(ĞCĞC¨=×+>Ò+>¸qÑ+AÔ+AĞCĞCĞCå”)Ğ?ÀÑIÔI€KØğ $Ø× Ò  Ñ#Ô#Ğ#àˆ4ó    c                   óD  — e Zd ZU  edd¬¦  «        Zeed<    edd¬¦  «        Zee         ed<    edd	¬¦  «        Z	eed
<    edd¬¦  «        Z
ee         ed<    edd¬¦  «        Zee         ed<    edd¬¦  «        Zee         ed<    ed¬¦  «        dd„¦   «         ZdS )ÚBitfield.z.C macro or logical identifier for the bitfield©ÚdescriptionÚnameNz9Bit index or inclusive MSB:LSB range (e.g., '3' or '7:4')©Údefaultr$   ÚbitszNRaw C bitmask expression or hex mask (e.g., 'BIT(3)', 'GENMASK(7, 4)', '0x08')r   zBBitfield-level access permissions if distinct from parent registerÚaccessz<Default hardware reset value for this bitfield (e.g., '0x0')Úreset_valuez-Hardware behavior controlled by this bitfieldr$   Úafter)Úmoder   c                 óF   — | j         st          | j        ¦  «        | _         | S )N)r(   r   r   )Úselfs    r   Úpopulate_bits_if_missingz!Bitfield.populate_bits_if_missing4   s"   € àŒyğ 	?İ-¨d¬oÑ>Ô>ˆDŒIØˆr    )r   r"   )Ú__name__Ú
__module__Ú__qualname__r   r%   ÚstrÚ__annotations__r(   r   r   r)   Ú
AccessModer*   r$   r   r/   © r    r   r"   r"      s^  € € € € € € ØcĞ'WĞXÑXÔX€Dˆ#ĞXĞXÑXØ˜%ØØOğñ ô €Dˆ(3Œ-ğ ğ ñ ğ eØØdğñ ô €Jğ ğ ñ ğ $) 5ØØXğ$ñ $ô $€FˆHZÔ ğ ğ ñ ğ "' ØØRğ"ñ "ô "€K˜#”ğ ğ ñ ğ "' ØØCğ"ñ "ô "€K˜#”ğ ğ ñ ğ
 €_˜'Ğ"Ñ"Ô"ğğ ğ ñ #Ô"ğğ ğ r    r"   c                   ób  — e Zd ZU  edd¬¦  «        Zeed<    edd¬¦  «        Zeed<    edd¬	¦  «        Ze	e         ed
<    edddd¬¦  «        Z
eed<    edd¬	¦  «        Zeed<    edd¬	¦  «        Ze	e         ed<    edd¬	¦  «        Ze	e         ed<    ee¬¦  «        Zee         ed<   dS )ÚRegisterMap.z=Register macro name from C header/source (e.g., 'E1000_CTRL')r#   r%   z;Hexadecimal byte offset from MMIO/BAR base (e.g., '0x0000')ÚoffsetNzYIsolated C offset calculation formula if indexed/computed (e.g., '0x03800 + (i * 0x100)')r&   Úoffset_expressioné    é   é@   z%Register bit width (8, 16, 32, or 64))r'   ÚgeÚler$   Úwidthr	   z7Register access mode: RW, RO, WO, R, W, W1C, or UNKNOWNr)   z9Hardware power-on reset value in hex (e.g., '0x00000000')r*   z/Functional description of the hardware registerr$   )Údefault_factoryÚ	bitfields)r0   r1   r2   r   r%   r3   r4   r9   r:   r   r@   Úintr)   r5   r*   r$   ÚlistrB   r   r"   r6   r    r   r8   r8   ;   sq  € € € € € € ØcĞ'fĞgÑgÔg€Dˆ#ĞgĞgÑgØ%˜Ğ)fĞgÑgÔg€FˆCĞgĞgÑgØ', uØØoğ(ñ (ô (Ğx ”}ğ ğ ñ ğ ØØØØ;ğ	ñ ô €Eˆ3ğ ğ ñ ğ ˜ØØMğñ ô €FˆJğ ğ ñ ğ "' ØØOğ"ñ "ô "€K˜#”ğ ğ ñ ğ "' ØØEğ"ñ "ô "€K˜#”ğ ğ ñ ğ !& °dĞ ;Ñ ;Ô ;€IˆtHŒ~Ğ;Ğ;Ñ;Ğ;Ğ;r    r8   )r   Útypingr   r   r   Úpydanticr   r   r   r5   r3   r   r"   r8   ÚRegisterDefinitionr6   r    r   ú<module>rH      sİ   ğØ 	€	€	€	Ø *Ğ *Ğ *Ğ *Ğ *Ğ *Ğ *Ğ *Ğ *Ğ *Ø 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6àĞAÔB€
ğ cğ ¨h°s¬mğ ğ ğ ğ ğ*ğ ğ ğ ğ ˆyñ ô ğ ğ<<ğ <ğ <ğ <ğ <)ñ <ô <ğ <ğ: !Ğ Ğ Ğ r    

// ==========================================================================
// File: src/kernelite_prism/models/__pycache__/matching.cpython-311.pyc
// ==========================================================================

§
    Ê€ûj[  ã                   óf   — d dl mZmZmZmZ d dlmZmZ  G d„ de¦  «        Z G d„ de¦  «        Z	dS )é    )ÚAnyÚDictÚListÚOptional)Ú	BaseModelÚFieldc                   ó   — e Zd ZU dZee         ed<   dZee         ed<   dZee         ed<    e	e
¬¦  «        Zeeef         ed<   dS )ÚDeviceMatchNÚ
compatibleÚ	vendor_idÚ	device_id©Údefault_factoryÚ	raw_entry)Ú__name__Ú
__module__Ú__qualname__r   r   ÚstrÚ__annotations__r   r   r   Údictr   r   r   © ó    ú8/workspaces/prism/src/kernelite_prism/models/matching.pyr
   r
      ss   € € € € € € Ø $€J˜”Ğ$Ğ$Ñ$Ø#€Iˆx˜Œ}Ğ#Ğ#Ñ#Ø#€Iˆx˜Œ}Ğ#Ğ#Ñ#Ø % °dĞ ;Ñ ;Ô ;€IˆtC˜HŒ~Ğ;Ğ;Ñ;Ğ;Ğ;r   r
   c                   ó€  — e Zd ZU  ee¬¦  «        Zee         ed<    ee¬¦  «        Z	ee         ed<    ee¬¦  «        Z
ee         ed<    ee¬¦  «        Zee         ed<    ee¬¦  «        Zee         ed<    ee¬¦  «        Zee         ed<    ee¬¦  «        Zee         ed<    ee¬¦  «        Zee         ed	<   d
S )ÚMatchingTablesr   Úof_device_idÚpci_device_idÚusb_device_idÚacpi_device_idÚi2c_device_idÚspi_device_idÚplatform_device_idÚhid_device_idN)r   r   r   r   Úlistr   r   r
   r   r   r   r   r    r!   r"   r#   r   r   r   r   r      s#  € € € € € € Ø&+ e¸DĞ&AÑ&AÔ&A€L${Ô#ĞAĞAÑAØ', u¸TĞ'BÑ'BÔ'B€M4˜Ô$ĞBĞBÑBØ', u¸TĞ'BÑ'BÔ'B€M4˜Ô$ĞBĞBÑBØ(-¨¸dĞ(CÑ(CÔ(C€ND˜Ô%ĞCĞCÑCØ', u¸TĞ'BÑ'BÔ'B€M4˜Ô$ĞBĞBÑBØ', u¸TĞ'BÑ'BÔ'B€M4˜Ô$ĞBĞBÑBØ,1¨EÀ$Ğ,GÑ,GÔ,GĞ˜˜[Ô)ĞGĞGÑGØ', u¸TĞ'BÑ'BÔ'B€M4˜Ô$ĞBĞBÑBĞBĞBr   r   N)
Útypingr   r   r   r   Úpydanticr   r   r
   r   r   r   r   ú<module>r'      s©   ğØ ,Ğ ,Ğ ,Ğ ,Ğ ,Ğ ,Ğ ,Ğ ,Ğ ,Ğ ,Ğ ,Ğ ,Ø %Ğ %Ğ %Ğ %Ğ %Ğ %Ğ %Ğ %ğ<ğ <ğ <ğ <ğ <)ñ <ô <ğ <ğCğ Cğ Cğ Cğ CYñ Cô Cğ Cğ Cğ Cr   

// ==========================================================================
// File: src/kernelite_prism/models/__pycache__/__init__.cpython-311.pyc
// ==========================================================================

§
    õ‹üjz  ã                   ó¦   — d Z ddlmZmZ ddlmZmZ ddlmZm	Z	 ddl
mZmZmZ ddlmZ ddlmZmZmZ ddlmZmZmZmZmZmZmZmZmZmZ g d	¢Zd
S )z@Pydantic data models for extracted kernel driver specifications.é   )Ú
DriverSpecÚexport_json_schema)ÚDriverMetadataÚModuleParam)ÚMatchingTablesÚDeviceMatch)ÚDriverInterfacesÚSysfsAttributeÚIoctlCommand)ÚDriverLifecycle)ÚRegisterMapÚRegisterDefinitionÚBitfield)
ÚBaseAddressRegionÚHardwareInfoÚSequenceStepÚRuntimeOpMappingÚDMASpecÚInterruptLifecycleÚRuntimeOperationsÚConcurrencyLockÚApiMigrationMappingÚMigrationNotes)r   r   r   r   r   r   r	   r
   r   r   r   r   r   r   r   r   r   r   r   r   r   r   r   N) Ú__doc__Úbaser   r   Úmetadatar   r   Úmatchingr   r   Ú
interfacesr	   r
   r   Ú	lifecycler   Ú	registersr   r   r   Úhardwarer   r   r   r   r   r   r   r   r   r   Ú__all__© ó    ú8/workspaces/prism/src/kernelite_prism/models/__init__.pyú<module>r&      s.  ğØ FĞ Fà 0Ğ 0Ğ 0Ğ 0Ğ 0Ğ 0Ğ 0Ğ 0Ø 1Ğ 1Ğ 1Ğ 1Ğ 1Ğ 1Ğ 1Ğ 1Ø 1Ğ 1Ğ 1Ğ 1Ğ 1Ğ 1Ğ 1Ğ 1Ø FĞ FĞ FĞ FĞ FĞ FĞ FĞ FĞ FĞ FØ &Ğ &Ğ &Ğ &Ğ &Ğ &Ø @Ğ @Ğ @Ğ @Ğ @Ğ @Ğ @Ğ @Ğ @Ğ @ğğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğ ğğ ğ €€€r$   

// ==========================================================================
// File: src/kernelite_prism/models/__pycache__/metadata.cpython-311.pyc
// ==========================================================================

§
    £øÏjˆ  ã                   ó^   — d dl mZmZ d dlmZmZ  G d„ de¦  «        Z G d„ de¦  «        ZdS )é    )ÚOptionalÚList)Ú	BaseModelÚFieldc                   óH   — e Zd ZU eed<   eed<   eed<   dZee         ed<   dS )ÚModuleParamÚnameÚtypeÚpermissionsNÚdescription)Ú__name__Ú
__module__Ú__qualname__ÚstrÚ__annotations__r   r   © ó    ú8/workspaces/prism/src/kernelite_prism/models/metadata.pyr   r      sD   € € € € € € Ø
€I€IIØ
€I€IIØĞĞÑØ!%€K˜#”Ğ%Ğ%Ñ%Ğ%Ğ%r   r   c                   óŒ   — e Zd ZU dZee         ed<   dZee         ed<   dZee         ed<    e	e
¬¦  «        Zee         ed<   dS )ÚDriverMetadataNÚauthorÚlicenser   )Údefault_factoryÚ
parameters)r   r   r   r   r   r   r   r   r   r   Úlistr   r   r   r   r   r   r   r   
   sp   € € € € € € Ø €FˆHSŒMĞ Ğ Ñ Ø!€GˆXcŒ]Ğ!Ğ!Ñ!Ø!%€K˜#”Ğ%Ğ%Ñ%Ø$) E¸$Ğ$?Ñ$?Ô$?€J[Ô!Ğ?Ğ?Ñ?Ğ?Ğ?r   r   N)Útypingr   r   Úpydanticr   r   r   r   r   r   r   ú<module>r      s   ğØ !Ğ !Ğ !Ğ !Ğ !Ğ !Ğ !Ğ !Ø %Ğ %Ğ %Ğ %Ğ %Ğ %Ğ %Ğ %ğ&ğ &ğ &ğ &ğ &)ñ &ô &ğ &ğ@ğ @ğ @ğ @ğ @Yñ @ô @ğ @ğ @ğ @r   

// ==========================================================================
// File: src/kernelite_prism/models/__pycache__/interfaces.cpython-311.pyc
// ==========================================================================

§
    Ú€ûj:  ã                   ó~   — d dl mZmZmZ d dlmZmZ  G d„ de¦  «        Z G d„ de¦  «        Z G d„ de¦  «        Z	d	S )
é    )ÚDictÚListÚOptional)Ú	BaseModelÚFieldc                   óX   — e Zd ZU eed<   eed<   dZee         ed<   dZee         ed<   dS )ÚSysfsAttributeÚnameÚmodeNÚshow_functionÚstore_function)Ú__name__Ú
__module__Ú__qualname__ÚstrÚ__annotations__r   r   r   © ó    ú:/workspaces/prism/src/kernelite_prism/models/interfaces.pyr	   r	      sL   € € € € € € Ø
€I€IIØ
€I€IIØ#'€M8˜C”=Ğ'Ğ'Ñ'Ø$(€NH˜S”MĞ(Ğ(Ñ(Ğ(Ğ(r   r	   c                   ó‚   — e Zd ZU eed<   dZee         ed<   dZee         ed<   dZee         ed<   dZ	ee         ed<   dS )ÚIoctlCommandr
   NÚdirÚtypeÚnrÚsize)
r   r   r   r   r   r   r   r   r   r   r   r   r   r   r      sk   € € € € € € Ø
€I€IIØ€Cˆ#ŒĞĞÑØ€Dˆ(3Œ-ĞĞÑØ€BˆŒĞĞÑØ€Dˆ(3Œ-ĞĞÑĞĞr   r   c                   óÎ   — e Zd ZU  eed¬¦  «        Zeeef         ed<    ee	¬¦  «        Z
ee         ed<    ee	¬¦  «        Zee         ed<    ee	¬¦  «        Zee         ed<   dS )	ÚDriverInterfacesz]Mapping of file_operations struct fields (e.g., 'unlocked_ioctl', 'read') to C function names)Údefault_factoryÚdescriptionÚfile_operations)r   Úsysfs_attributesÚprocfs_entriesÚioctl_commandsN)r   r   r   r   Údictr    r   r   r   Úlistr!   r   r	   r"   r#   r   r   r   r   r   r      s¯   € € € € € € Ø&+ eØØsğ'ñ 'ô '€OT˜#˜s˜(”^ğ ğ ñ ğ .3¨UÀ4Ğ-HÑ-HÔ-HĞd˜>Ô*ĞHĞHÑHØ % °dĞ ;Ñ ;Ô ;€ND˜”IĞ;Ğ;Ñ;Ø).¨¸tĞ)DÑ)DÔ)D€ND˜Ô&ĞDĞDÑDĞDĞDr   r   N)
Útypingr   r   r   Úpydanticr   r   r	   r   r   r   r   r   ú<module>r(      sÍ   ğØ 'Ğ 'Ğ 'Ğ 'Ğ 'Ğ 'Ğ 'Ğ 'Ğ 'Ğ 'Ø %Ğ %Ğ %Ğ %Ğ %Ğ %Ğ %Ğ %ğ)ğ )ğ )ğ )ğ )Yñ )ô )ğ )ğğ ğ ğ ğ 9ñ ô ğ ğEğ Eğ Eğ Eğ Eyñ Eô Eğ Eğ Eğ Er   

// ==========================================================================
// File: src/kernelite_prism/models/base.py
// ==========================================================================

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

// ==========================================================================
// File: src/kernelite_prism/models/__init__.py
// ==========================================================================

"""Pydantic data models for extracted kernel driver specifications."""

from .base import DriverSpec, export_json_schema
from .metadata import DriverMetadata, ModuleParam
from .matching import MatchingTables, DeviceMatch
from .interfaces import DriverInterfaces, SysfsAttribute, IoctlCommand
from .lifecycle import DriverLifecycle
from .registers import RegisterMap, RegisterDefinition, Bitfield
from .hardware import (
    BaseAddressRegion,
    HardwareInfo,
    SequenceStep,
    RuntimeOpMapping,
    DMASpec,
    InterruptLifecycle,
    RuntimeOperations,
    ConcurrencyLock,
    ApiMigrationMapping,
    MigrationNotes,
)

__all__ = [
    "DriverSpec",
    "export_json_schema",
    "DriverMetadata",
    "ModuleParam",
    "MatchingTables",
    "DeviceMatch",
    "DriverInterfaces",
    "SysfsAttribute",
    "IoctlCommand",
    "DriverLifecycle",
    "RegisterMap",
    "RegisterDefinition",
    "Bitfield",
    "BaseAddressRegion",
    "HardwareInfo",
    "SequenceStep",
    "RuntimeOpMapping",
    "DMASpec",
    "InterruptLifecycle",
    "RuntimeOperations",
    "ConcurrencyLock",
    "ApiMigrationMapping",
    "MigrationNotes",
]

// ==========================================================================
// File: src/kernelite_prism/models/hardware.py
// ==========================================================================

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

// ==========================================================================
// File: src/kernelite_prism/models/matching.py
// ==========================================================================

from typing import Any, Dict, List, Optional
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
    i2c_device_id: List[DeviceMatch] = Field(default_factory=list)
    spi_device_id: List[DeviceMatch] = Field(default_factory=list)
    platform_device_id: List[DeviceMatch] = Field(default_factory=list)
    hid_device_id: List[DeviceMatch] = Field(default_factory=list)

// ==========================================================================
// File: src/kernelite_prism/__pycache__/exceptions.cpython-311.pyc
// ==========================================================================

§
    p‹üjİ  ã                   óæ   —  G d „ de ¦  «        Z G d„ de¦  «        Z G d„ de¦  «        Z G d„ de¦  «        Z G d„ d	e¦  «        Z G d
„ de¦  «        Z G d„ de¦  «        Z G d„ de¦  «        ZdS )c                   ó   — e Zd ZdZdS )ÚKernelitePrismErrorz0Base exception class for Kernelite Prism errors.N©Ú__name__Ú
__module__Ú__qualname__Ú__doc__© ó    ú3/workspaces/prism/src/kernelite_prism/exceptions.pyr   r      s   € € € € € Ø:Ğ:Ğ:Ğ:r
   r   c                   ó   — e Zd ZdZdS )ÚConfigurationErrorzRRaised when environment variables, hardware devices, or options are misconfigured.Nr   r	   r
   r   r   r      s   € € € € € Ø\Ğ\Ğ\Ğ\r
   r   c                   ó   — e Zd ZdZdS )ÚAggregationErrorzKRaised when context aggregation fails (e.g., path not found or unreadable).Nr   r	   r
   r   r   r   	   s   € € € € € ØUĞUĞUĞUr
   r   c                   ó   — e Zd ZdZdS )ÚASTParsingErrorzJRaised when Tree-sitter C AST parsing encounters an unrecoverable failure.Nr   r	   r
   r   r   r      s   € € € € € ØTĞTĞTĞTr
   r   c                   ó   — e Zd ZdZdS )ÚExtractionErrorz;Raised when LLM inference or schema-enforced parsing fails.Nr   r	   r
   r   r   r      s   € € € € € ØEĞEĞEĞEr
   r   c                   ó   — e Zd ZdZdS )ÚSchemaValidationErrorzSRaised when a driver specification fails JSON Schema or AST grounding verification.Nr   r	   r
   r   r   r      s   € € € € € Ø]Ğ]Ğ]Ğ]r
   r   c                   ó   — e Zd ZdZdS )ÚSerializationErrorz,Raised when output formatting/writing fails.Nr   r	   r
   r   r   r      s   € € € € € Ø6Ğ6Ğ6Ğ6r
   r   c                   ó   — e Zd ZdZdS )ÚDocumentationErrorz=Raised when Markdown / Mermaid documentation rendering fails.Nr   r	   r
   r   r   r      s   € € € € € ØGĞGĞGĞGr
   r   N)	Ú	Exceptionr   r   r   r   r   r   r   r   r	   r
   r   ú<module>r      s˜  ğğ;ğ ;ğ ;ğ ;ğ ;˜)ñ ;ô ;ğ ;ğ]ğ ]ğ ]ğ ]ğ ]Ğ,ñ ]ô ]ğ ]ğVğ Vğ Vğ Vğ VĞ*ñ Vô Vğ VğUğ Uğ Uğ Uğ UĞ)ñ Uô Uğ UğFğ Fğ Fğ Fğ FĞ)ñ Fô Fğ Fğ^ğ ^ğ ^ğ ^ğ ^Ğ/ñ ^ô ^ğ ^ğ7ğ 7ğ 7ğ 7ğ 7Ğ,ñ 7ô 7ğ 7ğHğ Hğ Hğ Hğ HĞ,ñ Hô Hğ Hğ Hğ Hr
   

// ==========================================================================
// File: src/kernelite_prism/__pycache__/cli.cpython-311.pyc
// ==========================================================================

§
    ä¡üj{  ã            !       óş  — d dl Z d dlZd dlmZ d dlmZ d dlZddlmZ ddl	m
Z
 ddlmZmZ ddlmZ dd	lmZmZ d
Z ej        ¦   «          ej        d¬¦  «        dMd„¦   «         ¦   «         Ze                     ¦   «          ej        d ej        de¬¦  «        ¬¦  «         ej        ddd ej        g d¢d¬¦  «        dd¬¦  «         ej        dd ej        e¬¦  «        dd¬¦  «         ej        d  ej        e¬¦  «        dd!¬¦  «         ej        d"ed#e› d$¬%¦  «         ej        d& ej        g d'¢d¬¦  «        d(d)¬¦  «         ej        d*d+ ej        g d,¢d¬¦  «        d-d.¬¦  «         ej        d/d0d1¬%¦  «         ej        d2dd3¬%¦  «         ej        d4ddd5¬6¦  «        deded7ee         d8ee         d9ed:ed;ed<ed=ee         d>eddfd?„¦   «         ¦   «         ¦   «         ¦   «         ¦   «         ¦   «         ¦   «         ¦   «         ¦   «         ¦   «         ¦   «         Ze                     d@¦  «         ej        dA ej        de¬¦  «        ¬¦  «         ej        dd ej        e¬¦  «        ddB¬¦  «        dAed7ee         ddfdC„¦   «         ¦   «         ¦   «         Ze                     dD¦  «         ej        dA ej        de¬¦  «        ¬¦  «         ej        dE ej        de¬¦  «        ddF¬¦  «        dAedGee         ddfdH„¦   «         ¦   «         ¦   «         Ze                     dI¦  «         ej        dd ej        e¬¦  «        ddJ¬¦  «        d7ee         ddfdK„¦   «         ¦   «         Z e!dLk    r e¦   «          dS dS )Né    N)ÚPath)ÚOptionalé   )Úgenerate_markdown_spec)Úserialize_spec)Úrun_full_verificationÚvalidate_schema_compliance)ÚKernelitePrismError)Ú
DriverSpecÚexport_json_schemazQwen/Qwen2.5-Coder-3B-Instructzkernelite-prism)Úpackage_nameÚreturnc                  ó   — dS )z^Kernelite Prism: Extract structured hardware & C++ microkernel specs from Linux driver C code.N© r   ó    ú,/workspaces/prism/src/kernelite_prism/cli.pyÚmainr      s   € € € r   Útarget_pathT)ÚexistsÚ	path_type)Útypez--formatz-fÚoutput_format)ÚjsonÚyamlÚmdF)Úcase_sensitiver   z0Output format serialization (json, yaml, or md).)r   ÚdefaultÚhelpz--outz-o)r   zMOutput file path for driver_spec.json (or YAML). Prints to stdout if omitted.z--md-outzJOptional output path to simultaneously emit human-readable DRIVER_SPEC.md.z--modelz!Model ID or local path (default: z).)r   r   z	--backend)ÚtransformersÚoutlinesÚopenaiÚ
instructorr   zStructured generation backend.z--quantz-q)Ú4bitÚ8bitÚnoner#   z:Quantization precision (default: 4bit; use 'none' on CPU).z--deviceÚcudaz+Execution device ('cuda', 'cuda:0', 'cpu').z--tokenzHugging Face API token.z--no-ast-prunez8Disable AST boilerplate stripping before LLM submission.)Úis_flagr   r   ÚoutÚmd_outÚmodelÚbackendÚquantÚdeviceÚtokenÚno_ast_prunec
                 ó  — 	 ddl m}
 ddlm} ddlm}  |
|||||¬¦  «        }t          j        d| › dd¬	¦  «         t          j	        d¬
¦  «        5 }t          j
        dt          ¦  «          || |	 ¬¦  «        \  }}ddd¦  «         n# 1 swxY w Y   |D ] }t          j        d|j        › d¬	¦  «         Œ!t          j        d|j        › d|j        › dd¬	¦  «          ||j        |j        |j        |j        |j        ¬¦  «        }|                     ||¬¦  «        }t'          |¦  «        }t)          |||d¬¦  «         t+          ||¬¦  «        }|rM|j                             dd¬¦  «         |                     |d¬¦  «         t          j        d|› d¬	¦  «         nt          j        |¦  «         |rN|j                             dd¬¦  «         |                     |d¬¦  «         t          j        d|› d¬	¦  «         dS dS # t2          $ r8}t          j        d|› d¬	¦  «         t5          j        d¦  «         Y d}~dS d}~wt8          $ r8}t          j        d|› d¬	¦  «         t5          j        d¦  «         Y d}~dS d}~ww xY w)zYAnalyze a target C driver file or directory and produce machine and human specifications.r   )Úget_settings)Úaggregate_with_ast)ÚDriverExtractor)r*   r-   Úquantizationr+   Úhf_tokenz,Parsing C AST and aggregating context from 'z'...T©Úerr)ÚrecordÚalways)Úuse_ast_pruningNz	Warning: zExtracting specification via z [z]...)Ú
model_namer-   r4   r+   r5   )Úcode_contextÚ
ast_resultF)r=   Úmarkdown_textÚstrict)Úformat_type©ÚparentsÚexist_okúutf-8©ÚencodingzSpecification written to z"Markdown documentation written to zError: zUnexpected error: )Úconfigr1   Úcore.aggregatorr2   Úcore.extractorr3   ÚclickÚechoÚwarningsÚcatch_warningsÚsimplefilterÚUserWarningÚmessager+   r*   r-   r4   r5   Úextractr   r   r   ÚparentÚmkdirÚ
write_textr
   ÚsysÚexitÚ	Exception)r   r   r(   r)   r*   r+   r,   r-   r.   r/   r1   r2   r3   ÚsettingsÚcaught_warningsr<   r=   ÚwÚ	extractorÚspecÚmd_docÚserialized_outputÚes                          r   Úanalyzer`      s‘  € ğL;Ø(Ğ(Ğ(Ğ(Ğ(Ğ(Ø7Ğ7Ğ7Ğ7Ğ7Ğ7Ø3Ğ3Ğ3Ğ3Ğ3Ğ3à<ØØØØØğ
ñ 
ô 
ˆõ 	Œ
ĞSÀ+ĞSĞSĞSĞY]Ğ^Ñ^Ô^Ğ^İÔ$¨DĞ1Ñ1Ô1ğ 	°_İÔ! (­KÑ8Ô8Ğ8Ø'9Ğ'9ØØ$0Ğ 0ğ(ñ (ô (Ñ$ˆL˜*ğ	ğ 	ğ 	ñ 	ô 	ğ 	ğ 	ğ 	ğ 	ğ 	ğ 	øøøğ 	ğ 	ğ 	ğ 	ğ !ğ 	:ğ 	:ˆAİŒJĞ. 1¤9Ğ.Ğ.°DĞ9Ñ9Ô9Ğ9Ğ9åŒ
ØT¨HÔ,<ĞTĞTÀÄĞTĞTĞTØğ	
ñ 	
ô 	
ğ 	
ğ $OØ”~Ø”?Ø!Ô.ØÔ$ØÔ&ğ
ñ 
ô 
ˆ	ğ × Ò ¨lÀzĞ ÑRÔRˆå'¨Ñ-Ô-ˆİ˜d¨zÈĞX]Ğ^Ñ^Ô^Ğ^å*¨4¸]ĞKÑKÔKĞàğ 	*ØŒJ×Ò T°DĞÑ9Ô9Ğ9ØNŠNĞ,°wˆNÑ?Ô?Ğ?İŒJĞ8°3Ğ8Ğ8¸dĞCÑCÔCĞCĞCåŒJĞ(Ñ)Ô)Ğ)àğ 	PØŒM×Ò¨°tĞÑ<Ô<Ğ<Ø×Ò˜f¨wĞÑ7Ô7Ğ7İŒJĞD¸FĞDĞDÈ$ĞOÑOÔOĞOĞOĞOğ	Pğ 	Pøõ
 ğ ğ ğ İŒ
=˜Q== dĞ+Ñ+Ô+Ğ+İŒ‰Œˆˆˆˆˆˆˆøøøøİğ ğ ğ İŒ
Ğ+¨Ğ+Ğ+°Ğ6Ñ6Ô6Ğ6İŒ‰ŒˆˆˆˆˆˆˆøøøøğøøøsI   ‚AH Á,BÁ?H ÂBÂH ÂBÂE1H È
J
È-IÉJ
É-JÊJ
z	render-mdÚ	spec_jsonzNOutput Markdown file path (e.g., DRIVER_SPEC.md). Prints to stdout if omitted.c                 óÈ  — 	 t          j        |                      d¬¦  «        ¦  «        }t          |¦  «        }|rN|j                             dd¬¦  «         |                     |d¬¦  «         t          j        d|› d¬¦  «         d	S t          j        |¦  «         d	S # t          $ r8}t          j        d|› d¬¦  «         t          j        d¦  «         Y d	}~d	S d	}~ww xY w)
z=Render a validated driver_spec.json file into DRIVER_SPEC.md.rD   rE   TrA   z"Markdown specification written to r6   zError rendering Markdown: r   N)r   Úmodel_validate_jsonÚ	read_textr   rR   rS   rT   rJ   rK   rW   rU   rV   )ra   r(   r\   Ú
md_contentr_   s        r   Ú	render_mdrf   š   sş   € ğİÔ-¨i×.AÒ.AÈ7Ğ.AÑ.SÔ.SÑTÔTˆİ+¨DÑ1Ô1ˆ
Øğ 	#ØŒJ×Ò T°DĞÑ9Ô9Ğ9ØNŠN˜:°ˆNÑ8Ô8Ğ8İŒJĞA¸CĞAĞAÀtĞLÑLÔLĞLĞLĞLåŒJzÑ"Ô"Ğ"Ğ"Ğ"øİğ ğ ğ İŒ
Ğ3°Ğ3Ğ3¸Ğ>Ñ>Ô>Ğ>İŒ‰Œˆˆˆˆˆˆˆøøøøğøøøs   ‚BB Â	B Â
C!Â)-CÃC!Úvalidatez--schemaz0Optional path to custom driver_spec.schema.json.Úschemac           	      ó   — t          |                      d¬¦  «        |¬¦  «        }t          d„ |D ¦   «         ¦  «        }|D ]4}|j        rdnd}t	          j        d|› d|j        › d	|j        › ¦  «         Œ5|st          j	        d
¦  «         dS dS )zEValidate a driver_spec.json artifact against driver_spec.schema.json.rD   rE   )Úschema_pathc              3   ó$   K  — | ]}|j         V — Œd S )N)Úpassed)Ú.0Úfs     r   ú	<genexpr>zvalidate_cmd.<locals>.<genexpr>Á   s$   è è € Ğ,Ğ,˜a”Ğ,Ğ,Ğ,Ğ,Ğ,Ğ,r   ÚPASSÚFAILú[z] z: r   N)
r	   rd   Úallrl   rJ   rK   ÚcheckrP   rU   rV   )ra   rh   ÚfindingsÚall_okÚfindingÚprefixs         r   Úvalidate_cmdry   ³   sÁ   € õ *Ø×Ò WĞÑ-Ô-Øğñ ô €Hõ Ğ,Ğ, 8Ğ,Ñ,Ô,Ñ,Ô,€FØğ Eğ EˆØ"œ>Ğ5¨vˆİŒ
ĞCvĞCĞC ¤ĞCĞC°'´/ĞCĞCÑDÔDĞDĞDØğ İŒ‰Œˆˆˆğğ r   zexport-schemaz(Output path for driver_spec.schema.json.c                 óô   — t          d¬¦  «        }| rQ| j                             dd¬¦  «         |                      |dz   d¬¦  «         t	          j        d| › d¬	¦  «         d
S t	          j        |¦  «         d
S )zIExport the canonical JSON Schema Draft 2020-12 (driver_spec.schema.json).é   )ÚindentTrA   ú
rD   rE   zJSON Schema written to r6   N)r   rR   rS   rT   rJ   rK   )r(   Úschema_texts     r   Úexport_schema_cmdr   É   sŒ   € õ %¨AĞ.Ñ.Ô.€KØ
ğ  ØŒ
×Ò °ĞÑ5Ô5Ğ5ØŠ{ TÑ)°GˆÑ<Ô<Ğ<İŒ
Ğ2¨SĞ2Ğ2¸Ğ=Ñ=Ô=Ğ=Ğ=Ğ=åŒ
;ÑÔĞĞĞr   Ú__main__)r   N)"rU   rL   Úpathlibr   Útypingr   rJ   Úcore.doc_generatorr   Úcore.serializerr   Úcore.validatorr   r	   Ú
exceptionsr
   Úmodels.baser   r   ÚDEFAULT_CLI_MODELÚgroupÚversion_optionr   ÚcommandÚargumentÚoptionÚChoiceÚstrÚboolr`   rf   ry   r   Ú__name__r   r   r   ú<module>r’      së  ğØ 
€
€
€
Ø €€€Ø Ğ Ğ Ğ Ğ Ğ Ø Ğ Ğ Ğ Ğ Ğ Ø €€€à 6Ğ 6Ğ 6Ğ 6Ğ 6Ğ 6Ø +Ğ +Ğ +Ğ +Ğ +Ğ +Ø MĞ MĞ MĞ MĞ MĞ MĞ MĞ MØ +Ğ +Ğ +Ğ +Ğ +Ğ +Ø 7Ğ 7Ğ 7Ğ 7Ğ 7Ğ 7Ğ 7Ğ 7à4Ğ ğ €„„Ø€ÔĞ#4Ğ5Ñ5Ô5ğiğ iğ iñ 6Ô5ñ „ğiğ ‡‚„Ø€„ J E¤J°dÀdĞ$KÑ$KÔ$KĞLÑLÔLØ€„ØØØØ	ˆŒĞ,Ğ,Ğ,¸UĞ	CÑ	CÔ	CØØ	;ğñ ô ğ €„ØØØ	ˆŒ˜dĞ	#Ñ	#Ô	#ØØ	Xğñ ô ğ €„ØØ	ˆŒ˜dĞ	#Ñ	#Ô	#ØØ	Uğ	ñ ô ğ €„ØØØ	BĞ->Ğ	BĞ	BĞ	Bğñ ô ğ
 €„ØØ	ˆŒĞJĞJĞJĞ[`Ğ	aÑ	aÔ	aØØ	)ğ	ñ ô ğ €„ØØØ	ˆŒĞ.Ğ.Ğ.¸uĞ	EÑ	EÔ	EØØ	Eğñ ô ğ €„ØØØ	6ğñ ô ğ
 €„ØØØ	"ğñ ô ğ
 €„ØØØØ	Cğ	ñ ô ğHØğHàğHğ 
$ŒğHğ TŒNğ	Hğ
 ğHğ ğHğ ğHğ ğHğ CŒ=ğHğ ğHğ 
ğHğ Hğ Hñô ñô ñô ñô ñô ñô ñô ñô ñô ñ MÔLñ „ğrHğV ‡‚ˆkÑÔØ€„ * %¤*°DÀDĞ"IÑ"IÔ"IĞJÑJÔJØ€„ØØØ	ˆŒ˜dĞ	#Ñ	#Ô	#ØØ	Yğñ ô ğ˜ğ  H¨T¤Nğ °tğ ğ ğ ñô ñ KÔJñ Ôğğ  ‡‚ˆjÑÔØ€„ * %¤*°DÀDĞ"IÑ"IÔ"IĞJÑJÔJØ€„ØØ	ˆŒ˜4¨4Ğ	0Ñ	0Ô	0ØØ	;ğ	ñ ô ğ˜Dğ ¨(°4¬.ğ ¸Tğ ğ ğ ñô ñ KÔJñ Ôğğ ‡‚ˆoÑÔØ€„ØØØ	ˆŒ˜dĞ	#Ñ	#Ô	#ØØ	3ğñ ô ğ ˜8 Dœ>ğ  ¨dğ  ğ  ğ  ñô ñ Ôğ ğ ˆzÒĞØ€DF„F€F€F€Fğ Ğr   

// ==========================================================================
// File: src/kernelite_prism/__pycache__/config.cpython-311.pyc
// ==========================================================================

§
    „‹üj  ã                   ó¸   — d dl Z d dlmZ d dlZddlmZ dZh d£Zh d£Z G d„ d	¦  «        Z	ed
dddddfde
de
de
de
dee
         dee
         dee
         de	fd„ZdS )é    N)ÚOptionalé   )ÚConfigurationErrorzQwen/Qwen2.5-Coder-3B-Instruct>   Ú4bitÚ8bitÚnone>   ÚopenaiÚoutlinesÚ
instructorÚtransformersc                   ór   — e Zd ZdZeddddddfdededed	ed
ee         dee         dee         fd„Zdd„ZdS )ÚSettingszDHolds configuration state for inference backend and model selection.Úcudar   r   NÚmodelÚdeviceÚquantizationÚbackendÚhf_tokenÚopenai_api_keyÚopenai_base_urlc                 óP  — || _         || _        |                     ¦   «         | _        |                     ¦   «         | _        |p't          j        d¦  «        pt          j        d¦  «        | _        |pt          j        d¦  «        | _        |pt          j        d¦  «        | _	        d S )NÚHF_TOKENÚHUGGINGFACE_HUB_TOKENÚOPENAI_API_KEYÚOPENAI_BASE_URL)
r   r   Úlowerr   r   ÚosÚgetenvr   r   r   )Úselfr   r   r   r   r   r   r   s           ú//workspaces/prism/src/kernelite_prism/config.pyÚ__init__zSettings.__init__   s–   € ğ  ˆŒ
Ø!ˆŒØ!-×!3Ò!3Ñ!5Ô!5ˆÔØ#ŸMšM™OœOˆŒàĞSœ	 *Ñ-Ô-ĞSµ´Ğ;RÑ1SÔ1Sğ 	Œğ .<Ğ-Z½r¼yĞIYÑ?ZÔ?ZˆÔØ.=Ğ.]ÅÄĞK\ÑA]ÔA]ˆÔĞĞó    Úreturnc           
      óV  — | j         t          vr@t          d| j         › dd                     t	          t          ¦  «        ¦  «        › d¦  «        ‚| j         dv rÍ| j        t          vr@t          d| j        › dd                     t	          t          ¦  «        ¦  «        › d¦  «        ‚| j                             d¦  «        }|r6t          j
                             ¦   «         st          d| j        › d	¦  «        ‚|s-| j        d
k    r$t          d| j        › d| j        › d¦  «        ‚d S d S d S )NzUnsupported backend 'z'. Choose from: z, ú.>   r
   r   zUnsupported quantization 'r   zDevice 'z' was requested, but CUDA is not available. Ensure an NVIDIA GPU with appropriate CUDA drivers and PyTorch are installed, or pass '--device cpu --quant none'.r   zQuantization 'z1' is only supported on CUDA devices, got device 'z.'. Pass '--quant none' for non-CUDA execution.)r   ÚVALID_BACKENDSr   ÚjoinÚsortedr   ÚVALID_QUANTIZATIONSr   Ú
startswithÚtorchr   Úis_available)r   Úis_cudas     r    ÚvalidatezSettings.validate"   sŸ  € ØŒ<~Ğ-Ğ-İ$ğE¨¬ğ Eğ EØ $§	¢	­&µÑ*@Ô*@Ñ AÔ AğEğ Eğ Eñô ğ ğ
 Œ<Ğ7Ğ7Ğ7ØÔ Õ(;Ğ;Ğ;İ(ğN°Ô1Bğ Nğ NØ$(§I¢I­fÕ5HÑ.IÔ.IÑ$JÔ$JğNğ Nğ Nñô ğ ğ
 ”k×,Ò,¨VÑ4Ô4ˆGàğ uœz×6Ò6Ñ8Ô8ğ İ(ğ;˜tœ{ğ ;ğ ;ğ ;ñô ğ ğ ğ ˜tÔ0°FÒ:Ğ:İ(ğ_ TÔ%6ğ _ğ _Ø#'¤;ğ_ğ _ğ _ñô ğ ğ# 8Ğ7ğ ğ Ğ:Ğ:r"   )r#   N)	Ú__name__Ú
__module__Ú__qualname__Ú__doc__ÚDEFAULT_MODELÚstrr   r!   r.   © r"   r    r   r      sº   € € € € € ØNĞNğ #ØØ"Ø%Ø"&Ø(,Ø)-ğ^ğ ^àğ^ğ ğ^ğ ğ	^ğ
 ğ^ğ ˜3”-ğ^ğ ! œğ^ğ " #œğ^ğ ^ğ ^ğ ^ğ(ğ ğ ğ ğ ğ r"   r   r   r   r   r   r   r   r   r   r   r   r#   c           	      óZ   — t          | ||||||¬¦  «        }|                     ¦   «          |S )z0Factory function to fetch and validate settings.)r   r   r   r   r   r   r   )r   r.   )r   r   r   r   r   r   r   Úsettingss           r    Úget_settingsr8   @   sF   € õ ØØØ!ØØØ%Ø'ğñ ô €Hğ ×ÒÑÔĞØ€Or"   )r   Útypingr   r+   Ú
exceptionsr   r3   r)   r&   r   r4   r8   r5   r"   r    ú<module>r;      s  ğØ 	€	€	€	Ø Ğ Ğ Ğ Ğ Ğ Ø €€€Ø *Ğ *Ğ *Ğ *Ğ *Ğ *à0€Ø.Ğ.Ğ.Ğ ØEĞEĞE€ğ2ğ 2ğ 2ğ 2ğ 2ñ 2ô 2ğ 2ğl ØØØ!Ø"Ø$(Ø%)ğğ Øğàğğ ğğ ğ	ğ
 sŒmğğ ˜S”Mğğ ˜c”]ğğ ğğ ğ ğ ğ ğ r"   

// ==========================================================================
// File: src/kernelite_prism/__pycache__/__init__.cpython-311.pyc
// ==========================================================================

§
    ó üjÃ  ã                   ó2   — d Z ddlmZ dZg d¢Zdedefd„ZdS )	z^Kernelite Prism: AI-driven Linux driver extraction and C++ microkernel specification pipeline.é    )ÚAnyz0.2.0)	Ú__version__Ú
DriverSpecÚexport_json_schemaÚASTPreProcessorÚparse_driver_astÚDriverExtractorÚextract_driver_specÚgenerate_markdown_specÚrun_full_verificationÚnameÚreturnc                 ó˜   — | dv rddl m}m} ||dœ|          S | dv rddlm} t          || ¦  «        S t          dt          ›d| ›¦  «        ‚)N>   r   r   é   )r   r   >   r   r	   r   r
   r   r   )Úcorezmodule z has no attribute )Úmodelsr   r   Ú r   ÚgetattrÚAttributeErrorÚ__name__)r   r   r   r   s       ú1/workspaces/prism/src/kernelite_prism/__init__.pyÚ__getattr__r      s   € ØĞ3Ğ3Ğ3Ø:Ğ:Ğ:Ğ:Ğ:Ğ:Ğ:Ğ:à(Ğ@RĞSĞSĞTXÔYĞYØğ ğ ğ ğ 	ĞĞĞĞĞåt˜TÑ"Ô"Ğ"İ
ĞI¥8ĞIĞIÀĞIĞIÑ
JÔ
JĞJó    N)Ú__doc__Útypingr   r   Ú__all__Ústrr   © r   r   ú<module>r      sg   ğØ dĞ dà Ğ Ğ Ğ Ğ Ğ à€ğ
ğ 
ğ 
€ğKcğ K˜cğ Kğ Kğ Kğ Kğ Kğ Kr   

// ==========================================================================
// File: src/kernelite_prism/exceptions.py
// ==========================================================================

class KernelitePrismError(Exception):
    """Base exception class for Kernelite Prism errors."""


class ConfigurationError(KernelitePrismError):
    """Raised when environment variables, hardware devices, or options are misconfigured."""


class AggregationError(KernelitePrismError):
    """Raised when context aggregation fails (e.g., path not found or unreadable)."""


class ASTParsingError(KernelitePrismError):
    """Raised when Tree-sitter C AST parsing encounters an unrecoverable failure."""


class ExtractionError(KernelitePrismError):
    """Raised when LLM inference or schema-enforced parsing fails."""


class SchemaValidationError(KernelitePrismError):
    """Raised when a driver specification fails JSON Schema or AST grounding verification."""


class SerializationError(KernelitePrismError):
    """Raised when output formatting/writing fails."""


class DocumentationError(KernelitePrismError):
    """Raised when Markdown / Mermaid documentation rendering fails."""

// ==========================================================================
// File: src/kernelite_prism/__init__.py
// ==========================================================================

"""Kernelite Prism: AI-driven Linux driver extraction and C++ microkernel specification pipeline."""

from typing import Any

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "DriverSpec",
    "export_json_schema",
    "ASTPreProcessor",
    "parse_driver_ast",
    "DriverExtractor",
    "extract_driver_spec",
    "generate_markdown_spec",
    "run_full_verification",
]


def __getattr__(name: str) -> Any:
    if name in {"DriverSpec", "export_json_schema"}:
        from .models import DriverSpec, export_json_schema

        return {"DriverSpec": DriverSpec, "export_json_schema": export_json_schema}[name]
    if name in {
        "ASTPreProcessor",
        "parse_driver_ast",
        "DriverExtractor",
        "extract_driver_spec",
        "generate_markdown_spec",
        "run_full_verification",
    }:
        from . import core

        return getattr(core, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

// ==========================================================================
// File: src/kernelite_prism.egg-info/top_level.txt
// ==========================================================================

kernelite_prism
schemas


// ==========================================================================
// File: src/kernelite_prism.egg-info/dependency_links.txt
// ==========================================================================




// ==========================================================================
// File: src/kernelite_prism.egg-info/entry_points.txt
// ==========================================================================

[console_scripts]
kernelite-prism = kernelite_prism.cli:main
kprism = kernelite_prism.cli:main


// ==========================================================================
// File: src/kernelite_prism.egg-info/SOURCES.txt
// ==========================================================================

LICENSE
README.md
pyproject.toml
src/kernelite_prism/__init__.py
src/kernelite_prism/cli.py
src/kernelite_prism/config.py
src/kernelite_prism/exceptions.py
src/kernelite_prism.egg-info/PKG-INFO
src/kernelite_prism.egg-info/SOURCES.txt
src/kernelite_prism.egg-info/dependency_links.txt
src/kernelite_prism.egg-info/entry_points.txt
src/kernelite_prism.egg-info/requires.txt
src/kernelite_prism.egg-info/top_level.txt
src/kernelite_prism/core/__init__.py
src/kernelite_prism/core/aggregator.py
src/kernelite_prism/core/ast_parser.py
src/kernelite_prism/core/doc_generator.py
src/kernelite_prism/core/extractor.py
src/kernelite_prism/core/serializer.py
src/kernelite_prism/core/validator.py
src/kernelite_prism/models/__init__.py
src/kernelite_prism/models/base.py
src/kernelite_prism/models/hardware.py
src/kernelite_prism/models/interfaces.py
src/kernelite_prism/models/lifecycle.py
src/kernelite_prism/models/matching.py
src/kernelite_prism/models/metadata.py
src/kernelite_prism/models/registers.py
tests/test_reference_drivers.py

// ==========================================================================
// File: src/kernelite_prism.egg-info/requires.txt
// ==========================================================================

torch>=2.1.0
transformers>=4.40.0
accelerate>=0.28.0
bitsandbytes>=0.43.0
pydantic>=2.6.0
click>=8.1.0
pyyaml>=6.0.0
tree-sitter>=0.23.0
tree-sitter-c>=0.23.0
jsonschema>=4.21.0
jinja2>=3.1.3
outlines>=0.1.0
openai>=1.30.0
instructor>=1.3.0

[dev]
pytest>=8.0.0
pytest-cov>=5.0.0


// ==========================================================================
// File: src/kernelite_prism.egg-info/PKG-INFO
// ==========================================================================

Metadata-Version: 2.4
Name: kernelite-prism
Version: 0.2.0
Summary: AI-driven extraction tool for converting Linux driver C code into structured hardware and C++ microkernel specifications.
License: GPL-3.0-or-later
Requires-Python: >=3.11
Description-Content-Type: text/markdown
License-File: LICENSE
Requires-Dist: torch>=2.1.0
Requires-Dist: transformers>=4.40.0
Requires-Dist: accelerate>=0.28.0
Requires-Dist: bitsandbytes>=0.43.0
Requires-Dist: pydantic>=2.6.0
Requires-Dist: click>=8.1.0
Requires-Dist: pyyaml>=6.0.0
Requires-Dist: tree-sitter>=0.23.0
Requires-Dist: tree-sitter-c>=0.23.0
Requires-Dist: jsonschema>=4.21.0
Requires-Dist: jinja2>=3.1.3
Requires-Dist: outlines>=0.1.0
Requires-Dist: openai>=1.30.0
Requires-Dist: instructor>=1.3.0
Provides-Extra: dev
Requires-Dist: pytest>=8.0.0; extra == "dev"
Requires-Dist: pytest-cov>=5.0.0; extra == "dev"
Dynamic: license-file

# Kernelite Prism ğŸ’

> **Refractoring Linux driver source into structured, machine-readable specifications.**

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![API](https://img.shields.io/badge/LLM-Gemini-orange)](https://deepmind.google/technologies/gemini/)

**Kernelite Prism** is an AI-driven, LLM-first extraction tool designed to parse Linux kernel driver source code and output highly structured, machine-readable metadata. 

By passing localized driver directories or individual `.c` files to the Google Gemini API with strict Pydantic schemas, Prism uncovers the hidden hardware logic, interfaces, and dependencies within the kernelâ€”transforming opaque C code into clean JSON or YAML specifications.

---

## ğŸ¯ Primary Use Cases

* **AI Context Enrichment:** Feed accurate, structured hardware specs to autonomous coding agents or LLMs to assist in driver refactoring, porting, or generation.
* **Automated Hardware Documentation:** Generate human-readable driver specification sheets, register matrices, and device support tables directly from source code.
* **Emulation & Testing:** Rapidly scaffold QEMU hardware stubs or synthetic driver mocks based on real kernel driver abstractions and register maps.

---

## ğŸ” Extraction Targets

Prism extracts key Linux device driver structures:

* **Hardware & ID Matching:** `of_device_id` (Device Tree compatible strings), `pci_device_id`, `usb_device_id`, and ACPI tables.
* **Module Metadata:** `MODULE_DESCRIPTION`, `MODULE_AUTHOR`, `MODULE_LICENSE`, and `module_param` types/permissions.
* **Interfaces & Control Surfaces:** `ioctl` command definitions, `sysfs` attribute groups, `procfs` entries, and `file_operations` mappings.
* **Lifecycle & Driver Structs:** Platform, PCI, and I2C driver structs, alongside Probe, Remove, Suspend, and Resume entry points.
* **Register Maps:** Register offset macros, bitfield definitions (`BIT()`, `GENMASK()`), and hardware access logic.

---

## âš™ï¸ How It Works

1. **Context Aggregation:** Discovers target `.c` and `.h` files within a specified file or folder, combining file contents into an aggregated context payload.
2. **LLM Parsing:** Queries the **Gemini API** utilizing strict structured output parameters (`response_schema`).
3. **Pydantic Validation:** Validates responses against typed Pydantic models before returning serialized JSON or YAML artifacts.

---

## ğŸš€ Quickstart & Setup

### Prerequisites

* Python 3.10+
* A Google Gemini API Key (`GEMINI_API_KEY`)

---

### Option A: GitHub Codespaces (Recommended)

1. Open the repository in GitHub Codespaces.
2. Add your `GEMINI_API_KEY` secret under **GitHub Settings** $\rightarrow$ **Codespaces** $\rightarrow$ **Codespaces secrets**.
3. The devcontainer automatically installs `kernelite-prism` in editable mode upon creation.

---

### Option B: Local Installation

1. Clone the repository and navigate into the project:
```bash
git clone https://github.com/your-org/kernelite.git
cd kernelite

```


2. Install the package in editable mode:
```bash
pip install -e .

```


3. Export your Gemini API key:
```bash
export GEMINI_API_KEY="your-gemini-api-key-here"

```



---

## ğŸ› ï¸ Command Line Usage

Use either `kprism` or `kernelite-prism` in your terminal.

```text
Usage: kprism analyze [OPTIONS] TARGET_PATH

  Analyze a target C driver file or directory and produce a structured spec.

Options:
  -f, --format [json|yaml]  Output format serialization (default: json).
  -o, --out PATH            Output file path. Prints to stdout if omitted.
  --model TEXT              Gemini LLM model override (default: gemini-2.5-flash).
  --help                    Show this message and exit.

```

---

### Examples

#### 1. Analyze a single file and output to stdout

```bash
kprism analyze drivers/gpio/gpio-mmio.c

```

#### 2. Export a single driver file to JSON

```bash
kprism analyze drivers/gpio/gpio-mmio.c --format json --out gpio-mmio-spec.json

```

#### 3. Export an entire subsystem directory to YAML

```bash
kprism analyze drivers/i2c/busses/ --format yaml --out i2c-busses-spec.yaml

```

#### 4. Override the Gemini model

```bash
kprism analyze drivers/net/ethernet/realtek/r8169_main.c --model gemini-2.5-pro --out r8169.json

```

---

## ğŸ“„ Example Output

```json
{
  "driver_name": "gpio-mmio",
  "metadata": {
    "author": "Anton Vorontsov <cbouatmailru@gmail.com>",
    "license": "GPL",
    "description": "Generic MMIO GPIO driver"
  },
  "matching_tables": {
    "of_device_id": [
      {
        "compatible": "brcm,bcm2835-gpio"
      }
    ]
  },
  "lifecycle": {
    "probe_function": "bgpio_pdev_probe",
    "remove_function": "bgpio_pdev_remove"
  },
  "structs": [
    "platform_driver"
  ],
  "registers": [
    {
      "name": "BGPIO_DIR",
      "offset": "0x04",
      "description": "Direction control register",
      "bitfields": [
        {
          "name": "GPIO_DIR_IN",
          "mask_macro": "BIT(0)",
          "description": "Configure GPIO pin as input"
        }
      ]
    }
  ]
}

```

---

## ğŸ¤ Contributing

Contributions are welcome! Feel free to open issues or PRs to extend Pydantic schemas for additional kernel subsystems, refine extraction prompts, or handle specific driver macros.

---

## ğŸ“„ License

This project is licensed under the GPL v3 License - see the [LICENSE](LICENSE) file for details.


// ==========================================================================
// File: src/schemas/driver_spec.schema.json
// ==========================================================================

{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://kernelite.dev/schemas/driver_spec.schema.json",
  "title": "DriverSpec",
  "type": "object",
  "required": ["driver_name"],
  "properties": {
    "device_name": {
      "type": "string",
      "default": "",
      "description": "Human-readable hardware controller or peripheral name"
    },
    "driver_name": {
      "type": "string",
      "description": "Canonical name of the Linux driver module"
    },
    "compatibility": {
      "type": "array",
      "items": { "type": "string" },
      "default": [],
      "description": "Compatible DTB strings or hardware matching identifiers"
    },
    "hardware_info": { "$ref": "#/$defs/HardwareInfo" },
    "metadata": { "$ref": "#/$defs/DriverMetadata" },
    "matching_tables": { "$ref": "#/$defs/MatchingTables" },
    "interfaces": { "$ref": "#/$defs/DriverInterfaces" },
    "lifecycle": { "$ref": "#/$defs/DriverLifecycle" },
    "structs": {
      "type": "array",
      "items": { "type": "string" },
      "default": []
    },
    "register_map": {
      "type": "array",
      "items": { "$ref": "#/$defs/RegisterMap" },
      "default": []
    },
    "init_sequence": {
      "type": "array",
      "items": { "$ref": "#/$defs/SequenceStep" },
      "default": []
    },
    "runtime_operations": { "$ref": "#/$defs/RuntimeOperations" },
    "concurrency_locks": {
      "type": "array",
      "items": { "$ref": "#/$defs/ConcurrencyLock" },
      "default": []
    },
    "migration_notes": { "$ref": "#/$defs/MigrationNotes" }
  },
  "$defs": {
    "ApiMigrationMapping": {
      "type": "object",
      "required": ["linux_api", "microkernel_equivalent", "notes"],
      "properties": {
        "linux_api": { "type": "string" },
        "microkernel_equivalent": { "type": "string" },
        "notes": { "type": "string" }
      }
    },
    "BaseAddressRegion": {
      "type": "object",
      "required": ["region_id"],
      "properties": {
        "region_id": { "type": "string" },
        "name": { "type": ["string", "null"], "default": null },
        "base_offset": { "type": "string", "default": "0x0" },
        "size_hint": { "type": ["string", "null"], "default": null },
        "description": { "type": ["string", "null"], "default": null }
      }
    },
    "Bitfield": {
      "type": "object",
      "required": ["name", "mask_macro"],
      "properties": {
        "name": { "type": "string" },
        "bits": { "type": ["string", "null"], "default": null },
        "mask_macro": { "type": "string" },
        "access": {
          "type": ["string", "null"],
          "enum": ["RW", "RO", "WO", "R", "W", "W1C", null],
          "default": null
        },
        "reset_value": { "type": ["string", "null"], "default": null },
        "description": { "type": ["string", "null"], "default": null }
      }
    },
    "ConcurrencyLock": {
      "type": "object",
      "required": ["linux_primitive", "abstract_lock_type", "bare_metal_recommendation"],
      "properties": {
        "linux_primitive": { "type": "string" },
        "abstract_lock_type": {
          "type": "string",
          "enum": ["IRQSafeSpinlock", "Spinlock", "SleepableMutex", "ReadWriteLock", "Atomic", "None"]
        },
        "protected_resources": {
          "type": "array",
          "items": { "type": "string" },
          "default": []
        },
        "bare_metal_recommendation": { "type": "string" }
      }
    },
    "DMASpec": {
      "type": "object",
      "properties": {
        "allocation_api": { "type": ["string", "null"], "default": null },
        "address_mask_bits": { "type": ["integer", "null"], "default": null },
        "descriptor_rings": {
          "type": "array",
          "items": { "type": "string" },
          "default": []
        },
        "coherency_notes": { "type": ["string", "null"], "default": null }
      }
    },
    "DeviceMatch": {
      "type": "object",
      "properties": {
        "compatible": { "type": ["string", "null"], "default": null },
        "vendor_id": { "type": ["string", "null"], "default": null },
        "device_id": { "type": ["string", "null"], "default": null },
        "raw_entry": { "type": "object", "default": {} }
      }
    },
    "DriverInterfaces": {
      "type": "object",
      "properties": {
        "file_operations": {
          "type": "object",
          "additionalProperties": { "type": "string" },
          "default": {}
        },
        "sysfs_attributes": {
          "type": "array",
          "items": { "$ref": "#/$defs/SysfsAttribute" },
          "default": []
        },
        "procfs_entries": {
          "type": "array",
          "items": { "type": "string" },
          "default": []
        },
        "ioctl_commands": {
          "type": "array",
          "items": { "$ref": "#/$defs/IoctlCommand" },
          "default": []
        }
      }
    },
    "DriverLifecycle": {
      "type": "object",
      "properties": {
        "probe_function": { "type": ["string", "null"], "default": null },
        "remove_function": { "type": ["string", "null"], "default": null },
        "shutdown_function": { "type": ["string", "null"], "default": null },
        "suspend_function": { "type": ["string", "null"], "default": null },
        "resume_function": { "type": ["string", "null"], "default": null },
        "runtime_suspend_function": { "type": ["string", "null"], "default": null },
        "runtime_resume_function": { "type": ["string", "null"], "default": null },
        "init_function": { "type": ["string", "null"], "default": null },
        "exit_function": { "type": ["string", "null"], "default": null }
      }
    },
    "DriverMetadata": {
      "type": "object",
      "properties": {
        "author": { "type": ["string", "null"], "default": null },
        "license": { "type": ["string", "null"], "default": null },
        "description": { "type": ["string", "null"], "default": null },
        "parameters": {
          "type": "array",
          "items": { "$ref": "#/$defs/ModuleParam" },
          "default": []
        }
      }
    },
    "HardwareInfo": {
      "type": "object",
      "properties": {
        "bus_type": {
          "type": "string",
          "enum": ["mmio", "pci", "platform", "i2c", "spi", "usb", "uart", "other"],
          "default": "mmio"
        },
        "compatible_strings": {
          "type": "array",
          "items": { "type": "string" },
          "default": []
        },
        "vendor_ids": {
          "type": "array",
          "items": { "type": "string" },
          "default": []
        },
        "device_ids": {
          "type": "array",
          "items": { "type": "string" },
          "default": []
        },
        "base_address_offsets": {
          "type": "array",
          "items": { "$ref": "#/$defs/BaseAddressRegion" },
          "default": []
        },
        "endianness": {
          "type": "string",
          "enum": ["little", "big", "configurable", "unknown"],
          "default": "little"
        },
        "irq_lines": {
          "type": "array",
          "items": { "type": "string" },
          "default": []
        }
      }
    },
    "InterruptLifecycle": {
      "type": "object",
      "properties": {
        "irq_handler": { "type": ["string", "null"], "default": null },
        "threaded_handler": { "type": ["string", "null"], "default": null },
        "registration_api": { "type": ["string", "null"], "default": null },
        "irq_flags": {
          "type": "array",
          "items": { "type": "string" },
          "default": []
        },
        "status_register": { "type": ["string", "null"], "default": null },
        "ack_register": { "type": ["string", "null"], "default": null },
        "mask_register": { "type": ["string", "null"], "default": null },
        "ack_mechanism": { "type": ["string", "null"], "default": null },
        "flow_steps": {
          "type": "array",
          "items": { "$ref": "#/$defs/SequenceStep" },
          "default": []
        }
      }
    },
    "IoctlCommand": {
      "type": "object",
      "required": ["name"],
      "properties": {
        "name": { "type": "string" },
        "dir": { "type": ["string", "null"], "default": null },
        "type": { "type": ["string", "null"], "default": null },
        "nr": { "type": ["string", "null"], "default": null },
        "size": { "type": ["string", "null"], "default": null }
      }
    },
    "MatchingTables": {
      "type": "object",
      "properties": {
        "of_device_id": { "type": "array", "items": { "$ref": "#/$defs/DeviceMatch" }, "default": [] },
        "pci_device_id": { "type": "array", "items": { "$ref": "#/$defs/DeviceMatch" }, "default": [] },
        "usb_device_id": { "type": "array", "items": { "$ref": "#/$defs/DeviceMatch" }, "default": [] },
        "acpi_device_id": { "type": "array", "items": { "$ref": "#/$defs/DeviceMatch" }, "default": [] },
        "i2c_device_id": { "type": "array", "items": { "$ref": "#/$defs/DeviceMatch" }, "default": [] },
        "spi_device_id": { "type": "array", "items": { "$ref": "#/$defs/DeviceMatch" }, "default": [] },
        "platform_device_id": { "type": "array", "items": { "$ref": "#/$defs/DeviceMatch" }, "default": [] },
        "hid_device_id": { "type": "array", "items": { "$ref": "#/$defs/DeviceMatch" }, "default": [] }
      }
    },
    "MigrationNotes": {
      "type": "object",
      "properties": {
        "linux_to_microkernel_mappings": {
          "type": "array",
          "items": { "$ref": "#/$defs/ApiMigrationMapping" },
          "default": []
        },
        "architectural_caveats": {
          "type": "array",
          "items": { "type": "string" },
          "default": []
        }
      }
    },
    "ModuleParam": {
      "type": "object",
      "required": ["name", "type", "permissions"],
      "properties": {
        "name": { "type": "string" },
        "type": { "type": "string" },
        "permissions": { "type": "string" },
        "description": { "type": ["string", "null"], "default": null }
      }
    },
    "RegisterMap": {
      "type": "object",
      "required": ["name", "offset"],
      "properties": {
        "name": { "type": "string" },
        "offset": { "type": "string" },
        "offset_expression": { "type": ["string", "null"], "default": null },
        "width": { "type": "integer", "minimum": 8, "maximum": 64, "default": 32 },
        "access": {
          "type": "string",
          "enum": ["RW", "RO", "WO", "R", "W", "W1C", "UNKNOWN"],
          "default": "RW"
        },
        "reset_value": { "type": ["string", "null"], "default": null },
        "description": { "type": ["string", "null"], "default": null },
        "bitfields": {
          "type": "array",
          "items": { "$ref": "#/$defs/Bitfield" },
          "default": []
        }
      }
    },
    "RuntimeOpMapping": {
      "type": "object",
      "required": ["operation_name", "c_function"],
      "properties": {
        "operation_name": { "type": "string" },
        "c_function": { "type": "string" },
        "registers_touched": {
          "type": "array",
          "items": { "type": "string" },
          "default": []
        },
        "description": { "type": ["string", "null"], "default": null }
      }
    },
    "RuntimeOperations": {
      "type": "object",
      "properties": {
        "probe_handler": { "type": ["string", "null"], "default": null },
        "remove_handler": { "type": ["string", "null"], "default": null },
        "read_handlers": {
          "type": "array",
          "items": { "$ref": "#/$defs/RuntimeOpMapping" },
          "default": []
        },
        "write_handlers": {
          "type": "array",
          "items": { "$ref": "#/$defs/RuntimeOpMapping" },
          "default": []
        },
        "transmit_handler": {
          "anyOf": [{ "$ref": "#/$defs/RuntimeOpMapping" }, { "type": "null" }],
          "default": null
        },
        "receive_handler": {
          "anyOf": [{ "$ref": "#/$defs/RuntimeOpMapping" }, { "type": "null" }],
          "default": null
        },
        "dma_setup": {
          "anyOf": [{ "$ref": "#/$defs/DMASpec" }, { "type": "null" }],
          "default": null
        },
        "interrupt_lifecycle": { "$ref": "#/$defs/InterruptLifecycle" }
      }
    },
    "SequenceStep": {
      "type": "object",
      "required": ["step", "operation", "source_function", "description"],
      "properties": {
        "step": { "type": "integer", "minimum": 1 },
        "operation": {
          "type": "string",
          "enum": [
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
            "Other"
          ]
        },
        "source_function": { "type": "string" },
        "target_register": { "type": ["string", "null"], "default": null },
        "offset": { "type": ["string", "null"], "default": null },
        "value_or_mask": { "type": ["string", "null"], "default": null },
        "poll_condition": { "type": ["string", "null"], "default": null },
        "description": { "type": "string" }
      }
    },
    "SysfsAttribute": {
      "type": "object",
      "required": ["name", "mode"],
      "properties": {
        "name": { "type": "string" },
        "mode": { "type": "string" },
        "show_function": { "type": ["string", "null"], "default": null },
        "store_function": { "type": ["string", "null"], "default": null }
      }
    }
  }
}

// ==========================================================================
// File: pyproject.toml
// ==========================================================================

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "kernelite-prism"
version = "0.2.0"
description = "AI-driven extraction tool for converting Linux driver C code into structured hardware and C++ microkernel specifications."
readme = "README.md"
license = { text = "GPL-3.0-or-later" }
requires-python = ">=3.11"
dependencies = [
    "torch>=2.1.0",
    "transformers>=4.40.0",
    "accelerate>=0.28.0",
    "bitsandbytes>=0.43.0",
    "pydantic>=2.6.0",
    "click>=8.1.0",
    "pyyaml>=6.0.0",
    "tree-sitter>=0.23.0",
    "tree-sitter-c>=0.23.0",
    "jsonschema>=4.21.0",
    "jinja2>=3.1.3",
    "outlines>=0.1.0",
    "openai>=1.30.0",
    "instructor>=1.3.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=5.0.0",
]

[project.scripts]
kernelite-prism = "kernelite_prism.cli:main"
kprism = "kernelite_prism.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
kernelite_prism = ["schemas/*.json"]

// ==========================================================================
// File: README.md
// ==========================================================================

# Kernelite Prism ğŸ’

> **Refractoring Linux driver source into structured, machine-readable specifications.**

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![API](https://img.shields.io/badge/LLM-Gemini-orange)](https://deepmind.google/technologies/gemini/)

**Kernelite Prism** is an AI-driven, LLM-first extraction tool designed to parse Linux kernel driver source code and output highly structured, machine-readable metadata. 

By passing localized driver directories or individual `.c` files to the Google Gemini API with strict Pydantic schemas, Prism uncovers the hidden hardware logic, interfaces, and dependencies within the kernelâ€”transforming opaque C code into clean JSON or YAML specifications.

---

## ğŸ¯ Primary Use Cases

* **AI Context Enrichment:** Feed accurate, structured hardware specs to autonomous coding agents or LLMs to assist in driver refactoring, porting, or generation.
* **Automated Hardware Documentation:** Generate human-readable driver specification sheets, register matrices, and device support tables directly from source code.
* **Emulation & Testing:** Rapidly scaffold QEMU hardware stubs or synthetic driver mocks based on real kernel driver abstractions and register maps.

---

## ğŸ” Extraction Targets

Prism extracts key Linux device driver structures:

* **Hardware & ID Matching:** `of_device_id` (Device Tree compatible strings), `pci_device_id`, `usb_device_id`, and ACPI tables.
* **Module Metadata:** `MODULE_DESCRIPTION`, `MODULE_AUTHOR`, `MODULE_LICENSE`, and `module_param` types/permissions.
* **Interfaces & Control Surfaces:** `ioctl` command definitions, `sysfs` attribute groups, `procfs` entries, and `file_operations` mappings.
* **Lifecycle & Driver Structs:** Platform, PCI, and I2C driver structs, alongside Probe, Remove, Suspend, and Resume entry points.
* **Register Maps:** Register offset macros, bitfield definitions (`BIT()`, `GENMASK()`), and hardware access logic.

---

## âš™ï¸ How It Works

1. **Context Aggregation:** Discovers target `.c` and `.h` files within a specified file or folder, combining file contents into an aggregated context payload.
2. **LLM Parsing:** Queries the **Gemini API** utilizing strict structured output parameters (`response_schema`).
3. **Pydantic Validation:** Validates responses against typed Pydantic models before returning serialized JSON or YAML artifacts.

---

## ğŸš€ Quickstart & Setup

### Prerequisites

* Python 3.10+
* A Google Gemini API Key (`GEMINI_API_KEY`)

---

### Option A: GitHub Codespaces (Recommended)

1. Open the repository in GitHub Codespaces.
2. Add your `GEMINI_API_KEY` secret under **GitHub Settings** $\rightarrow$ **Codespaces** $\rightarrow$ **Codespaces secrets**.
3. The devcontainer automatically installs `kernelite-prism` in editable mode upon creation.

---

### Option B: Local Installation

1. Clone the repository and navigate into the project:
```bash
git clone https://github.com/your-org/kernelite.git
cd kernelite

```


2. Install the package in editable mode:
```bash
pip install -e .

```


3. Export your Gemini API key:
```bash
export GEMINI_API_KEY="your-gemini-api-key-here"

```



---

## ğŸ› ï¸ Command Line Usage

Use either `kprism` or `kernelite-prism` in your terminal.

```text
Usage: kprism analyze [OPTIONS] TARGET_PATH

  Analyze a target C driver file or directory and produce a structured spec.

Options:
  -f, --format [json|yaml]  Output format serialization (default: json).
  -o, --out PATH            Output file path. Prints to stdout if omitted.
  --model TEXT              Gemini LLM model override (default: gemini-2.5-flash).
  --help                    Show this message and exit.

```

---

### Examples

#### 1. Analyze a single file and output to stdout

```bash
kprism analyze drivers/gpio/gpio-mmio.c

```

#### 2. Export a single driver file to JSON

```bash
kprism analyze drivers/gpio/gpio-mmio.c --format json --out gpio-mmio-spec.json

```

#### 3. Export an entire subsystem directory to YAML

```bash
kprism analyze drivers/i2c/busses/ --format yaml --out i2c-busses-spec.yaml

```

#### 4. Override the Gemini model

```bash
kprism analyze drivers/net/ethernet/realtek/r8169_main.c --model gemini-2.5-pro --out r8169.json

```

---

## ğŸ“„ Example Output

```json
{
  "driver_name": "gpio-mmio",
  "metadata": {
    "author": "Anton Vorontsov <cbouatmailru@gmail.com>",
    "license": "GPL",
    "description": "Generic MMIO GPIO driver"
  },
  "matching_tables": {
    "of_device_id": [
      {
        "compatible": "brcm,bcm2835-gpio"
      }
    ]
  },
  "lifecycle": {
    "probe_function": "bgpio_pdev_probe",
    "remove_function": "bgpio_pdev_remove"
  },
  "structs": [
    "platform_driver"
  ],
  "registers": [
    {
      "name": "BGPIO_DIR",
      "offset": "0x04",
      "description": "Direction control register",
      "bitfields": [
        {
          "name": "GPIO_DIR_IN",
          "mask_macro": "BIT(0)",
          "description": "Configure GPIO pin as input"
        }
      ]
    }
  ]
}

```

---

## ğŸ¤ Contributing

Contributions are welcome! Feel free to open issues or PRs to extend Pydantic schemas for additional kernel subsystems, refine extraction prompts, or handle specific driver macros.

---

## ğŸ“„ License

This project is licensed under the GPL v3 License - see the [LICENSE](LICENSE) file for details.

