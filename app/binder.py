"""Resolve a small, explicit binding language without executing expressions."""

import re


class BindingError(ValueError):
    pass


def _path(value, parts):
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise BindingError(f"Missing binding path component: {part}")
    return value


def bind(template, inputs, steps, current_index=None):
    """Bind constants, $input paths and earlier $steps.N.result paths.

    Operations use {"$op": "join", "args": [list, separator]}.
    default evaluates its fallback only when the first value is absent/null.
    Unknown dollar-prefixed strings and operation names are errors.
    """
    if current_index is None:
        current_index = len(steps)
    if isinstance(template, str):
        if template.startswith("$input."):
            return _path(inputs, template[7:].split("."))
        if template == "$input":
            return inputs
        match = re.fullmatch(r"\$steps\.(\d+)\.result(?:\.(.+))?", template)
        if match:
            index = int(match[1])
            if index >= current_index or index >= len(steps):
                raise BindingError("A binding must reference a completed earlier step")
            result = steps[index]
            if not isinstance(result, dict) or "result" not in result:
                raise BindingError("Referenced step has no result")
            return _path(result["result"], match[2].split(".") if match[2] else [])
        if template.startswith("$"):
            raise BindingError(f"Unknown binding expression: {template}")
        return template
    if isinstance(template, list):
        return [bind(value, inputs, steps, current_index) for value in template]
    if not isinstance(template, dict):
        return template
    if "$op" not in template:
        return {
            key: bind(value, inputs, steps, current_index)
            for key, value in template.items()
        }
    if set(template) != {"$op", "args"} or not isinstance(template["args"], list):
        raise BindingError("An operation requires only $op and an args array")
    operation, raw = template["$op"], template["args"]
    arity = {"str": (1,), "int": (1,), "join": (1, 2), "pick": (2,), "default": (2,)}
    if operation not in arity or len(raw) not in arity[operation]:
        raise BindingError(
            f"Unknown operation or incorrect argument count: {operation}"
        )
    if operation == "default":
        try:
            value = bind(raw[0], inputs, steps, current_index)
        except BindingError as exc:
            if not str(exc).startswith("Missing binding path component:"):
                raise
            value = None
        return (
            value if value is not None else bind(raw[1], inputs, steps, current_index)
        )
    args = [bind(value, inputs, steps, current_index) for value in raw]
    try:
        if operation == "str":
            return str(args[0])
        if operation == "int":
            if isinstance(args[0], (bool, dict, list)):
                raise ValueError("Expected an integer or integer string")
            if isinstance(args[0], float) and not args[0].is_integer():
                raise ValueError("Integer conversion would lose precision")
            return int(args[0])
        if operation == "join":
            separator = args[1] if len(args) == 2 else ""
            if not isinstance(args[0], list) or not isinstance(separator, str):
                raise ValueError("join requires an array and a string separator")
            if any(not isinstance(item, str) for item in args[0]):
                raise ValueError("join requires string items")
            return separator.join(args[0])
        if operation == "pick":
            if not isinstance(args[1], (str, int)) or isinstance(args[1], bool):
                raise ValueError("pick requires a key or index")
            return _path(args[0], [str(args[1])])
    except (TypeError, ValueError, OverflowError) as exc:
        raise BindingError(f"Invalid {operation} arguments: {exc}") from exc
