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
    "Your objective is to analyze pre-parsed Linux kernel C source code (.c and .h files) "
    "and extract a complete, deterministic hardware and driver specification for porting to a "
    "bare-metal C++ microkernel.\n\n"
    "MANDATORY EXTRACTION RULES:\n"
    "1. ZERO HALLUCINATED ADDRESSES OR MASKS: Every register offset and bitmask in `register_map` "
    "MUST strictly match the C header/source macro definitions. Never invent offsets.\n"
    "2. ISOLATE REGISTER OFFSET EXPRESSIONS: When a register is indexed or computed dynamically "
    "(e.g., `BASE + (i * 0x4)` or `0x03800 + (n * 0x100)`), record the base hex offset in `offset` "
    "and preserve the exact C arithmetic formula in `offset_expression`.\n"
    "3. CHRONOLOGICAL PROBE / INIT SEQUENCE: Populate `init_sequence` in exact execution order "
    "matching `.probe` / early init (`MMIO Map`, `Hardware Reset`, `Write Register`, `Read & Poll`, "
    "`Memory Allocate`, `DMA Setup`, `Interrupt Hook`, `Register Device`).\n"
    "4. ABSTRACT CONCURRENCY PRIMITIVES: Translate every Linux lock (`spin_lock_irqsave`, "
    "`spin_lock`, `mutex_lock`, `raw_spin_lock_irqsave`) in `concurrency_locks` into an abstract "
    "lock (`IRQSafeSpinlock`, `Spinlock`, `SleepableMutex`, `ReadWriteLock`, `Atomic`), listing "
    "protected registers/rings and bare-metal C++ guidance.\n"
    "5. MAP IRQ ROUTINES TO RAW HARDWARE REGISTERS: In `runtime_operations.interrupt_lifecycle`, "
    "trace `irqreturn_t` handlers and `request_irq` calls to the exact hardware status register, "
    "acknowledge/clear register, mask register, acknowledgment mechanism (e.g., Read-to-clear vs. W1C), "
    "and ordered `flow_steps`.\n\n"
    "CRITICAL OUTPUT REQUIREMENT: Output strictly a single, valid JSON object satisfying the "
    "provided JSON Schema Draft 2020-12. Do not emit markdown fences, commentary, or extra text."
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

    # 2. Backfill metadata from MODULE_* macros if omitted by LLM
    meta = ast_result.metadata_macros
    if not spec.metadata.license and meta.get("license"):
        spec.metadata.license = meta["license"]
    if not spec.metadata.author and meta.get("author"):
        spec.metadata.author = meta["author"]
    if not spec.metadata.description and meta.get("description"):
        spec.metadata.description = meta["description"]

    # 3. Backfill structs list from AST-extracted driver structs
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
            # Graceful fallback to schema-guided decoding + validation retry loop
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

        # OpenAI Structured Outputs (`json_schema`)
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
        max_new_tokens: int = 4096,
        max_retries: int = 2,
        ast_result: Optional[ASTExtractionResult] = None,
    ) -> DriverSpec:
        """Extracts and validates a DriverSpec from C source code with schema enforcement and retries."""
        schema_json = json.dumps(DriverSpec.draft_2020_12_schema(), indent=2)
        base_prompt = (
            f"Analyze the following Linux C driver code and extract the complete hardware and "
            f"microkernel porting specification.\n\n"
            f"### Target JSON Schema (Draft 2020-12):\n{schema_json}\n\n"
            f"### C Source Code Context:\n{code_context}\n\n"
            f"### Output:\nProvide only the raw JSON object matching the schema:"
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
                spec = DriverSpec.model_validate_json(json_payload)
                return ground_spec_with_ast(spec, ast_result)

            except (ValidationError, json.JSONDecodeError) as e:
                last_error = e
                if attempt < max_retries:
                    current_prompt = (
                        f"{base_prompt}\n\n"
                        f"### PREVIOUS ATTEMPT VALIDATION ERROR:\n"
                        f"Your previous JSON failed strict schema validation with:\n{e}\n\n"
                        f"Fix all schema errors and return ONLY the corrected, valid JSON object:"
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
    max_new_tokens: int = 4096,
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