import json
import re
from typing import Optional
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from pydantic import ValidationError

from ..exceptions import ExtractionError
from ..models.base import DriverSpec

SYSTEM_INSTRUCTION = (
    "You are an expert Linux kernel driver engineer and static code analysis assistant. "
    "Your objective is to thoroughly parse provided C source code (.c and .h files) "
    "and extract all structured driver metadata, matching tables (of_device_id, pci_device_id, "
    "usb_device_id, acpi_device_id), module parameters, entry points/lifecycle callbacks, "
    "file operations/sysfs/ioctl control surfaces, and register map bitfields.\n\n"
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


def extract_driver_spec(
    code_context: str,
    model_name: str = "Qwen/Qwen2.5-Coder-7B-Instruct",
    device: str = "cuda",
    quantization: str = "4bit",  # "4bit", "8bit", or "none"
    hf_token: Optional[str] = None,
    max_new_tokens: int = 4096,
) -> DriverSpec:
    """Executes local Hugging Face model inference with 4-bit / 8-bit quantization."""
    try:
        quant_config = None

        if device.startswith("cuda"):
            compute_dtype = (
                torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            )

            if quantization == "4bit":
                quant_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_compute_dtype=compute_dtype,
                )
            elif quantization == "8bit":
                quant_config = BitsAndBytesConfig(load_in_8bit=True)

        tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            token=hf_token,
            trust_remote_code=True,
        )

        if tokenizer.pad_token_id is None:
            tokenizer.pad_token_id = tokenizer.eos_token_id

        model_kwargs = {
            "token": hf_token,
            "trust_remote_code": True,
        }

        if quant_config is not None:
            model_kwargs["quantization_config"] = quant_config
            model_kwargs["device_map"] = "auto"
        else:
            torch_dtype = (
                torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            )
            model_kwargs["torch_dtype"] = torch_dtype
            model_kwargs["device_map"] = "auto" if device.startswith("cuda") else None

        model = AutoModelForCausalLM.from_pretrained(model_name, **model_kwargs)

        if quant_config is None and not device.startswith("cuda"):
            model = model.to(device)

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

        if hasattr(tokenizer, "apply_chat_template") and tokenizer.chat_template:
            prompt_str = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        else:
            prompt_str = f"{SYSTEM_INSTRUCTION}\n\n{user_prompt}"

        inputs = tokenizer(prompt_str, return_tensors="pt").to(model.device)

        with torch.no_grad():
            output_tokens = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                temperature=0.1,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
            )

        generated_ids = output_tokens[0][inputs.input_ids.shape[1] :]
        response_text = tokenizer.decode(
            generated_ids, skip_special_tokens=True
        ).strip()

        if not response_text:
            raise ExtractionError("Model produced an empty response.")

        json_payload = _extract_json_text(response_text)
        return DriverSpec.model_validate_json(json_payload)

    except ValidationError as e:
        raise ExtractionError(
            f"Failed to validate generated JSON against DriverSpec schema: {e}\n"
            f"Extracted payload was:\n{json_payload if 'json_payload' in locals() else response_text}"
        ) from e
    except Exception as e:
        if isinstance(e, ExtractionError):
            raise
        raise ExtractionError(f"Hugging Face local extraction failed: {e}") from e