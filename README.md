# Kernelite Prism 💎

> **Refractoring Linux driver source into structured, machine-readable specifications.**

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![API](https://img.shields.io/badge/LLM-Gemini-orange)](https://deepmind.google/technologies/gemini/)

**Kernelite Prism** is an AI-driven, LLM-first extraction tool designed to parse Linux kernel driver source code and output highly structured, machine-readable metadata. 

By passing localized driver directories or individual `.c` files to the Google Gemini API with strict Pydantic schemas, Prism uncovers the hidden hardware logic, interfaces, and dependencies within the kernel—transforming opaque C code into clean JSON or YAML specifications.

---

## 🎯 Primary Use Cases

* **AI Context Enrichment:** Feed accurate, structured hardware specs to autonomous coding agents or LLMs to assist in driver refactoring, porting, or generation.
* **Automated Hardware Documentation:** Generate human-readable driver specification sheets, register matrices, and device support tables directly from source code.
* **Emulation & Testing:** Rapidly scaffold QEMU hardware stubs or synthetic driver mocks based on real kernel driver abstractions and register maps.

---

## 🔍 Extraction Targets

Prism extracts key Linux device driver structures:

* **Hardware & ID Matching:** `of_device_id` (Device Tree compatible strings), `pci_device_id`, `usb_device_id`, and ACPI tables.
* **Module Metadata:** `MODULE_DESCRIPTION`, `MODULE_AUTHOR`, `MODULE_LICENSE`, and `module_param` types/permissions.
* **Interfaces & Control Surfaces:** `ioctl` command definitions, `sysfs` attribute groups, `procfs` entries, and `file_operations` mappings.
* **Lifecycle & Driver Structs:** Platform, PCI, and I2C driver structs, alongside Probe, Remove, Suspend, and Resume entry points.
* **Register Maps:** Register offset macros, bitfield definitions (`BIT()`, `GENMASK()`), and hardware access logic.

---

## ⚙️ How It Works

1. **Context Aggregation:** Discovers target `.c` and `.h` files within a specified file or folder, combining file contents into an aggregated context payload.
2. **LLM Parsing:** Queries the **Gemini API** utilizing strict structured output parameters (`response_schema`).
3. **Pydantic Validation:** Validates responses against typed Pydantic models before returning serialized JSON or YAML artifacts.

---

## 🚀 Quickstart & Setup

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

## 🛠️ Command Line Usage

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

## 📄 Example Output

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

## 🤝 Contributing

Contributions are welcome! Feel free to open issues or PRs to extend Pydantic schemas for additional kernel subsystems, refine extraction prompts, or handle specific driver macros.

---

## 📄 License

This project is licensed under the GPL v3 License - see the [LICENSE](LICENSE) file for details.