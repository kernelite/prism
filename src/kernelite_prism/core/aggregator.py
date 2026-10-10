from pathlib import Path
from typing import List, Union
from ..exceptions import AggregationError


def aggregate_context(target_path: Path) -> str:
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
            # Read file with UTF-8, falling back gracefully to latin-1
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

    return "\n\n".join(context_blocks)