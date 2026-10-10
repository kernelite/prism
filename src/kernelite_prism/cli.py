import sys
from pathlib import Path
from typing import Optional
import click

from .config import get_settings
from .core.aggregator import aggregate_context
from .core.extractor import extract_driver_spec
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
    default="gemini-2.5-flash",
    help="Gemini LLM model override for extraction.",
)
def analyze(
    target_path: Path, output_format: str, out: Optional[Path], model: str
) -> None:
    """Analyze a target C driver file or directory and produce a structured spec."""
    try:
        settings = get_settings(model=model)

        click.echo(f"Aggregating C source context from '{target_path}'...", err=True)
        code_context = aggregate_context(target_path)

        click.echo(f"Extracting driver specification using {settings.model}...", err=True)
        spec = extract_driver_spec(
            code_context=code_context,
            api_key=settings.api_key,
            model_name=settings.model,
        )

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