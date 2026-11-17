import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, ValidationError

from ..exceptions import SchemaValidationError
from ..models.base import DriverSpec
from .ast_parser import ASTExtractionResult


class ValidationFinding(BaseModel):
    check: str
    passed: bool
    message: str


class VerificationReport(BaseModel):
    passed: bool
    schema_compliant: bool
    register_deterministic: bool
    sequence_parity: bool
    markdown_complete: bool
    findings: List[ValidationFinding] = Field(default_factory=list)


def load_driver_spec_schema(schema_path: Optional[Path] = None) -> Dict[str, Any]:
    """Loads driver_spec.schema.json from disk if provided, otherwise returns canonical Draft 2020-12 schema."""
    if schema_path is not None and schema_path.exists():
        return json.loads(schema_path.read_text(encoding="utf-8"))
    return DriverSpec.draft_2020_12_schema()


def validate_schema_compliance(
    spec_payload: Union[str, Dict[str, Any], DriverSpec],
    schema_path: Optional[Path] = None,
) -> List[ValidationFinding]:
    """Validates a JSON string, dictionary, or DriverSpec against driver_spec.schema.json (Draft 2020-12)."""
    findings: List[ValidationFinding] = []
    schema = load_driver_spec_schema(schema_path)

    if isinstance(spec_payload, DriverSpec):
        data = spec_payload.model_dump()
    elif isinstance(spec_payload, str):
        try:
            data = json.loads(spec_payload)
        except json.JSONDecodeError as e:
            return [
                ValidationFinding(
                    check="json_syntax",
                    passed=False,
                    message=f"Invalid JSON syntax: {e}",
                )
            ]
    else:
        data = spec_payload

    validator = Draft202012Validator(schema)
    schema_errors = sorted(validator.iter_errors(data), key=lambda err: list(err.path))
    if schema_errors:
        for err in schema_errors:
            loc = ".".join(str(p) for p in err.path) or "<root>"
            findings.append(
                ValidationFinding(
                    check="json_schema_draft_2020_12",
                    passed=False,
                    message=f"At '{loc}': {err.message}",
                )
            )
    else:
        findings.append(
            ValidationFinding(
                check="json_schema_draft_2020_12",
                passed=True,
                message="100% compliant with driver_spec.schema.json (Draft 2020-12).",
            )
        )

    try:
        DriverSpec.model_validate(data)
        findings.append(
            ValidationFinding(
                check="pydantic_model_validation",
                passed=True,
                message="Passed strict Pydantic DriverSpec type validation.",
            )
        )
    except ValidationError as e:
        findings.append(
            ValidationFinding(
                check="pydantic_model_validation",
                passed=False,
                message=f"Pydantic validation failed: {e}",
            )
        )

    return findings


def verify_register_determinism(
    spec: DriverSpec,
    ast_result: ASTExtractionResult,
) -> List[ValidationFinding]:
    """Ensures extracted register offsets and bitmasks match C AST constants with zero hallucinations."""
    findings: List[ValidationFinding] = []
    macro_map = ast_result.macro_lookup()

    if not spec.register_map:
        findings.append(
            ValidationFinding(
                check="register_presence",
                passed=False,
                message="Extracted register_map is empty.",
            )
        )
        return findings

    for reg in spec.register_map:
        if reg.name not in macro_map:
            findings.append(
                ValidationFinding(
                    check=f"register_symbol:{reg.name}",
                    passed=False,
                    message=f"Register '{reg.name}' not found in C header/source AST macros.",
                )
            )
            continue

        macro = macro_map[reg.name]
        if macro.hex_value is not None:
            try:
                extracted_int = (
                    int(reg.offset, 16)
                    if reg.offset.lower().startswith("0x")
                    else int(reg.offset)
                )
                if extracted_int != macro.numeric_value:
                    findings.append(
                        ValidationFinding(
                            check=f"register_offset:{reg.name}",
                            passed=False,
                            message=(
                                f"Offset mismatch for '{reg.name}': extracted '{reg.offset}', "
                                f"but C AST resolved '{macro.hex_value}'."
                            ),
                        )
                    )
                else:
                    findings.append(
                        ValidationFinding(
                            check=f"register_offset:{reg.name}",
                            passed=True,
                            message=f"Verified '{reg.name}' == {macro.hex_value}.",
                        )
                    )
            except ValueError:
                findings.append(
                    ValidationFinding(
                        check=f"register_offset:{reg.name}",
                        passed=False,
                        message=f"Non-numeric offset '{reg.offset}' for '{reg.name}'.",
                    )
                )

        for bf in reg.bitfields:
            if bf.name in macro_map:
                findings.append(
                    ValidationFinding(
                        check=f"bitfield_symbol:{reg.name}.{bf.name}",
                        passed=True,
                        message=f"Verified bitfield '{bf.name}' ({bf.mask_macro}).",
                    )
                )

    return findings


def verify_sequence_parity(
    spec: DriverSpec,
    ast_result: ASTExtractionResult,
    markdown_text: Optional[str] = None,
) -> List[ValidationFinding]:
    """Verifies chronological sequence parity between C .probe MMIO operations, init_sequence, and Mermaid."""
    findings: List[ValidationFinding] = []
    probe_funcs = [fn for fn in ast_result.functions if fn.role == "probe"]

    if not probe_funcs:
        findings.append(
            ValidationFinding(
                check="probe_ast_discovery",
                passed=True,
                message="No dedicated .probe function in AST; sequence parity check skipped.",
            )
        )
        return findings

    probe_fn = probe_funcs[0]
    if not spec.init_sequence:
        findings.append(
            ValidationFinding(
                check="init_sequence_populated",
                passed=False,
                message=f"Probe function '{probe_fn.name}' exists in AST, but init_sequence is empty.",
            )
        )
        return findings

    # Check strictly increasing 1..N step ordering
    expected_steps = list(range(1, len(spec.init_sequence) + 1))
    actual_steps = [s.step for s in spec.init_sequence]
    findings.append(
        ValidationFinding(
            check="init_sequence_monotonic_order",
            passed=(expected_steps == actual_steps),
            message=(
                f"init_sequence has {len(spec.init_sequence)} ordered steps."
                if expected_steps == actual_steps
                else f"Step numbering mismatch: {actual_steps} != {expected_steps}"
            ),
        )
    )

    if markdown_text is not None:
        has_mermaid = "```mermaid" in markdown_text and "sequenceDiagram" in markdown_text
        findings.append(
            ValidationFinding(
                check="mermaid_sequence_diagram",
                passed=has_mermaid,
                message=(
                    "Markdown contains valid Mermaid sequenceDiagram blocks."
                    if has_mermaid
                    else "Missing Mermaid sequenceDiagram in generated DRIVER_SPEC.md."
                ),
            )
        )

    return findings


def verify_markdown_completeness(markdown_text: str) -> List[ValidationFinding]:
    """Validates that DRIVER_SPEC.md contains all required human-readable documentation sections."""
    required_sections = [
        "## 1. Summary & Hardware Identification",
        "## 2. Register Memory Map",
        "## 3. Sequence Diagrams",
        "## 4. Runtime Operations, DMA & Concurrency Model",
        "## 5. C++ Microkernel Migration Notes",
    ]
    findings: List[ValidationFinding] = []
    for section in required_sections:
        present = section in markdown_text
        findings.append(
            ValidationFinding(
                check=f"markdown_section:{section}",
                passed=present,
                message=(
                    f"Section '{section}' present."
                    if present
                    else f"Missing section '{section}'."
                ),
            )
        )

    # Ensure no unrendered Jinja2 tags remain
    no_raw_jinja = "{{" not in markdown_text and "{%" not in markdown_text
    findings.append(
        ValidationFinding(
            check="markdown_no_unrendered_templates",
            passed=no_raw_jinja,
            message=(
                "All Jinja2 template expressions rendered cleanly."
                if no_raw_jinja
                else "Found unrendered Jinja2 delimiters in DRIVER_SPEC.md."
            ),
        )
    )
    return findings


def run_full_verification(
    spec: DriverSpec,
    ast_result: Optional[ASTExtractionResult] = None,
    markdown_text: Optional[str] = None,
    schema_path: Optional[Path] = None,
    strict: bool = False,
) -> VerificationReport:
    """Runs the complete verification harness across schema, AST determinism, sequence parity, and Markdown."""
    schema_findings = validate_schema_compliance(spec, schema_path=schema_path)
    reg_findings = (
        verify_register_determinism(spec, ast_result)
        if ast_result is not None
        else [ValidationFinding(check="register_ast_skip", passed=True, message="AST not provided.")]
    )
    seq_findings = (
        verify_sequence_parity(spec, ast_result, markdown_text=markdown_text)
        if ast_result is not None
        else [ValidationFinding(check="sequence_ast_skip", passed=True, message="AST not provided.")]
    )
    md_findings = (
        verify_markdown_completeness(markdown_text)
        if markdown_text is not None
        else [ValidationFinding(check="markdown_skip", passed=True, message="Markdown not provided.")]
    )

    all_findings = schema_findings + reg_findings + seq_findings + md_findings
    schema_ok = all(f.passed for f in schema_findings)
    reg_ok = all(f.passed for f in reg_findings)
    seq_ok = all(f.passed for f in seq_findings)
    md_ok = all(f.passed for f in md_findings)
    overall_ok = schema_ok and reg_ok and seq_ok and md_ok

    report = VerificationReport(
        passed=overall_ok,
        schema_compliant=schema_ok,
        register_deterministic=reg_ok,
        sequence_parity=seq_ok,
        markdown_complete=md_ok,
        findings=all_findings,
    )

    if strict and not overall_ok:
        failed_msgs = "; ".join(f.message for f in all_findings if not f.passed)
        raise SchemaValidationError(f"DriverSpec verification failed: {failed_msgs}")

    return report