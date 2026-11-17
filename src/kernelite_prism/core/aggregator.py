from pathlib import Path
from typing import List, Optional, Tuple, Union
import warnings
from ..exceptions import AggregationError
from .ast_parser import ASTExtractionResult, ASTPreProcessor

DEFAULT_TOKEN_WARN_THRESHOLD = 24_000
DEFAULT_VRAM_SAFE_TOKEN_BUDGET = 16_000
CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_tokens(text: str) -> int:
    """Estimates LLM token count using a conservative character-to-token heuristic."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


def collect_driver_files(
    target_path: Union[str, Path],
    extra_headers: Optional[List[Union[str, Path]]] = None,
) -> List[Tuple[str, str]]:
    """Collects `(relative_name, file_content)` pairs for `.c` and matching/explicit `.h` files."""
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
        # Only include same-stem sibling headers (e.g. qemu_fw_cfg.h), not all headers in drivers/net/
        for sibling_hdr in sorted(path.parent.glob(f"{path.stem}*.h")):
            if sibling_hdr not in files_to_process:
                files_to_process.append(sibling_hdr)
    elif path.is_dir():
        files_to_process = sorted(
            [f for f in path.rglob("*") if f.is_file() and f.suffix in [".c", ".h"]]
        )

    if extra_headers:
        for hdr in extra_headers:
            hdr_path = Path(hdr)
            if not hdr_path.exists():
                raise AggregationError(f"Included header '{hdr_path}' does not exist.")
            if hdr_path not in files_to_process:
                files_to_process.append(hdr_path)

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
                if path.is_dir() and file_path.is_relative_to(path)
                else file_path.name
            )
            collected.append((relative_name, content))
        except Exception as e:
            raise AggregationError(
                f"Failed to read file '{file_path}': {e}"
            ) from e

    return collected


def _compact_ast_context(ast_result: ASTExtractionResult, max_chars: int) -> str:
    """Builds a token-bounded context prioritizing AST ground-truth macros, structs, and key functions."""
    sections: List[str] = []

    # Always preserve the AST summary header (macros, hex offsets, structs, entry points)
    header_end = ast_result.pruned_source.find("*/")
    if header_end != -1:
        sections.append(ast_result.pruned_source[: header_end + 2])

    for s in ast_result.driver_structs:
        if s.raw_snippet:
            sections.append(f"/* Struct: {s.struct_type} ({s.file}) */\n{s.raw_snippet}")

    priority_order = {"probe": 0, "irq_handler": 1, "init": 2, "xmit": 3, "io_op": 4, "remove": 5, "helper": 6}
    ranked_funcs = sorted(
        [fn for fn in ast_result.functions if fn.role != "helper" or fn.mmio_calls or fn.irq_calls or fn.dma_calls],
        key=lambda fn: (priority_order.get(fn.role, 9), -len(fn.mmio_calls)),
    )

    used_chars = sum(len(s) for s in sections)
    for fn in ranked_funcs:
        block = f"/* [{fn.role.upper()}] {fn.file} */\n{fn.source_code}"
        if used_chars + len(block) + 4 > max_chars:
            continue
        sections.append(block)
        used_chars += len(block) + 4

    return "\n\n".join(sections)


def aggregate_with_ast(
    target_path: Union[str, Path],
    use_ast_pruning: bool = True,
    warn_token_threshold: int = DEFAULT_TOKEN_WARN_THRESHOLD,
    max_tokens: Optional[int] = DEFAULT_VRAM_SAFE_TOKEN_BUDGET,
    extra_headers: Optional[List[Union[str, Path]]] = None,
) -> Tuple[str, ASTExtractionResult]:
    """Aggregates C source files, parses AST primitives, and returns `(context_str, ast_result)`."""
    path = Path(target_path)
    collected_files = collect_driver_files(path, extra_headers=extra_headers)

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
        warnings.warn(
            f"Context from '{path}' (~{estimated_tokens:,} tokens) exceeds {max_tokens:,}-token budget; "
            f"compacting to AST macros, driver structs, and MMIO/IRQ/probe functions.",
            UserWarning,
            stacklevel=2,
        )
        aggregated = _compact_ast_context(ast_result, max_chars=max_tokens * CHARS_PER_TOKEN_ESTIMATE)
        estimated_tokens = estimate_tokens(aggregated)

    if estimated_tokens > warn_token_threshold:
        warnings.warn(
            f"Aggregated context from '{path}' spans {len(collected_files)} file(s) "
            f"(~{estimated_tokens:,} estimated tokens), exceeding the recommended threshold "
            f"of {warn_token_threshold:,} tokens.",
            UserWarning,
            stacklevel=2,
        )

    return aggregated, ast_result


def aggregate_context(
    target_path: Union[str, Path],
    warn_token_threshold: int = DEFAULT_TOKEN_WARN_THRESHOLD,
    max_tokens: Optional[int] = DEFAULT_VRAM_SAFE_TOKEN_BUDGET,
    use_ast_pruning: bool = True,
    extra_headers: Optional[List[Union[str, Path]]] = None,
) -> str:
    """Recursively collects and aggregates C source and header files into a formatted string context."""
    aggregated, _ = aggregate_with_ast(
        target_path=target_path,
        use_ast_pruning=use_ast_pruning,
        warn_token_threshold=warn_token_threshold,
        max_tokens=max_tokens,
        extra_headers=extra_headers,
    )
    return aggregated
