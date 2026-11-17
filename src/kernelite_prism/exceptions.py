class KernelitePrismError(Exception):
    """Base exception class for Kernelite Prism errors."""


class ConfigurationError(KernelitePrismError):
    """Raised when environment variables, hardware devices, or options are misconfigured."""


class AggregationError(KernelitePrismError):
    """Raised when context aggregation fails (e.g., path not found or unreadable)."""


class ASTParsingError(KernelitePrismError):
    """Raised when Tree-sitter C AST parsing encounters an unrecoverable failure."""


class ExtractionError(KernelitePrismError):
    """Raised when LLM inference or schema-enforced parsing fails."""


class SchemaValidationError(KernelitePrismError):
    """Raised when a driver specification fails JSON Schema or AST grounding verification."""


class SerializationError(KernelitePrismError):
    """Raised when output formatting/writing fails."""


class DocumentationError(KernelitePrismError):
    """Raised when Markdown / Mermaid documentation rendering fails."""