from google import genai
from google.genai import types
from pydantic import ValidationError

from ..exceptions import ExtractionError
from ..models.base import DriverSpec


SYSTEM_INSTRUCTION = (
    "You are an expert Linux kernel driver engineer and static code analysis assistant. "
    "Your objective is to thoroughly parse provided C source code (.c and .h files) "
    "and extract all structured driver metadata, matching tables (of_device_id, pci_device_id, "
    "usb_device_id, acpi_device_id), module parameters, entry points/lifecycle callbacks, "
    "file operations/sysfs/ioctl control surfaces, and register map bitfields. "
    "Output must strictly adhere to the provided JSON schema without missing available declarations."
)


def extract_driver_spec(
    code_context: str,
    api_key: str,
    model_name: str = "gemini-2.5-flash",
) -> DriverSpec:
    """Submits C source context to Gemini API with Pydantic structured output enforcement."""
    try:
        client = genai.Client(api_key=api_key)

        prompt = (
            "Analyze the following C source code and extract the driver specification:\n\n"
            f"{code_context}"
        )

        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=DriverSpec,
                temperature=0.1,
                system_instruction=SYSTEM_INSTRUCTION,
            ),
        )

        if not response.text:
            raise ExtractionError("Gemini API returned an empty response.")

        # Parse and validate returned JSON string into DriverSpec
        return DriverSpec.model_validate_json(response.text)

    except ValidationError as e:
        raise ExtractionError(
            f"Failed to validate model response against DriverSpec schema: {e}"
        ) from e
    except Exception as e:
        if isinstance(e, ExtractionError):
            raise
        raise ExtractionError(f"LLM Extraction failed: {e}") from e