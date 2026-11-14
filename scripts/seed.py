"""Ingest MCP tools/list, validate trusted metadata, and seed HydraDB idempotently."""
# ruff: noqa: E402

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.store import GraphStore
from app.transport import MCPTransport
from catalog import load_catalog

CAPABILITY = re.compile(r"\[consumes:\s*([A-Za-z0-9_, -]*)\s*\|\s*produces:\s*([A-Za-z0-9_, -]*)\]\s*$")


def capabilities(description):
    match = CAPABILITY.search(description)
    if not match:
        raise ValueError("Tool description is missing a trailing capability header")
    return tuple([value.strip() for value in group.split(",") if value.strip()] for group in match.groups())


def validate_live(snapshot, live, server):
    trusted = {tool["name"]: tool for tool in snapshot if tool["server"] == server}
    discovered = {}
    for tool in live:
        name = tool.get("name")
        if name in discovered:
            raise ValueError(f"{server}: duplicate tool name {name}")
        if name not in trusted:
            raise ValueError(f"{server}/{name}: missing trusted argument-binding metadata in catalog/catalog.json")
        Draft202012Validator.check_schema(tool.get("inputSchema", {}))
        consumes, produces = capabilities(tool.get("description", ""))
        expected = trusted[name]
        if consumes != expected["consumes"] or produces != expected["produces"]:
            raise ValueError(f"{server}/{name}: capability drift; review catalog/catalog.json before seeding")
        for field in ("description", "inputSchema"):
            if tool.get(field) != expected[field]:
                raise ValueError(f"{server}/{name}: {field} drift; review the committed snapshot")
        annotations = tool.get("annotations") or {}
        if (annotations.get("readOnlyHint", False) != (expected["risk"] == "read")
                or annotations.get("destructiveHint", False) != (expected["risk"] == "destructive")):
            raise ValueError(f"{server}/{name}: safety annotations disagree with trusted risk metadata")
        discovered[name] = expected
    missing = set(trusted) - set(discovered)
    if missing:
        raise ValueError(f"{server}: tools missing from tools/list: {sorted(missing)}")
    return list(discovered.values())


async def ingest(snapshot):
    transport = MCPTransport()
    discovered = []
    for server in sorted({tool["server"] for tool in snapshot}):
        live = await transport.list_tools(server)
        discovered.extend(validate_live(snapshot, live, server))
    return sorted(discovered, key=lambda tool: tool["key"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-only", action="store_true", help="Explicit offline ingest of the committed fixture snapshot; still writes real HydraDB")
    parser.add_argument("--catalog", default=os.getenv("SWITCHBOARD_CATALOG"), help="Reviewed catalog snapshot; defaults to the committed 60-tool fixture")
    args = parser.parse_args()
    snapshot = load_catalog(args.catalog)
    catalog = snapshot if args.snapshot_only else asyncio.run(ingest(snapshot))
    store = GraphStore(catalog)
    result = store.seed()
    actual = {"tools": int(store.hydra.query("MATCH (n:Tool) RETURN count(*) AS n")[0][0]),
              "types": int(store.hydra.query("MATCH (n:Type) RETURN count(*) AS n")[0][0]),
              "feeds": int(store.hydra.query("MATCH ()-[r:FEEDS]->() RETURN count(*) AS n")[0][0])}
    if actual != result:
        raise RuntimeError(f"Seed count mismatch: expected {result}, graph contains {actual}; inspect existing catalog data before resetting")
    print(json.dumps({"source": "committed snapshot" if args.snapshot_only else "MCP tools/list", **result}, indent=2))


if __name__ == "__main__":
    main()
