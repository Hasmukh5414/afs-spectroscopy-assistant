"""Self-contained tool parameters for providers that do not resolve local refs."""
from pydantic import BaseModel


def tool_parameters(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def expand(value, ancestors=frozenset()):
        if isinstance(value, list):
            return [expand(item, ancestors) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            reference = value["$ref"]
            prefix = "#/$defs/"
            if not reference.startswith(prefix):
                raise ValueError("Only local tool-schema references are supported")
            name = reference[len(prefix):]
            if name in ancestors or name not in definitions:
                raise ValueError("Recursive or missing tool-schema reference")
            resolved = expand(definitions[name], ancestors | {name})
            if not isinstance(resolved, dict):
                raise ValueError("A tool-schema definition must be an object")
            return {**resolved, **{key: expand(item, ancestors) for key, item in value.items() if key != "$ref"}}
        return {key: expand(item, ancestors) for key, item in value.items()}

    expanded = expand(schema)
    if not isinstance(expanded, dict):
        raise ValueError("Tool parameters must be an object")
    return expanded
