import json
import yaml

from ..exceptions import SerializationError
from ..models.base import DriverSpec


def serialize_spec(spec: DriverSpec, format_type: str = "json") -> str:
    """Serializes a validated DriverSpec model instance into JSON or YAML format."""
    try:
        data = spec.model_dump(exclude_none=True)
        fmt = format_type.lower()

        if fmt == "json":
            return json.dumps(data, indent=2)
        elif fmt in ["yaml", "yml"]:
            return yaml.dump(data, sort_keys=False, default_flow_style=False)
        else:
            raise SerializationError(
                f"Unsupported output format: '{format_type}'. Supported formats are 'json' and 'yaml'."
            )

    except Exception as e:
        if isinstance(e, SerializationError):
            raise
        raise SerializationError(f"Failed to serialize specification: {e}") from e