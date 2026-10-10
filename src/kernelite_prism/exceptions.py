class KernelitePrismError(Exception):
    """Base exception class for Kernelite Prism errors."""
    pass


class ConfigurationError(KernelitePrismError):
    """Raised when environment variables or options are misconfigured."""
    pass


class AggregationError(KernelitePrismError):
    """Raised when context aggregation fails (e.g., path not found or unreadable)."""
    pass


class ExtractionError(KernelitePrismError):
    """Raised when Gemini API call or Pydantic parsing fails."""
    pass


class SerializationError(KernelitePrismError):
    """Raised when output formatting/writing fails."""
    pass