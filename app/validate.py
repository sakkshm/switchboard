"""Structural order validation independent of graph traversal and ranking."""


def validate_order(chain, catalog, input_types=()):
    tools = catalog if isinstance(catalog, dict) else {t["key"]: t for t in catalog}
    available = set(input_types)
    seen = set()
    for index, item in enumerate(chain):
        key = item["tool"] if isinstance(item, dict) else item
        tool = tools.get(key)
        if not tool or key in seen or not set(tool["consumes"]).issubset(available):
            return index
        seen.add(key)
        available.update(tool["produces"])
    return None
