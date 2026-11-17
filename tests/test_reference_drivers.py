import json
import os
from pathlib import Path
from typing import Dict
import pytest

from kernelite_prism.core.ast_parser import parse_driver_ast
from kernelite_prism.core.doc_generator import generate_markdown_spec
from kernelite_prism.core.extractor import DriverExtractor
from kernelite_prism.core.validator import (
    run_full_verification,
    validate_schema_compliance,
)
from kernelite_prism.models.base import DriverSpec, export_json_schema

# ---------------------------------------------------------------------------
# Reference Linux Driver Fixtures:
#   1. drivers/tty/serial/8250/8250_early.c
#   2. drivers/net/ethernet/intel/e1000/e1000_main.c
#   3. drivers/virtio/virtio_mmio.c
# ---------------------------------------------------------------------------

UART_8250_EARLY_C = """
#include <linux/tty.h>
#include <linux/init.h>
#include <linux/console.h>
#include <linux/serial_8250.h>
#include <linux/serial_reg.h>
#include <linux/io.h>

#define UART_RX     0x00
#define UART_TX     0x00
#define UART_IER    0x01
#define UART_IIR    0x02
#define UART_FCR    0x02
#define UART_LCR    0x03
#define UART_MCR    0x04
#define UART_LSR    0x05

#define UART_LCR_WLEN8  0x03
#define UART_LCR_DLAB   0x80
#define UART_FCR_ENABLE_FIFO 0x01
#define UART_FCR_CLEAR_RCVR  0x02
#define UART_FCR_CLEAR_XMIT  0x04
#define UART_LSR_THRE   0x20
#define UART_LSR_TEMT   0x40
#define UART_MCR_DTR    0x01
#define UART_MCR_RTS    0x02

static unsigned int serial8250_early_in(struct uart_port *port, int offset)
{
    return readb(port->membase + (offset << port->regshift));
}

static void serial8250_early_out(struct uart_port *port, int offset, int value)
{
    writeb(value, port->membase + (offset << port->regshift));
}

static void serial_putc(struct uart_port *port, unsigned char c)
{
    unsigned int status;
    for (;;) {
        status = serial8250_early_in(port, UART_LSR);
        if ((status & (UART_LSR_THRE | UART_LSR_TEMT)) == (UART_LSR_THRE | UART_LSR_TEMT))
            break;
        cpu_relax();
    }
    serial8250_early_out(port, UART_TX, c);
}

static void init_port(struct earlycon_device *device)
{
    struct uart_port *port = &device->port;
    unsigned int divisor;
    unsigned char c;

    serial8250_early_out(port, UART_LCR, UART_LCR_WLEN8);
    serial8250_early_out(port, UART_IER, 0);
    serial8250_early_out(port, UART_FCR, UART_FCR_ENABLE_FIFO | UART_FCR_CLEAR_RCVR | UART_FCR_CLEAR_XMIT);
    serial8250_early_out(port, UART_MCR, UART_MCR_DTR | UART_MCR_RTS);

    divisor = DIV_ROUND_CLOSEST(port->uartclk, 16 * device->baud);
    c = serial8250_early_in(port, UART_LCR);
    serial8250_early_out(port, UART_LCR, c | UART_LCR_DLAB);
    serial8250_early_out(port, UART_TX, divisor & 0xff);
    serial8250_early_out(port, UART_IER, (divisor >> 8) & 0xff);
    serial8250_early_out(port, UART_LCR, c & ~UART_LCR_DLAB);
}

static int __init early_serial8250_setup(struct earlycon_device *device, const char *options)
{
    if (!device->port.membase)
        return -ENODEV;
    if (device->baud)
        init_port(device);
    device->con->write = early_serial8250_write;
    return 0;
}

static const struct of_device_id early_8250_of_match[] = {
    { .compatible = "ns16550a" },
    { .compatible = "ns16550" },
    {}
};

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Russell King");
MODULE_DESCRIPTION("8250/16550 early serial console driver");
"""

E1000_MAIN_C = """
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/netdevice.h>
#include <linux/interrupt.h>
#include <linux/spinlock.h>
#include <linux/io.h>

#define E1000_CTRL      0x00000
#define E1000_STATUS    0x00008
#define E1000_ICR       0x000C0
#define E1000_IMS       0x000D0
#define E1000_IMC       0x000D8
#define E1000_RCTL      0x00100
#define E1000_TCTL      0x00400
#define E1000_TDT       0x03818

#define E1000_CTRL_RST      BIT(26)
#define E1000_CTRL_SLU      BIT(6)
#define E1000_STATUS_LU     BIT(1)
#define E1000_IMS_RXT0      BIT(7)
#define E1000_IMS_TXDW      BIT(0)
#define E1000_ICR_INT_ASSERTED BIT(31)

#define er32(reg) writel_dummy_read(hw->hw_addr + E1000_##reg)
#define ew32(reg, val) writel((val), hw->hw_addr + E1000_##reg)

static const struct pci_device_id e1000_pci_tbl[] = {
    { PCI_VDEVICE(INTEL, 0x100E), 0 },
    { PCI_VDEVICE(INTEL, 0x100F), 0 },
    { 0, }
};

static irqreturn_t e1000_intr(int irq, void *data)
{
    struct net_device *netdev = data;
    struct e1000_adapter *adapter = netdev_priv(netdev);
    u32 icr = readl(adapter->hw.hw_addr + E1000_ICR);

    if (unlikely(!icr))
        return IRQ_NONE;

    writel(~0, adapter->hw.hw_addr + E1000_IMC);
    napi_schedule(&adapter->napi);
    return IRQ_HANDLED;
}

static netdev_tx_t e1000_xmit_frame(struct sk_buff *skb, struct net_device *netdev)
{
    struct e1000_adapter *adapter = netdev_priv(netdev);
    unsigned long flags;

    spin_lock_irqsave(&adapter->tx_lock, flags);
    writel(adapter->tx_ring.next_to_use, adapter->hw.hw_addr + E1000_TDT);
    spin_unlock_irqrestore(&adapter->tx_lock, flags);
    return NETDEV_TX_OK;
}

static int e1000_probe(struct pci_dev *pdev, const struct pci_device_id *ent)
{
    struct e1000_adapter *adapter;
    u32 ctrl, status;
    int err;

    dev_dbg(&pdev->dev, "Probing Intel e1000 PCI adapter\\n");
    err = pci_enable_device_mem(pdev);
    if (err)
        return err;

    adapter->hw.hw_addr = pci_iomap(pdev, 0, 0);
    spin_lock_init(&adapter->tx_lock);

    writel(0xffffffff, adapter->hw.hw_addr + E1000_IMC);
    ctrl = readl(adapter->hw.hw_addr + E1000_CTRL);
    writel(ctrl | E1000_CTRL_RST, adapter->hw.hw_addr + E1000_CTRL);

    status = readl(adapter->hw.hw_addr + E1000_STATUS);
    if (!(status & E1000_STATUS_LU))
        dev_dbg(&pdev->dev, "Link down during probe\\n");

    dma_alloc_coherent(&pdev->dev, 4096, &adapter->tx_dma, GFP_KERNEL);
    err = request_irq(pdev->irq, e1000_intr, IRQF_SHARED, "e1000", adapter->netdev);
    writel(E1000_IMS_RXT0 | E1000_IMS_TXDW, adapter->hw.hw_addr + E1000_IMS);
    return err;
}

static void e1000_remove(struct pci_dev *pdev)
{
    free_irq(pdev->irq, pdev);
}

static const struct net_device_ops e1000_netdev_ops = {
    .ndo_start_xmit = e1000_xmit_frame,
};

static struct pci_driver e1000_driver = {
    .name = "e1000",
    .id_table = e1000_pci_tbl,
    .probe = e1000_probe,
    .remove = e1000_remove,
};

MODULE_LICENSE("GPL v2");
MODULE_AUTHOR("Intel Corporation");
MODULE_DESCRIPTION("Intel(R) PRO/1000 Network Driver");
"""

VIRTIO_MMIO_C = """
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/spinlock.h>
#include <linux/virtio_mmio.h>

#define VIRTIO_MMIO_MAGIC_VALUE         0x000
#define VIRTIO_MMIO_VERSION             0x004
#define VIRTIO_MMIO_DEVICE_ID           0x008
#define VIRTIO_MMIO_VENDOR_ID           0x00c
#define VIRTIO_MMIO_DEVICE_FEATURES     0x010
#define VIRTIO_MMIO_DRIVER_FEATURES     0x020
#define VIRTIO_MMIO_QUEUE_SEL           0x030
#define VIRTIO_MMIO_QUEUE_NOTIFY        0x050
#define VIRTIO_MMIO_INTERRUPT_STATUS    0x060
#define VIRTIO_MMIO_INTERRUPT_ACK       0x064
#define VIRTIO_MMIO_STATUS              0x070

#define VIRTIO_MMIO_INT_VRING           BIT(0)
#define VIRTIO_MMIO_INT_CONFIG          BIT(1)
#define VIRTIO_CONFIG_S_ACKNOWLEDGE     0x01
#define VIRTIO_CONFIG_S_DRIVER          0x02
#define VIRTIO_CONFIG_S_DRIVER_OK       0x04

static irqreturn_t vm_interrupt(int irq, void *opaque)
{
    struct virtio_mmio_device *vm_dev = opaque;
    unsigned long status;

    status = readl(vm_dev->base + VIRTIO_MMIO_INTERRUPT_STATUS);
    writel(status, vm_dev->base + VIRTIO_MMIO_INTERRUPT_ACK);

    if (unlikely(!status))
        return IRQ_NONE;

    return IRQ_HANDLED;
}

static int virtio_mmio_probe(struct platform_device *pdev)
{
    struct virtio_mmio_device *vm_dev;
    unsigned long magic, version;
    int rc;

    vm_dev = devm_kzalloc(&pdev->dev, sizeof(*vm_dev), GFP_KERNEL);
    vm_dev->base = devm_platform_ioremap_resource(pdev, 0);
    spin_lock_init(&vm_dev->lock);

    magic = readl(vm_dev->base + VIRTIO_MMIO_MAGIC_VALUE);
    if (magic != 0x74726976)
        return -ENODEV;

    version = readl(vm_dev->base + VIRTIO_MMIO_VERSION);
    writel(0, vm_dev->base + VIRTIO_MMIO_STATUS);
    writel(VIRTIO_CONFIG_S_ACKNOWLEDGE, vm_dev->base + VIRTIO_MMIO_STATUS);
    writel(VIRTIO_CONFIG_S_DRIVER, vm_dev->base + VIRTIO_MMIO_STATUS);

    rc = devm_request_irq(&pdev->dev, platform_get_irq(pdev, 0), vm_interrupt,
                          IRQF_SHARED, dev_name(&pdev->dev), vm_dev);
    writel(VIRTIO_CONFIG_S_ACKNOWLEDGE | VIRTIO_CONFIG_S_DRIVER | VIRTIO_CONFIG_S_DRIVER_OK,
           vm_dev->base + VIRTIO_MMIO_STATUS);
    return rc;
}

static void virtio_mmio_remove(struct platform_device *pdev)
{
    struct virtio_mmio_device *vm_dev = platform_get_drvdata(pdev);
    writel(0, vm_dev->base + VIRTIO_MMIO_STATUS);
}

static const struct of_device_id virtio_mmio_match[] = {
    { .compatible = "virtio,mmio" },
    {},
};

static struct platform_driver virtio_mmio_driver = {
    .probe = virtio_mmio_probe,
    .remove = virtio_mmio_remove,
    .driver = {
        .name = "virtio-mmio",
        .of_match_table = virtio_mmio_match,
    },
};

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Pawel Moll <pawel.moll@arm.com>");
MODULE_DESCRIPTION("Platform bus driver for memory mapped virtio devices");
"""


def _mock_structured_specs() -> Dict[str, dict]:
    """Deterministic structured payloads matching the three Linux reference drivers."""
    return {
        "8250_early": {
            "device_name": "NS16550A Early UART Controller",
            "driver_name": "8250_early",
            "compatibility": ["ns16550a", "ns16550"],
            "hardware_info": {
                "bus_type": "uart",
                "compatible_strings": ["ns16550a", "ns16550"],
                "base_address_offsets": [
                    {
                        "region_id": "0",
                        "name": "UART_MMIO",
                        "base_offset": "0x0",
                        "description": "Stride-shifted 8-bit UART register window (offset << regshift)",
                    }
                ],
                "endianness": "little",
            },
            "register_map": [
                {
                    "name": "UART_TX",
                    "offset": "0x00",
                    "offset_expression": "offset << port->regshift",
                    "width": 8,
                    "access": "WO",
                    "description": "Transmit Holding Register (DLAB=0) / Divisor Latch LSB (DLAB=1)",
                },
                {
                    "name": "UART_IER",
                    "offset": "0x01",
                    "offset_expression": "0x01 << port->regshift",
                    "width": 8,
                    "access": "RW",
                    "description": "Interrupt Enable Register (DLAB=0) / Divisor Latch MSB (DLAB=1)",
                },
                {
                    "name": "UART_FCR",
                    "offset": "0x02",
                    "offset_expression": "0x02 << port->regshift",
                    "width": 8,
                    "access": "WO",
                    "description": "FIFO Control Register",
                    "bitfields": [
                        {"name": "UART_FCR_ENABLE_FIFO", "bits": "0", "mask_macro": "0x01"},
                        {"name": "UART_FCR_CLEAR_RCVR", "bits": "1", "mask_macro": "0x02"},
                        {"name": "UART_FCR_CLEAR_XMIT", "bits": "2", "mask_macro": "0x04"},
                    ],
                },
                {
                    "name": "UART_LCR",
                    "offset": "0x03",
                    "offset_expression": "0x03 << port->regshift",
                    "width": 8,
                    "access": "RW",
                    "description": "Line Control Register",
                    "bitfields": [
                        {"name": "UART_LCR_WLEN8", "bits": "1:0", "mask_macro": "0x03"},
                        {"name": "UART_LCR_DLAB", "bits": "7", "mask_macro": "0x80"},
                    ],
                },
                {
                    "name": "UART_MCR",
                    "offset": "0x04",
                    "offset_expression": "0x04 << port->regshift",
                    "width": 8,
                    "access": "RW",
                    "description": "Modem Control Register",
                },
                {
                    "name": "UART_LSR",
                    "offset": "0x05",
                    "offset_expression": "0x05 << port->regshift",
                    "width": 8,
                    "access": "RO",
                    "description": "Line Status Register",
                    "bitfields": [
                        {"name": "UART_LSR_THRE", "bits": "5", "mask_macro": "0x20"},
                        {"name": "UART_LSR_TEMT", "bits": "6", "mask_macro": "0x40"},
                    ],
                },
            ],
            "init_sequence": [
                {
                    "step": 1,
                    "operation": "Write Register",
                    "source_function": "init_port",
                    "target_register": "UART_LCR",
                    "offset": "0x03",
                    "value_or_mask": "UART_LCR_WLEN8",
                    "description": "Configure 8-bit word length, no parity, 1 stop bit",
                },
                {
                    "step": 2,
                    "operation": "Write Register",
                    "source_function": "init_port",
                    "target_register": "UART_IER",
                    "offset": "0x01",
                    "value_or_mask": "0x00",
                    "description": "Disable all UART interrupts for polled early console operation",
                },
                {
                    "step": 3,
                    "operation": "Write Register",
                    "source_function": "init_port",
                    "target_register": "UART_FCR",
                    "offset": "0x02",
                    "value_or_mask": "UART_FCR_ENABLE_FIFO | UART_FCR_CLEAR_RCVR | UART_FCR_CLEAR_XMIT",
                    "description": "Enable and flush TX/RX hardware FIFOs",
                },
                {
                    "step": 4,
                    "operation": "Write Register",
                    "source_function": "init_port",
                    "target_register": "UART_MCR",
                    "offset": "0x04",
                    "value_or_mask": "UART_MCR_DTR | UART_MCR_RTS",
                    "description": "Assert DTR and RTS modem control signals",
                },
                {
                    "step": 5,
                    "operation": "Write Register",
                    "source_function": "init_port",
                    "target_register": "UART_LCR",
                    "offset": "0x03",
                    "value_or_mask": "UART_LCR_DLAB",
                    "description": "Set DLAB bit to expose baud rate divisor latch registers",
                },
            ],
        },
        "e1000": {
            "device_name": "Intel PRO/1000 Gigabit Ethernet Controller",
            "driver_name": "e1000",
            "compatibility": [],
            "hardware_info": {
                "bus_type": "pci",
                "vendor_ids": ["0x8086"],
                "device_ids": ["0x100E", "0x100F"],
                "base_address_offsets": [
                    {
                        "region_id": "BAR0",
                        "name": "E1000_MMIO_BAR0",
                        "base_offset": "0x00000",
                        "description": "Primary MMIO register space mapped via pci_iomap(pdev, 0, 0)",
                    }
                ],
                "endianness": "little",
            },
            "matching_tables": {
                "pci_device_id": [
                    {"vendor_id": "0x8086", "device_id": "0x100E"},
                    {"vendor_id": "0x8086", "device_id": "0x100F"},
                ]
            },
            "lifecycle": {
                "probe_function": "e1000_probe",
                "remove_function": "e1000_remove",
            },
            "register_map": [
                {
                    "name": "E1000_CTRL",
                    "offset": "0x00000",
                    "width": 32,
                    "access": "RW",
                    "description": "Device Control Register",
                    "bitfields": [
                        {"name": "E1000_CTRL_SLU", "bits": "6", "mask_macro": "BIT(6)"},
                        {"name": "E1000_CTRL_RST", "bits": "26", "mask_macro": "BIT(26)"},
                    ],
                },
                {
                    "name": "E1000_STATUS",
                    "offset": "0x00008",
                    "width": 32,
                    "access": "RO",
                    "description": "Device Status Register",
                    "bitfields": [
                        {"name": "E1000_STATUS_LU", "bits": "1", "mask_macro": "BIT(1)"},
                    ],
                },
                {
                    "name": "E1000_ICR",
                    "offset": "0x000C0",
                    "width": 32,
                    "access": "RO",
                    "description": "Interrupt Cause Read Register (Read-to-Clear)",
                },
                {
                    "name": "E1000_IMS",
                    "offset": "0x000D0",
                    "width": 32,
                    "access": "RW",
                    "description": "Interrupt Mask Set/Read Register",
                },
                {
                    "name": "E1000_IMC",
                    "offset": "0x000D8",
                    "width": 32,
                    "access": "WO",
                    "description": "Interrupt Mask Clear Register",
                },
                {
                    "name": "E1000_TDT",
                    "offset": "0x03818",
                    "offset_expression": "0x03818 + (n * 0x100)",
                    "width": 32,
                    "access": "RW",
                    "description": "Transmit Descriptor Tail",
                },
            ],
            "init_sequence": [
                {
                    "step": 1,
                    "operation": "MMIO Map",
                    "source_function": "e1000_probe",
                    "value_or_mask": "BAR0",
                    "description": "Map PCI BAR0 MMIO register space via pci_iomap",
                },
                {
                    "step": 2,
                    "operation": "Write Register",
                    "source_function": "e1000_probe",
                    "target_register": "E1000_IMC",
                    "offset": "0x000D8",
                    "value_or_mask": "0xffffffff",
                    "description": "Mask all hardware interrupts prior to device reset",
                },
                {
                    "step": 3,
                    "operation": "Hardware Reset",
                    "source_function": "e1000_probe",
                    "target_register": "E1000_CTRL",
                    "offset": "0x00000",
                    "value_or_mask": "E1000_CTRL_RST",
                    "description": "Trigger global MAC reset via E1000_CTRL_RST",
                },
                {
                    "step": 4,
                    "operation": "Read & Poll",
                    "source_function": "e1000_probe",
                    "target_register": "E1000_STATUS",
                    "offset": "0x00008",
                    "value_or_mask": "E1000_STATUS_LU",
                    "poll_condition": "(status & E1000_STATUS_LU) != 0",
                    "description": "Check physical link-up status in E1000_STATUS",
                },
                {
                    "step": 5,
                    "operation": "DMA Setup",
                    "source_function": "e1000_probe",
                    "value_or_mask": "4096",
                    "description": "Allocate coherent DMA descriptor ring buffer",
                },
                {
                    "step": 6,
                    "operation": "Interrupt Hook",
                    "source_function": "e1000_probe",
                    "value_or_mask": "e1000_intr (IRQF_SHARED)",
                    "description": "Register shared PCI interrupt handler e1000_intr",
                },
                {
                    "step": 7,
                    "operation": "Write Register",
                    "source_function": "e1000_probe",
                    "target_register": "E1000_IMS",
                    "offset": "0x000D0",
                    "value_or_mask": "E1000_IMS_RXT0 | E1000_IMS_TXDW",
                    "description": "Unmask RX timer and TX descriptor write-back interrupts",
                },
            ],
            "runtime_operations": {
                "probe_handler": "e1000_probe",
                "remove_handler": "e1000_remove",
                "transmit_handler": {
                    "operation_name": "ndo_start_xmit",
                    "c_function": "e1000_xmit_frame",
                    "registers_touched": ["E1000_TDT"],
                },
                "dma_setup": {
                    "allocation_api": "dma_alloc_coherent",
                    "address_mask_bits": 64,
                    "descriptor_rings": ["TX descriptor ring (TDT @ 0x03818)"],
                    "coherency_notes": "Issue write barrier before updating E1000_TDT tail index.",
                },
                "interrupt_lifecycle": {
                    "irq_handler": "e1000_intr",
                    "registration_api": "request_irq",
                    "irq_flags": ["IRQF_SHARED"],
                    "status_register": "E1000_ICR",
                    "ack_register": "E1000_ICR",
                    "mask_register": "E1000_IMC",
                    "ack_mechanism": "Read-to-clear E1000_ICR followed by masking E1000_IMC for NAPI polling",
                    "flow_steps": [
                        {
                            "step": 1,
                            "operation": "Read Register",
                            "source_function": "e1000_intr",
                            "target_register": "E1000_ICR",
                            "offset": "0x000C0",
                            "description": "Read and clear pending interrupt causes from E1000_ICR",
                        },
                        {
                            "step": 2,
                            "operation": "Write Register",
                            "source_function": "e1000_intr",
                            "target_register": "E1000_IMC",
                            "offset": "0x000D8",
                            "value_or_mask": "0xffffffff",
                            "description": "Mask further interrupts during bottom-half packet processing",
                        },
                    ],
                },
            },
            "concurrency_locks": [
                {
                    "linux_primitive": "spin_lock_irqsave(&adapter->tx_lock, flags)",
                    "abstract_lock_type": "IRQSafeSpinlock",
                    "protected_resources": ["TX descriptor ring", "E1000_TDT register"],
                    "bare_metal_recommendation": "Use IRQ-disabling ticket spinlock around TX ring updates.",
                }
            ],
        },
        "virtio_mmio": {
            "device_name": "VirtIO MMIO Transport",
            "driver_name": "virtio_mmio",
            "compatibility": ["virtio,mmio"],
            "hardware_info": {
                "bus_type": "platform",
                "compatible_strings": ["virtio,mmio"],
                "base_address_offsets": [
                    {
                        "region_id": "0",
                        "name": "VIRTIO_MMIO_REGS",
                        "base_offset": "0x000",
                        "size_hint": "0x200",
                        "description": "VirtIO MMIO control and virtqueue configuration registers",
                    }
                ],
                "endianness": "little",
            },
            "lifecycle": {
                "probe_function": "virtio_mmio_probe",
                "remove_function": "virtio_mmio_remove",
            },
            "register_map": [
                {
                    "name": "VIRTIO_MMIO_MAGIC_VALUE",
                    "offset": "0x000",
                    "width": 32,
                    "access": "RO",
                    "reset_value": "0x74726976",
                    "description": "Magic value ('virt' in little-endian)",
                },
                {
                    "name": "VIRTIO_MMIO_VERSION",
                    "offset": "0x004",
                    "width": 32,
                    "access": "RO",
                    "description": "Device version number",
                },
                {
                    "name": "VIRTIO_MMIO_DEVICE_ID",
                    "offset": "0x008",
                    "width": 32,
                    "access": "RO",
                    "description": "Virtio Subsystem Device ID",
                },
                {
                    "name": "VIRTIO_MMIO_QUEUE_NOTIFY",
                    "offset": "0x050",
                    "width": 32,
                    "access": "WO",
                    "description": "Queue notifier register",
                },
                {
                    "name": "VIRTIO_MMIO_INTERRUPT_STATUS",
                    "offset": "0x060",
                    "width": 32,
                    "access": "RO",
                    "description": "Interrupt status flags",
                    "bitfields": [
                        {"name": "VIRTIO_MMIO_INT_VRING", "bits": "0", "mask_macro": "BIT(0)"},
                        {"name": "VIRTIO_MMIO_INT_CONFIG", "bits": "1", "mask_macro": "BIT(1)"},
                    ],
                },
                {
                    "name": "VIRTIO_MMIO_INTERRUPT_ACK",
                    "offset": "0x064",
                    "width": 32,
                    "access": "WO",
                    "description": "Interrupt acknowledge register (Write-1-to-Clear)",
                },
                {
                    "name": "VIRTIO_MMIO_STATUS",
                    "offset": "0x070",
                    "width": 32,
                    "access": "RW",
                    "description": "Device status bitmask",
                },
            ],
            "init_sequence": [
                {
                    "step": 1,
                    "operation": "MMIO Map",
                    "source_function": "virtio_mmio_probe",
                    "value_or_mask": "resource 0",
                    "description": "Map platform MMIO memory region via devm_platform_ioremap_resource",
                },
                {
                    "step": 2,
                    "operation": "Read Register",
                    "source_function": "virtio_mmio_probe",
                    "target_register": "VIRTIO_MMIO_MAGIC_VALUE",
                    "offset": "0x000",
                    "value_or_mask": "0x74726976",
                    "description": "Verify 'virt' magic signature",
                },
                {
                    "step": 3,
                    "operation": "Hardware Reset",
                    "source_function": "virtio_mmio_probe",
                    "target_register": "VIRTIO_MMIO_STATUS",
                    "offset": "0x070",
                    "value_or_mask": "0x0",
                    "description": "Reset VirtIO device by writing 0 to VIRTIO_MMIO_STATUS",
                },
                {
                    "step": 4,
                    "operation": "Write Register",
                    "source_function": "virtio_mmio_probe",
                    "target_register": "VIRTIO_MMIO_STATUS",
                    "offset": "0x070",
                    "value_or_mask": "VIRTIO_CONFIG_S_ACKNOWLEDGE | VIRTIO_CONFIG_S_DRIVER",
                    "description": "Set ACKNOWLEDGE and DRIVER status bits",
                },
                {
                    "step": 5,
                    "operation": "Interrupt Hook",
                    "source_function": "virtio_mmio_probe",
                    "value_or_mask": "vm_interrupt (IRQF_SHARED)",
                    "description": "Bind platform IRQ handler vm_interrupt",
                },
                {
                    "step": 6,
                    "operation": "Write Register",
                    "source_function": "virtio_mmio_probe",
                    "target_register": "VIRTIO_MMIO_STATUS",
                    "offset": "0x070",
                    "value_or_mask": "VIRTIO_CONFIG_S_ACKNOWLEDGE | VIRTIO_CONFIG_S_DRIVER | VIRTIO_CONFIG_S_DRIVER_OK",
                    "description": "Signal DRIVER_OK to transition device live",
                },
            ],
            "runtime_operations": {
                "probe_handler": "virtio_mmio_probe",
                "remove_handler": "virtio_mmio_remove",
                "interrupt_lifecycle": {
                    "irq_handler": "vm_interrupt",
                    "registration_api": "devm_request_irq",
                    "irq_flags": ["IRQF_SHARED"],
                    "status_register": "VIRTIO_MMIO_INTERRUPT_STATUS",
                    "ack_register": "VIRTIO_MMIO_INTERRUPT_ACK",
                    "ack_mechanism": "Write-1-to-clear VIRTIO_MMIO_INTERRUPT_ACK with value read from VIRTIO_MMIO_INTERRUPT_STATUS",
                    "flow_steps": [
                        {
                            "step": 1,
                            "operation": "Read Register",
                            "source_function": "vm_interrupt",
                            "target_register": "VIRTIO_MMIO_INTERRUPT_STATUS",
                            "offset": "0x060",
                            "description": "Read active VirtIO interrupt bitmap",
                        },
                        {
                            "step": 2,
                            "operation": "Write Register",
                            "source_function": "vm_interrupt",
                            "target_register": "VIRTIO_MMIO_INTERRUPT_ACK",
                            "offset": "0x064",
                            "value_or_mask": "status",
                            "description": "Acknowledge serviced interrupt bits in VIRTIO_MMIO_INTERRUPT_ACK",
                        },
                    ],
                },
            },
            "concurrency_locks": [
                {
                    "linux_primitive": "spin_lock_init(&vm_dev->lock)",
                    "abstract_lock_type": "IRQSafeSpinlock",
                    "protected_resources": ["Virtqueue list", "VIRTIO_MMIO_QUEUE_SEL"],
                    "bare_metal_recommendation": "Guard queue selector and vring updates with an IRQ-safe spinlock.",
                }
            ],
        },
    }


def _load_driver_source(rel_kernel_path: str, fallback_code: str) -> tuple[str, str]:
    """Loads from LINUX_KERNEL_TREE if configured on disk, otherwise uses embedded reference source."""
    kernel_root = os.getenv("LINUX_KERNEL_TREE")
    if kernel_root:
        candidate = Path(kernel_root) / rel_kernel_path
        if candidate.exists():
            return (rel_kernel_path, candidate.read_text(encoding="utf-8", errors="replace"))
    return (rel_kernel_path, fallback_code)


def test_canonical_json_schema_draft_2020_12():
    schema_str = export_json_schema()
    schema = json.loads(schema_str)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert "device_name" in schema["properties"]
    assert "compatibility" in schema["properties"]
    assert "register_map" in schema["properties"]
    assert "init_sequence" in schema["properties"]


@pytest.mark.parametrize(
    "driver_key,kernel_path,fallback_source",
    [
        ("8250_early", "drivers/tty/serial/8250/8250_early.c", UART_8250_EARLY_C),
        ("e1000", "drivers/net/ethernet/intel/e1000/e1000_main.c", E1000_MAIN_C),
        ("virtio_mmio", "drivers/virtio/virtio_mmio.c", VIRTIO_MMIO_C),
    ],
)
def test_end_to_end_driver_extraction_and_verification(
    driver_key: str,
    kernel_path: str,
    fallback_source: str,
):
    filename, source_code = _load_driver_source(kernel_path, fallback_source)

    # Task 1: Tree-sitter C AST pre-processing & boilerplate stripping
    ast_result = parse_driver_ast([(filename, source_code)], strip_boilerplate=True)
    assert len(ast_result.macros) > 0
    assert len(ast_result.mmio_operations) > 0
    assert "MODULE_LICENSE" not in ast_result.pruned_source.split("*/", 1)[1]

    # Task 2 & Task 3: Constrained LLM extraction + self-healing retry validation
    mock_payloads = _mock_structured_specs()
    attempt_counter = {"count": 0}

    def simulated_constrained_llm(_system_prompt: str, _user_prompt: str) -> str:
        attempt_counter["count"] += 1
        # Simulate malformed JSON on first attempt to verify automatic schema retry recovery
        if attempt_counter["count"] == 1:
            return '{"driver_name": "broken", "init_sequence": "not_an_array"}'
        return json.dumps(mock_payloads[driver_key])

    extractor = DriverExtractor(custom_generator=simulated_constrained_llm)
    spec = extractor.extract(
        code_context=ast_result.pruned_source,
        ast_result=ast_result,
    )
    assert attempt_counter["count"] == 2
    assert isinstance(spec, DriverSpec)

    # Task 4: Markdown & Mermaid specification generation
    markdown_doc = generate_markdown_spec(spec)
    assert "```mermaid" in markdown_doc
    assert "sequenceDiagram" in markdown_doc
    assert "## 2. Register Memory Map" in markdown_doc
    assert "## 5. C++ Microkernel Migration Notes" in markdown_doc

    # Task 5: Full verification harness (Schema + AST Determinism + Sequence Parity + Markdown)
    schema_findings = validate_schema_compliance(spec.model_dump_json())
    assert all(f.passed for f in schema_findings)

    report = run_full_verification(
        spec=spec,
        ast_result=ast_result,
        markdown_text=markdown_doc,
        strict=True,
    )
    assert report.passed is True
    assert report.schema_compliant is True
    assert report.register_deterministic is True
    assert report.sequence_parity is True
    assert report.markdown_complete is True