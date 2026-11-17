# Kernelite Prism

> **Automated hardware specification extraction and bare-metal C++ microkernel porting documentation from Linux kernel driver C code.**

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)
[![JSON Schema](https://img.shields.io/badge/schema-Draft%202020--12-green.svg)](https://json-schema.org/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

**Kernelite Prism** (`kernelite-prism` / `kprism`) is an AI-driven static analysis and specification extraction pipeline built to accelerate porting Linux device drivers to bare-metal C++ microkernels.

By combining deterministic **Tree-sitter C AST pre-processing** with **schema-constrained LLM extraction**, Prism parses Linux kernel driver source trees (`.c` and `.h`) and generates two verified artifacts:

1. **Machine-Parsable Specification (`driver_spec.json`)**: A strictly typed JSON artifact conforming to JSON Schema Draft 2020-12 (`schemas/driver_spec.schema.json`), containing register maps, bitfields, isolated offset formulas, chronological `.probe` initialization steps, DMA setup, and interrupt lifecycles for automated C++ code generation.
2. **Human-Readable Specification (`DRIVER_SPEC.md`)**: Self-contained Markdown documentation featuring register memory tables, Mermaid.js initialization and interrupt sequence diagrams, abstract concurrency lock mappings, and a C++20 bare-metal driver skeleton.

---

## Technical Architecture

```text
[ Linux Source Code (.c, .h) ]
               │
               ▼
   [ Tree-sitter C AST Parser ]      ──► Extracts register macros, bitmasks, driver structs,
   (ast_parser.py & aggregator.py)       MMIO/IRQ/lock calls; strips kernel boilerplate
               │
               ▼
   [ Constrained LLM Engine ]        ──► Enforces driver_spec.schema.json via Transformers,
   (extractor.py)                        Outlines, OpenAI Structured Outputs, or Instructor
               │
       ┌───────┴───────────────────────┐
       ▼                               ▼
[ driver_spec.json ]           [ Verification Harness ]
(models/ & serializer.py)       (validator.py: Schema + AST Grounding + Parity)
       │
       ▼
[ Jinja2 + Mermaid Generator ] ──► [ DRIVER_SPEC.md ]
(doc_generator.py)                  (Register Tables, Sequence Diagrams, C++20 Notes)
```

---

## Extraction & Analysis Capabilities

- **AST Pre-Processing & Token Optimization**: Uses `tree-sitter-c` to extract `#define` register offsets, `BIT()` / `GENMASK()` bitmasks, driver structs (`platform_driver`, `pci_driver`, `net_device_ops`, `uart_ops`, `file_operations`, `of_device_id`, `pci_device_id`), and MMIO calls (`readl`, `writel`, `ioread32`, `iowrite32`), while stripping `MODULE_*` macros, debug logging (`dev_dbg`, `pr_debug`), and `CONFIG_PM` blocks.
- **Deterministic Register Grounding**: Evaluates C constant expressions safely and verifies extracted offsets and bitmasks against AST-parsed header definitions to prevent hallucinated addresses.
- **Dynamic Offset Expression Isolation**: Separates base hexadecimal offsets from indexed C offset arithmetic (e.g., `0x03818 + (n * 0x100)` or `offset << port->regshift`).
- **Probe & Interrupt Lifecycle Parity**: Captures ordered initialization steps (`MMIO Map`, `Hardware Reset`, `Write Register`, `Read & Poll`, `DMA Setup`, `Interrupt Hook`) and maps top-half `irqreturn_t` routines to raw status, mask, and acknowledgment registers (`Read-to-Clear` vs. `W1C`), rendering both as Mermaid sequence diagrams.
- **Concurrency & Microkernel Porting Guidance**: Translates Linux synchronization primitives (`spin_lock_irqsave`, `spin_lock`, `mutex_lock`) into abstract concurrency locks (`IRQSafeSpinlock`, `Spinlock`, `SleepableMutex`) alongside Linux-to-C++ API migration tables.

---

## Quickstart & Installation

### Prerequisites

- Python **3.11+**
- Optional: NVIDIA GPU with CUDA drivers for quantized local inference (`4bit` / `8bit`), or an OpenAI-compatible API endpoint.

### Option A: VS Code Dev Container / GitHub Codespaces (Recommended)

1. Open the repository in a Dev Container or GitHub Codespaces.
2. The container automatically configures Python 3.11, installs PyTorch with CUDA support and `bitsandbytes`, and installs `kernelite-prism` in editable mode.

### Option B: Local Installation

```bash
git clone [https://github.com/your-org/kernelite.git](https://github.com/your-org/kernelite.git)
cd prism

# Install core package and runtime dependencies
pip install -e .

# Optional: Install pytest and coverage tools for running the test suite
pip install -e ".[dev]"
```

### Environment Variables

Depending on your chosen inference backend, configure the relevant credentials:

```bash
# For gated Hugging Face models (local transformers / outlines backends)
export HF_TOKEN="your-huggingface-token"

# For OpenAI Structured Outputs or Instructor backends
export OPENAI_API_KEY="your-openai-api-key"
# Optional custom OpenAI-compatible endpoint (e.g., vLLM, Ollama, LocalAI)
export OPENAI_BASE_URL="http://localhost:8000/v1"
```

---

## Command Line Usage

Both `kprism` and `kernelite-prism` are installed as CLI entry points.

### 1. `kprism analyze` — Extract Machine & Human Specifications

```text
Usage: kprism analyze [OPTIONS] TARGET_PATH

Options:
  -f, --format [json|yaml|md]                             Output format (default: json)
  -o, --out PATH                                          Output path for driver_spec.json / YAML
  --md-out PATH                                           Simultaneously write DRIVER_SPEC.md
  --model TEXT                                            Model ID or path (default: Qwen/Qwen2.5-Coder-3B-Instruct)
  --backend [transformers|outlines|openai|instructor]     Structured output backend (default: transformers)
  -q, --quant [4bit|8bit|none]                            Quantization precision (default: 4bit)
  --device TEXT                                           Execution device: cuda, cuda:0, cpu (default: cuda)
  --token TEXT                                            Hugging Face API token
  --no-ast-prune                                          Disable AST boilerplate stripping
  --help                                                  Show this message and exit
```

#### Examples

```bash
# Local GPU inference (4-bit quantized Qwen2.5-Coder-3B-Instruct) emitting JSON + Markdown
kprism analyze drivers/virtio/virtio_mmio.c \
  --out build/driver_spec.json \
  --md-out build/DRIVER_SPEC.md

# Local inference with Outlines grammar-constrained decoding
kprism analyze drivers/net/ethernet/intel/e1000/ \
  --backend outlines \
  --out build/e1000_spec.json \
  --md-out build/E1000_SPEC.md

# CPU-only local execution (disables bitsandbytes quantization)
kprism analyze drivers/tty/serial/8250/8250_early.c \
  --device cpu \
  --quant none \
  --out build/8250_early.json

# API-based structured output extraction via Instructor or OpenAI
kprism analyze drivers/virtio/virtio_mmio.c \
  --backend instructor \
  --model gpt-4o-mini \
  --out build/driver_spec.json \
  --md-out build/DRIVER_SPEC.md
```

### 2. `kprism export-schema` — Export Canonical JSON Schema (Draft 2020-12)

```bash
kprism export-schema --out schemas/driver_spec.schema.json
```

### 3. `kprism validate` — Verify Schema Compliance

```bash
kprism validate build/driver_spec.json --schema schemas/driver_spec.schema.json
```

### 4. `kprism render-md` — Render Markdown Documentation from JSON

```bash
kprism render-md build/driver_spec.json --out build/DRIVER_SPEC.md
```

---

## Running the Verification & Integration Tests

Run the test suite covering JSON Schema Draft 2020-12 compliance, Tree-sitter AST constant grounding, probe sequence parity, and Markdown generation across reference drivers (`8250_early.c`, `e1000_main.c`, and `virtio_mmio.c`):

```bash
pytest -v
```

---

## Example Output (`driver_spec.json`)

```json
{
  "device_name": "VirtIO MMIO Transport",
  "driver_name": "virtio_mmio",
  "compatibility": [
    "virtio,mmio"
  ],
  "hardware_info": {
    "bus_type": "platform",
    "compatible_strings": [
      "virtio,mmio"
    ],
    "base_address_offsets": [
      {
        "region_id": "0",
        "name": "VIRTIO_MMIO_REGS",
        "base_offset": "0x000",
        "size_hint": "0x200",
        "description": "VirtIO MMIO control and virtqueue configuration registers"
      }
    ],
    "endianness": "little"
  },
  "lifecycle": {
    "probe_function": "virtio_mmio_probe",
    "remove_function": "virtio_mmio_remove"
  },
  "structs": [
    "of_device_id",
    "platform_driver"
  ],
  "register_map": [
    {
      "name": "VIRTIO_MMIO_MAGIC_VALUE",
      "offset": "0x000",
      "width": 32,
      "access": "RO",
      "reset_value": "0x74726976",
      "description": "Magic value ('virt' in little-endian)"
    },
    {
      "name": "VIRTIO_MMIO_INTERRUPT_STATUS",
      "offset": "0x060",
      "width": 32,
      "access": "RO",
      "description": "Interrupt status flags",
      "bitfields": [
        {
          "name": "VIRTIO_MMIO_INT_VRING",
          "bits": "0",
          "mask_macro": "BIT(0)"
        },
        {
          "name": "VIRTIO_MMIO_INT_CONFIG",
          "bits": "1",
          "mask_macro": "BIT(1)"
        }
      ]
    },
    {
      "name": "VIRTIO_MMIO_INTERRUPT_ACK",
      "offset": "0x064",
      "width": 32,
      "access": "WO",
      "description": "Interrupt acknowledge register (Write-1-to-Clear)"
    }
  ],
  "init_sequence": [
    {
      "step": 1,
      "operation": "MMIO Map",
      "source_function": "virtio_mmio_probe",
      "value_or_mask": "resource 0",
      "description": "Map platform MMIO memory region via devm_platform_ioremap_resource"
    },
    {
      "step": 2,
      "operation": "Read Register",
      "source_function": "virtio_mmio_probe",
      "target_register": "VIRTIO_MMIO_MAGIC_VALUE",
      "offset": "0x000",
      "value_or_mask": "0x74726976",
      "description": "Verify 'virt' magic signature"
    }
  ]
}
```

---

## License

This project is licensed under the **GNU General Public License v3.0 or later** (`GPL-3.0-or-later`). See the [LICENSE](LICENSE) file for details.