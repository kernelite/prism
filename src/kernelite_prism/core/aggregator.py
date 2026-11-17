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