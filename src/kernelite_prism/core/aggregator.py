from pathlib import Path
from typing import List, Optional, Union
import warnings
from ..exceptions import AggregationError

# Reserve ~8k tokens for system prompt, JSON schema, and 4096 generated output tokens
# within Qwen2.5-Coder's 32k context window.
DEFAULT_TOKEN_WARN_THRESHOLD = 24_000
CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_tokens(text: str) -> int:
    """Estimates LLM token count using a conservative character-to-token heuristic."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


def aggregate_context(
    target_path: Union[str, Path],
    warn_token_threshold: int = DEFAULT_TOKEN_WARN_THRESHOLD,
    max_tokens: Optional[int] = None,
) -> str:
    """Recursively collects and aggregates C source and header files into a formatted string context."""
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
    elif path.is_dir():
        # Collect all C source and header files under directory recursively
        files_to_process = sorted(
            [f for f in path.rglob("*") if f.is_file() and f.suffix in [".c", ".h"]]
        )

    if not files_to_process:
        raise AggregationError(
            f"No .c or .h source files found in target path '{path}'."
        )

    context_blocks: List[str] = []

    for file_path in files_to_process:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            relative_name = (
                file_path.relative_to(path)
                if path.is_dir()
                else file_path.name
            )
            block = (
                f"/* =========================================================================\n"
                f" * File: {relative_name}\n"
                f" * ========================================================================= */\n\n"
                f"{content}"
            )
            context_blocks.append(block)
        except Exception as e:
            raise AggregationError(
                f"Failed to read file '{file_path}': {e}"
            ) from e

    aggregated = "\n\n".join(context_blocks)
    estimated_tokens = estimate_tokens(aggregated)

    if max_tokens is not None and estimated_tokens > max_tokens:
        raise AggregationError(
            f"Aggregated context from '{path}' ({len(files_to_process)} files, "
            f"~{estimated_tokens:,} tokens) exceeds the configured limit of {max_tokens:,} tokens."
        )

    if estimated_tokens > warn_token_threshold:
        warnings.warn(
            f"Aggregated context from '{path}' spans {len(files_to_process)} file(s) "
            f"(~{estimated_tokens:,} estimated tokens), exceeding the recommended threshold "
            f"of {warn_token_threshold:,} tokens. Large multi-file contexts may cause context "
            f"truncation, degraded accuracy, or GPU OOM errors.",
            UserWarning,
            stacklevel=2,
        )

    return aggregated