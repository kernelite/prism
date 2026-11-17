# Driver Specifications Extraction Tests

This document contains some driver specification ectraction commands to test kernelite prism features.

```
cd /workspaces/prism
mkdir -p specs

# 1. Stage 1A — PL011 UART Console (0x09000000)
kprism analyze linux/drivers/tty/serial/amba-pl011.c \
  -I linux/include/linux/amba/serial.h \
  --model Qwen/Qwen2.5-Coder-3B-Instruct \
  --device cuda --quant 4bit \
  --out specs/amba_pl011.json \
  --md-out specs/AMBA_PL011.md

# 2. Stage 1B — ARM GICv2 Interrupt Controller (0x08000000 / 0x08010000)
kprism analyze linux/drivers/irqchip/irq-gic.c \
  -I linux/include/linux/irqchip/arm-gic.h \
  --model Qwen/Qwen2.5-Coder-3B-Instruct \
  --device cuda --quant 4bit \
  --out specs/irq_gic.json \
  --md-out specs/IRQ_GIC.md

# 3. Stage 2 — QEMU fw_cfg / ramfb Display Interface (0x09020000)
kprism analyze linux/drivers/firmware/qemu_fw_cfg.c \
  -I linux/include/uapi/linux/qemu_fw_cfg.h \
  --model Qwen/Qwen2.5-Coder-3B-Instruct \
  --device cuda --quant 4bit \
  --out specs/qemu_fw_cfg.json \
  --md-out specs/QEMU_FW_CFG.md

# 4. Stage 3 — VirtIO MMIO Transport (0x0a000000 - 0x0a003e00)
kprism analyze linux/drivers/virtio/virtio_mmio.c \
  -I linux/include/uapi/linux/virtio_mmio.h \
  -I linux/include/uapi/linux/virtio_config.h \
  --model Qwen/Qwen2.5-Coder-3B-Instruct \
  --device cuda --quant 4bit \
  --out specs/virtio_mmio.json \
  --md-out specs/VIRTIO_MMIO.md

# 5. Stage 4 — VirtIO HID Input (Keyboard & Tablet, Device ID 18)
kprism analyze linux/drivers/virtio/virtio_input.c \
  -I linux/include/uapi/linux/virtio_input.h \
  --model Qwen/Qwen2.5-Coder-3B-Instruct \
  --device cuda --quant 4bit \
  --out specs/virtio_input.json \
  --md-out specs/VIRTIO_INPUT.md

# 6. Stage 5 — VirtIO Ethernet NIC (Device ID 1, compacted for 8GB VRAM)
kprism analyze linux/drivers/net/virtio_net.c \
  -I linux/include/uapi/linux/virtio_net.h \
  --model Qwen/Qwen2.5-Coder-3B-Instruct \
  --max-tokens 14000 \
  --device cuda --quant 4bit \
  --out specs/virtio_net.json \
  --md-out specs/VIRTIO_NET.md
```