"""Check that every consumed type comes from the input or an earlier step."""


def check(steps, tools):
    available = set()
    for index, step in enumerate(steps):
        tool = tools[step["tool"]]
        for type_name in tool["consumes"]:
            reference = step["args"].get(type_name)
            if not reference:
                return f"step {index} ({step['tool']}) has no binding for {type_name}"
            if reference.startswith("$steps."):
                source = int(reference.split(".")[1])
                if source >= index:
                    return f"step {index} binds {type_name} from step {source}, which is not earlier"
                if type_name not in available:
                    return f"step {index} needs {type_name} before any step produces it"
        available.update(tool["produces"])
    return None
