"""Core modules for AST pre-processing, context aggregation, LLM extraction, validation, and documentation."""

from .ast_parser import (
    ASTExtractionResult,
    ASTPreProcessor,
    ConcurrencyCall,
    DriverStructInfo,
    FunctionSummary,
    MMIOCall,
    MacroConstant,
    parse_driver_ast,
    safe_eval_c_constant,
)
from .aggregator import (
    aggregate_context,
    aggregate_with_ast,
    collect_driver_files,
    estimate_tokens,
)
from .extractor import DriverExtractor, extract_driver_spec, ground_spec_with_ast
from .doc_generator import generate_markdown_spec
from .serializer import serialize_spec
from .validator import (
    ValidationFinding,
    VerificationReport,
    load_driver_spec_schema,
    run_full_verification,
    validate_schema_compliance,
    verify_markdown_completeness,
    verify_register_determinism,
    verify_sequence_parity,
)

__all__ = [
    "ASTExtractionResult",
    "ASTPreProcessor",
    "ConcurrencyCall",
    "DriverStructInfo",
    "FunctionSummary",
    "MMIOCall",
    "MacroConstant",
    "parse_driver_ast",
    "safe_eval_c_constant",
    "aggregate_context",
    "aggregate_with_ast",
    "collect_driver_files",
    "estimate_tokens",
    "DriverExtractor",
    "extract_driver_spec",
    "ground_spec_with_ast",
    "generate_markdown_spec",
    "serialize_spec",
    "ValidationFinding",
    "VerificationReport",
    "load_driver_spec_schema",
    "run_full_verification",
    "validate_schema_compliance",
    "verify_markdown_completeness",
    "verify_register_determinism",
    "verify_sequence_parity",
]