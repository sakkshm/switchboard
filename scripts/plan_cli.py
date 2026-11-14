"""Print a graph-validated plan without executing any tools."""
# ruff: noqa: E402

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.planner import NoPlan, Planner
from app.store import GraphStore
from catalog import load_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task")
    parser.add_argument("--principal", default="demo")
    parser.add_argument("--inputs", default="{}", help="JSON object of user inputs")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--catalog", default=os.getenv("SWITCHBOARD_CATALOG"), help="Reviewed tool catalog snapshot")
    args = parser.parse_args()
    inputs = json.loads(args.inputs)
    if not isinstance(inputs, dict):
        parser.error("--inputs must be a JSON object")
    catalog = load_catalog(args.catalog)
    try:
        plan = Planner(GraphStore(catalog), catalog).plan(args.task, principal=args.principal, inputs=inputs, reuse=False)
    except NoPlan as exc:
        parser.exit(1, f"NoPlan: {exc}\n")
    if args.json:
        print(json.dumps(plan, indent=2))
    else:
        print(" -> ".join(step["tool"] for step in plan["steps"]))
        print(f"plan={plan['key']} graph_traversals={plan['graph_queries']} type_valid=true")


if __name__ == "__main__":
    main()
