"""Turn a tool chain into argument templates: `$input.*` and `$steps.N.result.*`."""

import json


def _producer_ref(index, tool, type_name):
    properties = json.loads(tool["output_schema"] or "{}").get("properties", {})
    return f"$steps.{index}.result.{type_name}" if type_name in properties else f"$steps.{index}.result"


def bind(tools, chain):
    """Return (steps, required input types) for an ordered list of tool keys."""
    available = {}
    required = []
    steps = []
    for index, key in enumerate(chain):
        tool = tools[key]
        args = {}
        for type_name in tool["consumes"]:
            if type_name in available:
                args[type_name] = available[type_name]
            else:
                args[type_name] = f"$input.{type_name}"
                if type_name not in required:
                    required.append(type_name)
        steps.append({"i": index, "tool": key, "args": args})
        for type_name in tool["produces"]:
            available[type_name] = _producer_ref(index, tool, type_name)
    return steps, required


def resolve(template, inputs, results):
    if isinstance(template, str) and template.startswith("$input."):
        return inputs[template[len("$input.") :]]
    if isinstance(template, str) and template.startswith("$steps."):
        parts = template.split(".")
        if parts[2] != "result":
            raise KeyError(template)
        value = results[int(parts[1])]
        for part in parts[3:]:
            value = value[part]
        return value
    return template


def resolve_args(args, inputs, results):
    return {name: resolve(value, inputs, results) for name, value in args.items()}
