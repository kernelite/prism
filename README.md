# Kernelite Prism 💎

> **Refractoring Linux driver source into structured, machine-readable specifications.**

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![API](https://img.shields.io/badge/LLM-Gemini-orange)](https://deepmind.google/technologies/gemini/)

**Kernelite Prism** is an AI-driven, LLM-first extraction tool designed to parse Linux kernel driver source code and output highly structured, machine-readable metadata. 

By passing localized driver directories or individual `.c` files to the Google Gemini API with strict Pydantic schemas, Prism uncovers the hidden hardware logic, interfaces, and dependencies within the kernel—transforming opaque C code into clean JSON or YAML specifications.

---

## 🎯 Primary Use Cases

* **AI Context Enrichment:** Feed highly accurate, structured hardware specs to autonomous coding agents or LLMs to assist in driver refactoring, porting, or generation.
* **Automated Hardware Documentation:** Generate human-readable driver specification sheets, register matrices, and device support tables directly from the source truth.
* **Emulation & Testing:** Rapidly scaffold QEMU hardware stubs or synthetic driver mocks based on real kernel driver abstractions and register maps.

## 🔍 Extraction Targets

Prism focuses on extracting the core DNA of Linux device drivers:

* **Hardware & ID Matching:** `of_device_id` (Device Tree compatible strings), `pci_device_id`, `usb_device_id`, and ACPI tables.
* **Module Metadata:** `MODULE_DESCRIPTION`, `MODULE_AUTHOR`, `MODULE_LICENSE`, and `module_param` types/permissions.
* **Interfaces & Control Surfaces:** `ioctl` command definitions, `sysfs` attribute groups, `procfs` entries, and `file_operations` mappings.
* **Lifecycle & Driver Structs:** Platform, PCI, and I2C driver structs, alongside Probe, Remove, Suspend, and Resume entry points.
* **Register Maps:** Register offset macros, bitfield definitions (`BIT()`, `GENMASK()`), and hardware access logic.

## ⚙️ How it Works

Unlike traditional static analysis tools that require complex Clang/LLVM build environments or full kernel header trees, Prism uses an **LLM-first approach**:
1. **Context Aggregation:** Prism ingests target `.c` and `.h` files within a specified driver folder, grouping shared context.
2. **LLM Parsing:** It leverages the **Gemini API** with strict structured output enforcement.
3. **Validation:** Responses are validated via **Pydantic** models in Python, guaranteeing the output matches the expected specification schema.

## 🚀 Getting Started

### Prerequisites
* Python 3.10+
* A Google Gemini API Key

### Installation

Clone the repository and install the package:

```bash
git clone https://github.com/your-org/kernelite.git
cd kernelite/prism
pip install -e .
```

### Configuration

Export your Gemini API key to your environment:

```bash
export GEMINI_API_KEY="your-api-key-here"
```

## 🛠️ Usage

Run `kprism` (or `kernelite-prism`) against a local driver directory or file. 

**Analyze a single driver file:**
```bash
kprism analyze ./drivers/gpio/gpio-mmio.c --format json --out gpio-mmio-spec.json
```

**Analyze an entire driver subsystem directory:**
```bash
kprism analyze ./drivers/i2c/busses/ --format yaml --out i2c-busses.yaml
```

### Example Output

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
      { "compatible": "linux,gpio-mmio" }
    ]
  },
  "lifecycle": {
    "probe_function": "bgpio_pdev_probe",
    "remove_function": "bgpio_pdev_remove"
  },
  "structs": [
    "platform_driver"
  ]
}
```

## 🤝 Contributing

Contributions are welcome! Whether it's adding new Pydantic models for different kernel subsystems, improving the Gemini prompts, or handling edge cases in legacy driver code, please feel free to open a PR or Issue.

## 📄 License

This project is licensed under the GPL v3 License - see the [LICENSE](LICENSE) file for details.
