import sys
import warnings
from pathlib import Path
from typing import Optional
import click

from .core.doc_generator import generate_markdown_spec
from .core.serializer import serialize_spec
from .core.validator import run_full_verification, validate_schema_compliance
from .exceptions import KernelitePrismError
from .models.base import DriverSpec, export_json_schema

DEFAULT_CLI_MODEL = "Qwen/Qwen2.5-Coder-3B-Instruct"


@click.group()
@click.version_option(package_name="kernelite-prism")
def main() -> None:
    """Kernelite Prism: Extract structured hardware & C++ microkernel specs from Linux driver C code."""


@main.command()
@click.argument("target_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["json", "yaml", "md"], case_sensitive=False),
    default="json",
    help="Output format serialization (json, yaml, or md).",
)
@click.option(
    "--out",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output file path for driver_spec.json (or YAML). Prints to stdout if omitted.",
)
@click.option(
    "--md-out",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional output path to simultaneously emit human-readable DRIVER_SPEC.md.",
)
@click.option(
    "--model",
    default=DEFAULT_CLI_MODEL,
    help=f"Model ID or local path (default: {DEFAULT_CLI_MODEL}).",
)
@click.option(
    "--backend",
    type=click.Choice(["transformers", "outlines", "openai", "instructor"], case_sensitive=False),
    default="transformers",
    help="Structured generation backend.",
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
@click.option(
    "--no-ast-prune",
    is_flag=True,
    default=False,
    help="Disable AST boilerplate stripping before LLM submission.",
)
def analyze(
    target_path: Path,
    output_format: str,
    out: Optional[Path],
    md_out: Optional[Path],
    model: str,
    backend: str,
    quant: str,
    device: str,
    token: Optional[str],
    no_ast_prune: bool,
) -> None:
    """Analyze a target C driver file or directory and produce machine and human specifications."""
    try:
        from .config import get_settings
        from .core.aggregator import aggregate_with_ast
        from .core.extractor import DriverExtractor

        settings = get_settings(
            model=model,
            device=device,
            quantization=quant,
            backend=backend,
            hf_token=token,
        )

        click.echo(f"Parsing C AST and aggregating context from '{target_path}'...", err=True)
        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always", UserWarning)
            code_context, ast_result = aggregate_with_ast(
                target_path,
                use_ast_pruning=not no_ast_prune,
            )

        for w in caught_warnings:
            click.echo(f"Warning: {w.message}", err=True)

        click.echo(
            f"Extracting specification via {settings.backend} [{settings.model}]...",
            err=True,
        )
        extractor = DriverExtractor(
            model_name=settings.model,
            device=settings.device,
            quantization=settings.quantization,
            backend=settings.backend,
            hf_token=settings.hf_token,
        )
        spec = extractor.extract(code_context=code_context, ast_result=ast_result)

        md_doc = generate_markdown_spec(spec)
        run_full_verification(spec, ast_result=ast_result, markdown_text=md_doc, strict=False)

        serialized_output = serialize_spec(spec, format_type=output_format)

        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(serialized_output, encoding="utf-8")
            click.echo(f"Specification written to {out}", err=True)
        else:
            click.echo(serialized_output)

        if md_out:
            md_out.parent.mkdir(parents=True, exist_ok=True)
            md_out.write_text(md_doc, encoding="utf-8")
            click.echo(f"Markdown documentation written to {md_out}", err=True)

    except KernelitePrismError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


@main.command("render-md")
@click.argument("spec_json", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--out",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output Markdown file path (e.g., DRIVER_SPEC.md). Prints to stdout if omitted.",
)
def render_md(spec_json: Path, out: Optional[Path]) -> None:
    """Render a validated driver_spec.json file into DRIVER_SPEC.md."""
    try:
        spec = DriverSpec.model_validate_json(spec_json.read_text(encoding="utf-8"))
        md_content = generate_markdown_spec(spec)
        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md_content, encoding="utf-8")
            click.echo(f"Markdown specification written to {out}", err=True)
        else:
            click.echo(md_content)
    except Exception as e:
        click.echo(f"Error rendering Markdown: {e}", err=True)
        sys.exit(1)


@main.command("validate")
@click.argument("spec_json", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--schema",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Optional path to custom driver_spec.schema.json.",
)
def validate_cmd(spec_json: Path, schema: Optional[Path]) -> None:
    """Validate a driver_spec.json artifact against driver_spec.schema.json."""
    findings = validate_schema_compliance(
        spec_json.read_text(encoding="utf-8"),
        schema_path=schema,
    )
    all_ok = all(f.passed for f in findings)
    for finding in findings:
        prefix = "PASS" if finding.passed else "FAIL"
        click.echo(f"[{prefix}] {finding.check}: {finding.message}")
    if not all_ok:
        sys.exit(1)


@main.command("export-schema")
@click.option(
    "--out",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output path for driver_spec.schema.json.",
)
def export_schema_cmd(out: Optional[Path]) -> None:
    """Export the canonical JSON Schema Draft 2020-12 (driver_spec.schema.json)."""
    schema_text = export_json_schema(indent=2)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(schema_text + "\n", encoding="utf-8")
        click.echo(f"JSON Schema written to {out}", err=True)
    else:
        click.echo(schema_text)


if __name__ == "__main__":
    main()
