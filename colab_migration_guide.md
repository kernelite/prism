# Migrating Kernelite Prism to Google Colab Pro

This guide explains how to migrate the execution of **Kernelite Prism** from a local environment to Google Colab (Pro/Pay-as-you-go). By leveraging Google's infrastructure, we can use high-capacity GPUs (L4 or A100) to run unquantized or larger models (e.g., 14B or 32B), while ensuring data persistence and cache management via Google Drive.

## 1. Initial Notebook Configuration

Create a new notebook on Google Colab and configure the hardware acceleration:

1. Go to **Runtime** > **Change runtime type**.
2. Select **Hardware accelerator: GPU**.
3. Choose a high-capacity GPU (e.g., **L4** or **A100**).
4. Save.

## 2. Google Drive Mounting and Cache Configuration

Since Colab's storage is volatile, it's crucial to mount Google Drive to save the extracted specifications (JSON/YAML files) and to cache the Hugging Face model weights. This prevents having to redownload several gigabytes every time the notebook is restarted.

Create a code cell and insert the following script:

```python
import os
from google.colab import drive

# 1. Mount Google Drive
drive.mount('/content/drive')

# 2. Create the necessary folders (if they don't exist)
CACHE_DIR = "/content/drive/MyDrive/Kernelite/hf_cache"
OUTPUT_DIR = "/content/drive/MyDrive/Kernelite/specs"
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 3. Set the environment variable for the Hugging Face cache
os.environ["HF_HOME"] = CACHE_DIR
print(f"Hugging Face cache set to: {os.environ['HF_HOME']}")
```

## 3. Repository Cloning and Installation

In a new cell, clone the source code (if your repo is private, remember to configure a GitHub Personal Access Token) and install the dependencies:

```bash
# Replace the URL with your actual repository URL
!git clone https://github.com/your-username/kernelite-prism.git /content/kernelite-prism

# Move into the project directory
%cd /content/kernelite-prism

# Install the required libraries and the package in editable mode
!pip install -q transformers accelerate bitsandbytes pydantic click pyyaml
!pip install -q -e .
```

## 4. Loading the C Source Code (Drivers)

You have two options for providing the C drivers to Prism:

*   **Clone an entire kernel tree (e.g., torvalds/linux):** Useful if you want to scan whole subsystems.
*   **Upload specific files to Google Drive:** Useful for targeted analysis.

If you have your C files on Google Drive, you can analyze them directly from the path `/content/drive/MyDrive/...`.

```bash
# Example: Clone the Linux tree to have test material
!git clone --depth 1 https://github.com/torvalds/linux.git /content/linux
```

## 5. Running the Extraction

Now you can use the `kprism` CLI directly from the notebook, just as you do in your local terminal. Use the exclamation mark `!` to run shell commands.

### Example A: Extraction with the 7B model (No quantization on L4/A100)

If you selected an L4 or A100 GPU, the 24GB+ of VRAM allows you to run the 7B model at full precision, improving speed and further reducing hallucinations.

```bash
!kprism analyze /content/linux/drivers/gpio/gpio-mmio.c \
    --model Qwen/Qwen2.5-Coder-7B-Instruct \
    --quant none \
    --format json \
    --out /content/drive/MyDrive/Kernelite/specs/gpio-mmio.json
```

### Example B: Scaling to the 32B model (Quantized on L4 or A100)

For highly complex extraction tasks, you can use the flagship 32B model quantized to 4-bit (it will take up about 20-22 GB).

```bash
!kprism analyze /content/linux/drivers/net/ethernet/intel/e1000/ \
    --model Qwen/Qwen2.5-Coder-32B-Instruct \
    --quant 4bit \
    --format yaml \
    --out /content/drive/MyDrive/Kernelite/specs/e1000-spec.yaml
```

## 6. Complete Template (Copy & Paste)

Here is a single block to copy into one cell to have everything ready upon session startup:

```python
import os
from google.colab import drive

# Mount Drive & Setup Cache
drive.mount('/content/drive')
os.makedirs("/content/drive/MyDrive/Kernelite/hf_cache", exist_ok=True)
os.makedirs("/content/drive/MyDrive/Kernelite/specs", exist_ok=True)
os.environ["HF_HOME"] = "/content/drive/MyDrive/Kernelite/hf_cache"

# Install Dependencies
!pip install -q transformers accelerate bitsandbytes pydantic click pyyaml
!rm -rf /content/prism_app
!git clone https://github.com/your-username/kernelite-prism.git /content/prism_app
%cd /content/prism_app
!pip install -q -e .

print("\n✅ Environment ready! You can use the !kprism command")
```