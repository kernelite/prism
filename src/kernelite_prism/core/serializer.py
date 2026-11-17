import json
import yaml

from ..exceptions import SerializationError
from ..models.base import DriverSpec
from .doc_generator import generate_markdown_spec


def serialize_spec(spec: DriverSpec, format_type: str = "json") -> str:
    """Serializes a validated DriverSpec model instance into JSON, YAML, or Markdown format."""
    try:
        fmt = format_type.lower()

        if fmt == "json":
            data = spec.model_dump(exclude_none=True)
            return json.dumps(data, indent=2)
        elif fmt in ["yaml", "yml"]:
            data = spec.model_dump(exclude_none=True)
            return yaml.dump(data, sort_keys=False, default_flow_style=False)
        elif fmt in ["md", "markdown"]:
            return generate_markdown_spec(spec)
        else:
            raise SerializationError(
                f"Unsupported output format: '{format_type}'. Supported formats are 'json', 'yaml', and 'markdown'."
            )

    except Exception as e:
        if isinstance(e, SerializationError):
            raise
        raise SerializationError(f"Failed to serialize specification: {e}") from e