"""G1: prove fixture dependencies and bindings before involving HydraDB."""
# ruff: noqa: E402

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from catalog import load_catalog


def check_chain(chain, tools):
    available = set()
    for index, key in enumerate(chain):
        tool = tools[key]
        missing = set(tool["consumes"]) - available
        if missing:
            raise AssertionError(f"{key} needs missing types {sorted(missing)}")
        references = {int(value) for value in re.findall(r"\$steps\.(\d+)\.result", json.dumps(tool["args"]))}
        if any(step >= index for step in references):
            raise AssertionError(f"{key} contains a forward reference")
        if index:
            if index - 1 not in references:
                raise AssertionError(f"{key} does not bind its predecessor's result")
            if not set(tools[chain[index - 1]]["produces"]) & set(tool["consumes"]):
                raise AssertionError(f"No FEEDS edge into {key}")
        available.update(tool["produces"])


def main():
    catalog = load_catalog()
    tools = {tool["key"]: tool for tool in catalog}
    assert len(tools) == len(catalog) == 60
    for tool in catalog:
        assert tool["consumes"] or tool["entry"], tool["key"]
    edges = [(left["key"], right["key"]) for left in catalog for right in catalog
             if left["key"] != right["key"] and set(left["produces"]) & set(right["consumes"])]
    for source, target in edges:
        assert set(tools[source]["produces"]) & set(tools[target]["consumes"])
    tasks = [json.loads(line) for line in (ROOT / "evals/tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    for task in tasks:
        assert len(task["expected_chain"]) >= 4
        check_chain(task["expected_chain"], tools)
    for index in (0, 4, 6, 10, 12, 18):
        task = tasks[index]
        print(f"PASS {task['id']}: " + " -> ".join(task["expected_chain"]))
    print(f"6/6 chain gates PASS; {len(tasks)} task bindings validated, {len(catalog)} tools, {len(edges)} FEEDS edges")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
