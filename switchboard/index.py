"""Discover upstream tools and write the tool graph into HydraDB."""

import asyncio
import json
import re

from .executor import list_tools
from .hydra import node_id

HEADER = re.compile(r"\[consumes:\s*([^|\]]*?)\s*\|\s*produces:\s*([^\]]*?)\]", re.IGNORECASE)


def parse_capabilities(description):
    """Read `[consumes: a,b | produces: c]` from a tool description."""
    match = HEADER.search(description or "")
    if not match:
        return [], []

    def split(part):
        return [item.strip() for item in part.split(",") if item.strip()]

    return split(match.group(1)), split(match.group(2))


def capabilities(tool, override=None):
    """Header wins, then config annotations. MCP has no dependency types."""
    consumes, produces = parse_capabilities(tool.description)
    if consumes or produces:
        return consumes, produces
    override = override or {}
    return override.get("consumes", []), override.get("produces", [])


async def _discover(servers):
    tools = []
    for name, server in servers.items():
        if "." in name:
            raise ValueError(f"server name cannot contain a dot: {name}")
        overrides = server.get("capabilities", {})
        for tool in await list_tools(server):
            consumes, produces = capabilities(tool, overrides.get(tool.name))
            key = f"{name}.{tool.name}"
            tools.append(
                {
                    "id": node_id(key),
                    "key": key,
                    "name": tool.name,
                    "server": name,
                    "description": tool.description or "",
                    "consumes": consumes,
                    "produces": produces,
                    "input_schema": json.dumps(tool.input_schema or {}),
                    "output_schema": json.dumps(tool.output_schema or {}),
                }
            )
    return tools


def discover(servers):
    return asyncio.run(_discover(servers))


def edges(tools):
    """One FEEDS edge for every producer/consumer pair, across servers."""
    return [
        {"src": src["id"], "dst": dst["id"]}
        for src in tools
        for dst in tools
        if src["id"] != dst["id"] and set(src["produces"]) & set(dst["consumes"])
    ]


def chunks(items, size=1000):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _literals(values):
    for value in values:
        if not value.replace("_", "").isalnum():
            raise ValueError(f"type names must be alphanumeric: {value!r}")
    return "[" + ", ".join(f"'{value}'" for value in values) + "]"


def write(hydra, tools):
    # shortcut: OSS rejects list values in UNWIND rows and top-level MERGE+SET,
    # so scalars go through one UNWIND batch and type lists get one MATCH per node
    for chunk in chunks(tools):
        hydra.query(
            "UNWIND $rows AS row MERGE (n {id: row.id}) "
            "SET n:Tool, n.key = row.key, n.name = row.name, n.server = row.server, "
            "n.description = row.description, n.input_schema = row.input_schema, "
            "n.output_schema = row.output_schema",
            {"rows": chunk},
        )
    for tool in tools:
        hydra.query(
            f"MATCH (n:Tool {{id: $id}}) "
            f"SET n.consumes = {_literals(tool['consumes'])}, "
            f"n.produces = {_literals(tool['produces'])}",
            {"id": tool["id"]},
        )
    pairs = edges(tools)
    for chunk in chunks(pairs):
        # shortcut: CREATE without a label dedupes on (src, dst, type); stale tools
        # are not pruned until `switchboard reset`
        hydra.query(
            "UNWIND $rows AS row "
            "CREATE (a {id: row.src})-[:FEEDS]->(b {id: row.dst})",
            {"rows": chunk},
        )
    return len(tools), len(pairs)
