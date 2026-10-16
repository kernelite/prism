import os
from typing import Optional
import torch
from .exceptions import ConfigurationError

# Default to a high-capability open-weights code model
DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"


class Settings:
    """Holds configuration state for local GPU inference and model selection."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        device: str = "cuda",
        hf_token: Optional[str] = None,
    ):
        self.model: str = model
        self.device: str = device
        self.hf_token: Optional[str] = (
            hf_token or os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
        )

    def validate(self) -> None:
        if self.device.startswith("cuda") and not torch.cuda.is_available():
            raise ConfigurationError(
                f"Device '{self.device}' was requested, but CUDA is not available. "
                "Ensure an NVIDIA GPU with appropriate CUDA drivers and PyTorch are installed."
            )


def get_settings(
    model: str = DEFAULT_MODEL,
    device: str = "cuda",
    hf_token: Optional[str] = None,
) -> Settings:
    """Factory function to fetch and validate settings."""
    settings = Settings(model=model, device=device, hf_token=hf_token)
    settings.validate()
    return settings