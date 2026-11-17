from __future__ import annotations

import ast
import operator
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple, Union
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from tree_sitter import Language, Node, Parser

try:
    import tree_sitter_c as tsc
    from tree_sitter import Language as _Language, Parser as _Parser

    C_LANGUAGE: Optional[Language] = _Language(tsc.language())
    _TREE_SITTER_IMPORT_ERROR: Optional[Exception] = None
except ImportError as exc:
    tsc = None
    _Language = None
    _Parser = None
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
        if C_LANGUAGE is None or _Parser is None:
            raise ASTParsingError(
                "tree-sitter and tree-sitter-c are required. Install with: pip install 'tree-sitter>=0.23.0' 'tree-sitter-c>=0.23.0'"
            ) from _TREE_SITTER_IMPORT_ERROR
        self.strip_boilerplate = strip_boilerplate
        self.parser = _Parser(C_LANGUAGE)

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
            for prefix in ("REG_", "_REG", "UART_", "UART0", "GIC_", "FW_CFG_", "VIRTIO_", "E1000_", "CSR_", "MMIO_")
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

        offset_macros = [m for m in macros if m.category == "register_offset"]
        mask_macros = [m for m in macros if m.category == "bitmask"]
        reg_macros = offset_macros + mask_macros
        if reg_macros:
            lines.append(" *")
            lines.append(" * Extracted Register & Bitmask Macros:")
            for m in reg_macros[:250]:
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