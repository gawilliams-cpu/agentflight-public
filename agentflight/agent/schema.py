"""Check a tool call's arguments against its schema before using them.

The API's strict mode should already guarantee this; checking again in code
means a malformed call is bounced back to the agent instead of breaking the run.
Covers the parts of JSON Schema our tool file uses: object, array, string, boolean,
integer, enum, required, and additionalProperties: false.
"""

TYPES = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}


def schema_errors(value, schema, path="input") -> list:
    errors = []
    expected = schema.get("type")
    if expected:
        python_type = TYPES[expected]
        # bool is a subclass of int in Python; don't let True pass as an integer.
        if not isinstance(value, python_type) or (expected == "integer" and isinstance(value, bool)):
            return [f"{path} should be {expected}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} should be one of {schema['enum']}, got {value!r}")
    if expected == "object":
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name} is required")
        for name, item in value.items():
            if name in properties:
                errors += schema_errors(item, properties[name], f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{name} is not an allowed field")
    if expected == "array" and "items" in schema:
        for i, item in enumerate(value):
            errors += schema_errors(item, schema["items"], f"{path}[{i}]")
    return errors
