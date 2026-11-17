import sys
import warnings
from pathlib import Path
from typing import Optional
import click

from .config import DEFAULT_MODEL, get_settings
from .core.aggregator import aggregate_context
from .core.extractor import DriverExtractor
from .core.serializer import serialize_spec
from .exceptions import KernelitePrismError


@click.group()
@click.version_option(package_name="kernelite-prism")
def main() -> None:
    """Kernelite Prism: Extract structured specifications from Linux driver C code."""
    pass


@main.command()
@click.argument("target_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["json", "yaml"], case_sensitive=False),
    default="json",
    help="Output format serialization (json or yaml).",
)
@click.option(
    "--out",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output file path. Prints to stdout if omitted.",
)
@click.option(
    "--model",
    default=DEFAULT_MODEL,
    help=f"Hugging Face repository ID or local path (default: {DEFAULT_MODEL}).",
)
@click.option(
    "--quant",
    "-q",
    type=click.Choice(["4bit", "8bit", "none"], case_sensitive=False),
    default="4bit",
    help="Quantization precision (default: 4bit; use 'none' on CPU).",
)
@click.option(
    "--device",
    default="cuda",
    help="Execution device ('cuda', 'cuda:0', 'cpu').",
)
@click.option(
    "--token",
    default=None,
    help="Hugging Face API token.",
)
def analyze(
    target_path: Path,
    output_format: str,
    out: Optional[Path],
    model: str,
    quant: str,
    device: str,
    token: Optional[str],
) -> None:
    """Analyze a target C driver file or directory and produce a structured spec."""
    try:
        settings = get_settings(
            model=model,
            device=device,
            quantization=quant,
            hf_token=token,
        )

        click.echo(f"Aggregating C source context from '{target_path}'...", err=True)
        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always", UserWarning)
            code_context = aggregate_context(target_path)

        for w in caught_warnings:
            click.echo(f"Warning: {w.message}", err=True)

        click.echo(
            f"Extracting specification on {settings.device} [{settings.quantization}] using {settings.model}...",
            err=True,
        )
        extractor = DriverExtractor(
            model_name=settings.model,
            device=settings.device,
            quantization=settings.quantization,
            hf_token=settings.hf_token,
        )
        spec = extractor.extract(code_context=code_context)

        serialized_output = serialize_spec(spec, format_type=output_format)

        if out:
            out.write_text(serialized_output, encoding="utf-8")
            click.echo(f"Specification written to {out}", err=True)
        else:
            click.echo(serialized_output)

    except KernelitePrismError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()