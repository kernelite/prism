import json
import re
from typing import Any, Dict, Optional
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from pydantic import ValidationError

from ..config import DEFAULT_MODEL, VALID_QUANTIZATIONS
from ..exceptions import ConfigurationError, ExtractionError
from ..models.base import DriverSpec

SYSTEM_INSTRUCTION = (
    "You are an expert Linux kernel driver engineer and static code analysis assistant. "
    "Your objective is to thoroughly parse provided C source code (.c and .h files) "
    "and extract all structured driver metadata, matching tables (of_device_id, pci_device_id, "
    "usb_device_id, acpi_device_id, i2c_device_id, spi_device_id, platform_device_id, hid_device_id), "
    "module parameters, entry points/lifecycle callbacks (probe, remove, suspend, resume, shutdown, "
    "runtime PM, module_init/exit), file_operations mappings, sysfs/procfs/ioctl control surfaces, "
    "and register map bitfields.\n\n"
    "CRITICAL REQUIREMENT: Output must strictly be a single, valid JSON object satisfying "
    "the schema provided. Do not prepend or append markdown explanations, commentary, or text outside the JSON."
)


def _extract_json_text(raw_output: str) -> str:
    """Extracts raw JSON payload from markdown fences or bracket boundaries."""
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", raw_output)
    if fence_match:
        return fence_match.group(1).strip()

    start_idx = raw_output.find("{")
    end_idx = raw_output.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        return raw_output[start_idx : end_idx + 1].strip()

    return raw_output.strip()


class DriverExtractor:
    """Encapsulates local Hugging Face model and tokenizer state for reusable driver extraction."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str = "cuda",
        quantization: str = "4bit",  # "4bit", "8bit", or "none"
        hf_token: Optional[str] = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.quantization = quantization.lower()
        self.hf_token = hf_token

        self._validate_runtime_options()
        self.tokenizer, self.model = self._load_model_and_tokenizer()

    def _validate_runtime_options(self) -> None:
        if self.quantization not in VALID_QUANTIZATIONS:
            raise ConfigurationError(
                f"Unsupported quantization '{self.quantization}'. "
                f"Choose from: {', '.join(sorted(VALID_QUANTIZATIONS))}."
            )

        is_cuda = self.device.startswith("cuda")

        if is_cuda and not torch.cuda.is_available():
            raise ConfigurationError(
                f"Device '{self.device}' was requested, but CUDA is not available. "
                "Ensure an NVIDIA GPU with appropriate CUDA drivers and PyTorch are installed."
            )

        if not is_cuda and self.quantization != "none":
            raise ConfigurationError(
                f"Quantization '{self.quantization}' is only supported on CUDA devices, "
                f"got device '{self.device}'. Use quantization='none' for non-CUDA execution."
            )

    def _load_model_and_tokenizer(self) -> tuple[Any, Any]:
        is_cuda = self.device.startswith("cuda")
        quant_config: Optional[BitsAndBytesConfig] = None

        if is_cuda:
            compute_dtype = (
                torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            )
            if self.quantization == "4bit":
                quant_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_compute_dtype=compute_dtype,
                )
            elif self.quantization == "8bit":
                quant_config = BitsAndBytesConfig(load_in_8bit=True)
        else:
            compute_dtype = torch.float32

        try:
            tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                token=self.hf_token,
                trust_remote_code=True,
            )

            if tokenizer.pad_token_id is None:
                tokenizer.pad_token_id = tokenizer.eos_token_id

            model_kwargs: Dict[str, Any] = {
                "token": self.hf_token,
                "trust_remote_code": True,
                "device_map": "auto" if self.device == "cuda" else self.device,
            }

            if quant_config is not None:
                model_kwargs["quantization_config"] = quant_config
            else:
                model_kwargs["torch_dtype"] = compute_dtype

            model = AutoModelForCausalLM.from_pretrained(
                self.model_name, **model_kwargs
            )
            model.eval()
            return tokenizer, model

        except Exception as e:
            raise ExtractionError(
                f"Failed to load model '{self.model_name}' on '{self.device}': {e}"
            ) from e

    def extract(self, code_context: str, max_new_tokens: int = 4096) -> DriverSpec:
        """Extracts and validates a DriverSpec from aggregated C source code."""
        response_text = ""
        json_payload: Optional[str] = None

        try:
            schema_json = json.dumps(DriverSpec.model_json_schema(), indent=2)
            user_prompt = (
                f"Analyze the following C source code and extract the driver specification.\n\n"
                f"### Target JSON Schema:\n{schema_json}\n\n"
                f"### C Source Code Context:\n{code_context}\n\n"
                f"### Output:\nProvide only the raw JSON output matching the schema:"
            )

            messages = [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": user_prompt},
            ]

            if (
                hasattr(self.tokenizer, "apply_chat_template")
                and self.tokenizer.chat_template
            ):
                prompt_str = self.tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
            else:
                prompt_str = f"{SYSTEM_INSTRUCTION}\n\n{user_prompt}"

            inputs = self.tokenizer(prompt_str, return_tensors="pt").to(
                self.model.device
            )

            with torch.no_grad():
                output_tokens = self.model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    pad_token_id=self.tokenizer.pad_token_id,
                )

            generated_ids = output_tokens[0][inputs.input_ids.shape[1] :]
            response_text = self.tokenizer.decode(
                generated_ids, skip_special_tokens=True
            ).strip()

            if not response_text:
                raise ExtractionError("Model produced an empty response.")

            json_payload = _extract_json_text(response_text)
            return DriverSpec.model_validate_json(json_payload)

        except ValidationError as e:
            raise ExtractionError(
                f"Failed to validate generated JSON against DriverSpec schema: {e}\n"
                f"Extracted payload was:\n{json_payload if json_payload is not None else response_text}"
            ) from e
        except ExtractionError:
            raise
        except Exception as e:
            raise ExtractionError(f"Hugging Face local extraction failed: {e}") from e


def extract_driver_spec(
    code_context: str,
    model_name: str = DEFAULT_MODEL,
    device: str = "cuda",
    quantization: str = "4bit",  # "4bit", "8bit", or "none"
    hf_token: Optional[str] = None,
    max_new_tokens: int = 4096,
) -> DriverSpec:
    """Convenience one-shot wrapper around DriverExtractor."""
    extractor = DriverExtractor(
        model_name=model_name,
        device=device,
        quantization=quantization,
        hf_token=hf_token,
    )
    return extractor.extract(
        code_context=code_context,
        max_new_tokens=max_new_tokens,
    )