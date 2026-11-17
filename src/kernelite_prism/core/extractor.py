import json
import os
import re
from typing import Any, Callable, Dict, Optional
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from pydantic import ValidationError

from ..config import DEFAULT_MODEL, VALID_BACKENDS, VALID_QUANTIZATIONS
from ..exceptions import ConfigurationError, ExtractionError
from ..models.base import DriverSpec
from ..models.registers import Bitfield, RegisterMap, infer_bits_from_macro
from .ast_parser import ASTExtractionResult

SYSTEM_INSTRUCTION = (
    "You are an expert Linux kernel driver engineer and bare-metal C++ systems architect. "
    "Analyze the provided Linux kernel C source code (.c and .h files) and output a concrete JSON "
    "data object describing the hardware peripheral and driver mechanics for bare-metal C++ porting.\n\n"
    "MANDATORY RULES:\n"
    "1. OUTPUT DATA, NOT A JSON SCHEMA: Never emit '$schema', '$defs', 'properties', or 'anyOf'. "
    "Emit a single populated JSON object matching the exact structure shown in the Output Template.\n"
    "2. ZERO HALLUCINATED ADDRESSES OR MASKS: Every register offset and bitmask in `register_map` "
    "MUST strictly match the C header/source `#define` constants. Never invent hex offsets.\n"
    "3. ISOLATE DYNAMIC OFFSET EXPRESSIONS: When a register offset is indexed or shifted "
    "(e.g., `BASE + (i * 0x4)` or `offset << port->regshift`), store the base hex offset in `offset` "
    "and the C formula in `offset_expression`.\n"
    "4. CHRONOLOGICAL PROBE / INIT SEQUENCE: Populate `init_sequence` in exact execution order "
    "matching `.probe` / setup (`MMIO Map`, `Hardware Reset`, `Write Register`, `Read & Poll`, "
    "`Read Register`, `Memory Allocate`, `DMA Setup`, `Interrupt Hook`, `Register Device`).\n"
    "5. CONCURRENCY & IRQ MAPPING: Translate Linux locks (`spin_lock_irqsave`, `spin_lock`, `mutex_lock`) "
    "into `concurrency_locks` (`IRQSafeSpinlock`, `Spinlock`, `SleepableMutex`, `ReadWriteLock`, `Atomic`), "
    "and map `irqreturn_t` handlers in `runtime_operations.interrupt_lifecycle` to raw status, mask, "
    "and acknowledge registers.\n\n"
    "6. CONCISE OUTPUT BUDGET: Keep every `description` under 12 words and include only the primary control/status/IRQ bitfields (max 8 per register) so the JSON completes cleanly without truncation.\n\n""CRITICAL: Return ONLY valid, raw JSON with no markdown fences or commentary."
)

COMPACT_OUTPUT_TEMPLATE = """{
  "device_name": "<Human-readable hardware controller name>",
  "driver_name": "<Canonical Linux driver module name>",
  "compatibility": ["<DTB compatible string, e.g. arm,pl011>"],
  "hardware_info": {
    "bus_type": "<mmio | pci | platform | i2c | spi | usb | uart | other>",
    "compatible_strings": ["<DTB compatible string>"],
    "vendor_ids": ["<0x... if PCI/USB>"],
    "device_ids": ["<0x... if PCI/USB>"],
    "base_address_offsets": [
      {
        "region_id": "0",
        "name": "<MMIO region name>",
        "base_offset": "0x0",
        "size_hint": "<e.g. 0x1000 or null>",
        "description": "<Region description>"
      }
    ],
    "endianness": "<little | big | configurable | unknown>",
    "irq_lines": ["<IRQ description>"]
  },
  "metadata": {
    "author": "<MODULE_AUTHOR or null>",
    "license": "<MODULE_LICENSE or null>",
    "description": "<MODULE_DESCRIPTION or null>",
    "parameters": []
  },
  "matching_tables": {
    "of_device_id": [{"compatible": "<compatible string>"}],
    "pci_device_id": [],
    "platform_device_id": []
  },
  "lifecycle": {
    "probe_function": "<probe C function name or null>",
    "remove_function": "<remove C function name or null>",
    "init_function": "<module/console init C function or null>"
  },
  "structs": ["<e.g. platform_driver, uart_ops, amba_driver>"],
  "register_map": [
    {
      "name": "<REGISTER_MACRO_NAME>",
      "offset": "<0x00>",
      "offset_expression": "<C offset expression if computed, else null>",
      "width": 32,
      "access": "<RW | RO | WO | R | W | W1C | UNKNOWN>",
      "reset_value": "<0x0 or null>",
      "description": "<Register purpose>",
      "bitfields": [
        {
          "name": "<BITFIELD_MACRO_NAME>",
          "bits": "<e.g. 0 or 7:4>",
          "mask_macro": "<e.g. BIT(0) or 0x01>",
          "description": "<Bitfield behavior>"
        }
      ]
    }
  ],
  "init_sequence": [
    {
      "step": 1,
      "operation": "<MMIO Map | Hardware Reset | Write Register | Read & Poll | Read Register | Memory Allocate | DMA Setup | Interrupt Hook | Clock/Reset Control | Register Device | Other>",
      "source_function": "<C function name>",
      "target_register": "<REGISTER_MACRO_NAME or null>",
      "offset": "<0x00 or null>",
      "value_or_mask": "<Written value/mask or null>",
      "poll_condition": "<Exit condition for Read & Poll, else null>",
      "description": "<What this step does on hardware>"
    }
  ],
  "runtime_operations": {
    "probe_handler": "<probe C function name or null>",
    "remove_handler": "<remove C function name or null>",
    "read_handlers": [
      {
        "operation_name": "<e.g. rx_chars>",
        "c_function": "<C function>",
        "registers_touched": ["<REG_NAME>"],
        "description": "<Handler summary>"
      }
    ],
    "write_handlers": [
      {
        "operation_name": "<e.g. start_tx>",
        "c_function": "<C function>",
        "registers_touched": ["<REG_NAME>"],
        "description": "<Handler summary>"
      }
    ],
    "transmit_handler": null,
    "receive_handler": null,
    "dma_setup": {
      "allocation_api": "<e.g. dma_alloc_coherent or null>",
      "address_mask_bits": 64,
      "descriptor_rings": [],
      "coherency_notes": "<Barrier/coherency notes or null>"
    },
    "interrupt_lifecycle": {
      "irq_handler": "<top-half ISR C function or null>",
      "threaded_handler": null,
      "registration_api": "<e.g. request_irq or devm_request_irq or null>",
      "irq_flags": ["<e.g. IRQF_SHARED>"],
      "status_register": "<ISR status register macro or null>",
      "ack_register": "<ISR ack/clear register macro or null>",
      "mask_register": "<Interrupt mask register macro or null>",
      "ack_mechanism": "<e.g. Write-1-to-clear ICR or Read-to-clear>",
      "flow_steps": [
        {
          "step": 1,
          "operation": "<Read Register | Write Register | Other>",
          "source_function": "<ISR C function>",
          "target_register": "<REG_NAME or null>",
          "offset": "<0x00 or null>",
          "value_or_mask": "<mask or null>",
          "description": "<Step description>"
        }
      ]
    }
  },
  "concurrency_locks": [
    {
      "linux_primitive": "<e.g. spin_lock_irqsave(&port->lock, flags)>",
      "abstract_lock_type": "<IRQSafeSpinlock | Spinlock | SleepableMutex | ReadWriteLock | Atomic | None>",
      "protected_resources": ["<Registers or ring buffers>"],
      "bare_metal_recommendation": "<Bare-metal C++ locking guidance>"
    }
  ],
  "migration_notes": {
    "linux_to_microkernel_mappings": [
      {
        "linux_api": "<e.g. readl / writel>",
        "microkernel_equivalent": "<e.g. MmioRegion::read32 / write32>",
        "notes": "<Bare-metal C++ porting note>"
      }
    ],
    "architectural_caveats": ["<Important bare-metal porting caveat>"]
  }
}"""


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


def ground_spec_with_ast(spec: DriverSpec, ast_result: Optional[ASTExtractionResult]) -> DriverSpec:
    """Enforces deterministic parity between AST-parsed C constants and the extracted DriverSpec."""
    if ast_result is None:
        return spec

    macro_map = ast_result.macro_lookup()

    # 1. Verify and normalize offsets & bitfield masks against AST constants
    for reg in spec.register_map:
        if reg.name in macro_map:
            macro = macro_map[reg.name]
            if macro.hex_value is not None:
                reg.offset = macro.hex_value
            elif macro.value and not reg.offset:
                reg.offset = macro.value

        for bf in reg.bitfields:
            if bf.name in macro_map:
                bf_macro = macro_map[bf.name]
                if bf_macro.value and not bf.mask_macro:
                    bf.mask_macro = bf_macro.value
            if not bf.bits and bf.mask_macro:
                bf.bits = infer_bits_from_macro(bf.mask_macro)

    # 2. If LLM omitted registers that were parsed from AST headers/sources, backfill them deterministically
    existing_reg_names = {r.name for r in spec.register_map}
    if not existing_reg_names:
        for macro in ast_result.macros:
            if macro.category == "register_offset" and macro.hex_value is not None:
                spec.register_map.append(
                    RegisterMap(
                        name=macro.name,
                        offset=macro.hex_value,
                        description=f"Extracted from {macro.file} (#define {macro.name} {macro.value})",
                    )
                )

    # 3. Backfill metadata from MODULE_* macros if omitted by LLM
    meta = ast_result.metadata_macros
    if not spec.metadata.license and meta.get("license"):
        spec.metadata.license = meta["license"]
    if not spec.metadata.author and meta.get("author"):
        spec.metadata.author = meta["author"]
    if not spec.metadata.description and meta.get("description"):
        spec.metadata.description = meta["description"]

    # 4. Backfill structs list from AST-extracted driver structs
    for s in ast_result.driver_structs:
        if s.struct_type not in spec.structs:
            spec.structs.append(s.struct_type)

    return spec


class DriverExtractor:
    """Encapsulates LLM inference and schema-enforced structured output generation."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str = "cuda",
        quantization: str = "4bit",
        backend: str = "transformers",
        hf_token: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        openai_base_url: Optional[str] = None,
        custom_generator: Optional[Callable[[str, str], str]] = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.quantization = quantization.lower()
        self.backend = backend.lower()
        self.hf_token = hf_token
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        self.openai_base_url = openai_base_url or os.getenv("OPENAI_BASE_URL")
        self.custom_generator = custom_generator

        self.tokenizer: Any = None
        self.model: Any = None
        self._outlines_logits_processor: Any = None

        if self.custom_generator is None:
            self._validate_runtime_options()
            if self.backend in {"transformers", "outlines"}:
                self.tokenizer, self.model = self._load_model_and_tokenizer()
                if self.backend == "outlines":
                    self._init_outlines_processor()

    def _validate_runtime_options(self) -> None:
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
                    "Ensure an NVIDIA GPU with appropriate CUDA drivers and PyTorch are installed."
                )

            if not is_cuda and self.quantization != "none":
                raise ConfigurationError(
                    f"Quantization '{self.quantization}' is only supported on CUDA devices, "
                    f"got device '{self.device}'. Use quantization='none' for non-CUDA execution."
                )

    def _init_outlines_processor(self) -> None:
        """Initializes Outlines JSON Schema constrained logits processor when available."""
        try:
            import outlines
            from outlines.processors import JSONLogitsProcessor

            outlines_model = outlines.from_transformers(self.model, self.tokenizer)
            self._outlines_logits_processor = JSONLogitsProcessor(
                schema=DriverSpec.draft_2020_12_schema(),
                tokenizer=outlines_model.tokenizer,
            )
        except Exception:
            self._outlines_logits_processor = None

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

    def _generate_with_transformers(self, user_prompt: str, max_new_tokens: int) -> str:
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

        inputs = self.tokenizer(prompt_str, return_tensors="pt").to(self.model.device)
        gen_kwargs: Dict[str, Any] = {
            "max_new_tokens": max_new_tokens,
            "do_sample": False,
            "pad_token_id": self.tokenizer.pad_token_id,
        }
        if self._outlines_logits_processor is not None:
            gen_kwargs["logits_processor"] = [self._outlines_logits_processor]

        with torch.no_grad():
            output_tokens = self.model.generate(**inputs, **gen_kwargs)

        generated_ids = output_tokens[0][inputs.input_ids.shape[1] :]
        return self.tokenizer.decode(generated_ids, skip_special_tokens=True).strip()

    def _generate_with_openai_or_instructor(self, user_prompt: str, max_new_tokens: int) -> str:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ExtractionError("The 'openai' package is required for API structured outputs.") from e

        client = OpenAI(api_key=self.openai_api_key, base_url=self.openai_base_url)

        if self.backend == "instructor":
            try:
                import instructor

                patched_client = instructor.from_openai(client)
                spec_obj = patched_client.chat.completions.create(
                    model=self.model_name,
                    response_model=DriverSpec,
                    max_tokens=max_new_tokens,
                    temperature=0.0,
                    messages=[
                        {"role": "system", "content": SYSTEM_INSTRUCTION},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                return spec_obj.model_dump_json()
            except Exception as e:
                raise ExtractionError(f"Instructor structured extraction failed: {e}") from e

        response = client.chat.completions.create(
            model=self.model_name,
            temperature=0.0,
            max_tokens=max_new_tokens,
            messages=[
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "driver_spec",
                    "strict": False,
                    "schema": DriverSpec.draft_2020_12_schema(),
                },
            },
        )
        content = response.choices[0].message.content or ""
        return content.strip()

    def extract(
        self,
        code_context: str,
        max_new_tokens: int = 8192,
        max_retries: int = 2,
        ast_result: Optional[ASTExtractionResult] = None,
    ) -> DriverSpec:
        """Extracts and validates a DriverSpec from C source code with schema enforcement and retries."""
        base_prompt = (
            f"Analyze the following Linux C driver code and extract the hardware and bare-metal "
            f"C++ porting specification as a populated JSON object.\n\n"
            f"### Required JSON Output Structure (populate with extracted values; do NOT output a JSON Schema):\n"
            f"{COMPACT_OUTPUT_TEMPLATE}\n\n"
            f"### C Source Code Context:\n{code_context}\n\n"
            f"### Output:\nProvide ONLY the populated JSON object starting with '{{':"
        )

        current_prompt = base_prompt
        last_error: Optional[Exception] = None
        last_payload: str = ""

        for attempt in range(max_retries + 1):
            try:
                if self.custom_generator is not None:
                    response_text = self.custom_generator(SYSTEM_INSTRUCTION, current_prompt)
                elif self.backend in {"openai", "instructor"}:
                    response_text = self._generate_with_openai_or_instructor(
                        current_prompt, max_new_tokens
                    )
                else:
                    response_text = self._generate_with_transformers(
                        current_prompt, max_new_tokens
                    )

                if not response_text or not response_text.strip():
                    raise ExtractionError("Model produced an empty response.")

                json_payload = _extract_json_text(response_text)
                last_payload = json_payload

                parsed_obj = json.loads(json_payload)
                if isinstance(parsed_obj, dict) and ("$defs" in parsed_obj or "$schema" in parsed_obj):
                    raise ValueError(
                        "Output echoed JSON Schema keywords ('$schema'/'$defs') instead of a populated DriverSpec instance."
                    )

                spec = DriverSpec.model_validate(parsed_obj)
                return ground_spec_with_ast(spec, ast_result)

            except (ValidationError, json.JSONDecodeError, ValueError) as e:
                last_error = e
                if attempt < max_retries:
                    current_prompt = (
                        f"{base_prompt}\n\n"
                        f"### PREVIOUS ATTEMPT ERROR:\n"
                        f"Your previous output failed validation:\n{e}\n\n"
                        f"Keep all descriptions under 8 words and limit bitfields to the top 6 active bits per register so the JSON is not truncated. Emit ONLY a valid, populated DriverSpec JSON data object:"
                    )
            except ExtractionError:
                raise
            except Exception as e:
                raise ExtractionError(f"LLM extraction failed: {e}") from e

        raise ExtractionError(
            f"Failed to validate generated JSON against DriverSpec schema after {max_retries + 1} attempt(s): "
            f"{last_error}\nExtracted payload was:\n{last_payload}"
        ) from last_error


def extract_driver_spec(
    code_context: str,
    model_name: str = DEFAULT_MODEL,
    device: str = "cuda",
    quantization: str = "4bit",
    backend: str = "transformers",
    hf_token: Optional[str] = None,
    max_new_tokens: int = 8192,
    ast_result: Optional[ASTExtractionResult] = None,
    custom_generator: Optional[Callable[[str, str], str]] = None,
) -> DriverSpec:
    """Convenience one-shot wrapper around DriverExtractor."""
    extractor = DriverExtractor(
        model_name=model_name,
        device=device,
        quantization=quantization,
        backend=backend,
        hf_token=hf_token,
        custom_generator=custom_generator,
    )
    return extractor.extract(
        code_context=code_context,
        max_new_tokens=max_new_tokens,
        ast_result=ast_result,
    )
