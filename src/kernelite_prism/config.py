import os
from typing import Optional
from .exceptions import ConfigurationError

DEFAULT_MODEL = "gemini-2.5-flash"


class Settings:
    """Holds configuration state for API keys and default model selection."""

    def __init__(self, api_key: Optional[str] = None, model: str = DEFAULT_MODEL):
        self.api_key: str = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model: str = model

    def validate(self) -> None:
        if not self.api_key:
            raise ConfigurationError(
                "GEMINI_API_KEY is not set. Please set the GEMINI_API_KEY "
                "environment variable or provide an API key."
            )


def get_settings(api_key: Optional[str] = None, model: str = DEFAULT_MODEL) -> Settings:
    """Factory function to fetch and validate settings."""
    settings = Settings(api_key=api_key, model=model)
    settings.validate()
    return settings