import os
from typing import Optional
import torch
from .exceptions import ConfigurationError

DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-3B-Instruct"
VALID_QUANTIZATIONS = {"4bit", "8bit", "none"}
VALID_BACKENDS = {"transformers", "outlines", "openai", "instructor"}


class Settings:
    """Holds configuration state for inference backend and model selection."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        device: str = "cuda",
        quantization: str = "4bit",
        backend: str = "transformers",
        hf_token: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        openai_base_url: Optional[str] = None,
    ):
        self.model: str = model
        self.device: str = device
        self.quantization: str = quantization.lower()
        self.backend: str = backend.lower()
        self.hf_token: Optional[str] = (
            hf_token or os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
        )
        self.openai_api_key: Optional[str] = openai_api_key or os.getenv("OPENAI_API_KEY")
        self.openai_base_url: Optional[str] = openai_base_url or os.getenv("OPENAI_BASE_URL")

    def validate(self) -> None:
        if self.backend not in VALID_BACKENDS:
            raise ConfigurationError(
                f"Unsupported backend '{self.backend}'. "
                f"Choose from: {', '.join(sorted(VALID_BACKENDS))}."
            )

        if self.backend in {"transformers", "outlines"}:
            if self.quantization not in VALID_QUANTIZATIONS:
                raise ConfigurationError(
                    f"Unsupported quantization '{self.quantization}'. "
                    f"Choose from: {', '.join(sorted(VALID_QUANTIZATIONS))}."
                )

            is_cuda = self.device.startswith("cuda")

            if is_cuda and not torch.cuda.is_available():
                raise ConfigurationError(
                    f"Device '{self.device}' was requested, but CUDA is not available. "
                    "Ensure an NVIDIA GPU with appropriate CUDA drivers and PyTorch are installed, "
                    "or pass '--device cpu --quant none'."
                )

            if not is_cuda and self.quantization != "none":
                raise ConfigurationError(
                    f"Quantization '{self.quantization}' is only supported on CUDA devices, "
                    f"got device '{self.device}'. Pass '--quant none' for non-CUDA execution."
                )


def get_settings(
    model: str = DEFAULT_MODEL,
    device: str = "cuda",
    quantization: str = "4bit",
    backend: str = "transformers",
    hf_token: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    openai_base_url: Optional[str] = None,
) -> Settings:
    """Factory function to fetch and validate settings."""
    settings = Settings(
        model=model,
        device=device,
        quantization=quantization,
        backend=backend,
        hf_token=hf_token,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
    )
    settings.validate()
    return settings