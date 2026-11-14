"""G2: prove six four-step chains exist across the fixture MCP servers."""

import asyncio
import sys
from itertools import pairwise
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from switchboard.index import parse_capabilities  # noqa: E402

SERVERS = ["repo", "issue", "incident", "account"]

CHAINS = {
    "comment on a failing test issue": [
        "repo.repo_search",
        "repo.list_issues",
        "issue.get_issue",
        "issue.add_comment",
    ],
    "review a patch": [
        "repo.locate_file",
        "repo.read_file",
        "repo.get_diff",
        "repo.post_review",
    ],
    "page on-call": [
        "incident.search_logs",
        "incident.get_trace",
        "incident.find_oncall",
        "incident.page_oncall",
    ],
    "refund a duplicate order": [
        "account.find_account",
        "account.list_orders",
        "account.get_order",
        "account.issue_refund",
    ],
    "close a stale issue": [
        "repo.repo_search",
        "repo.list_issues",
        "issue.get_issue",
        "issue.close_issue",
    ],
    "open and announce a ticket": [
        "incident.search_logs",
        "incident.get_trace",
        "incident.open_ticket",
        "incident.notify_channel",
    ],
}


async def discover(name):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", f"tests.fixtures.{name}_server"],
        cwd=str(ROOT),
    )
    async with Client(params, read_timeout_seconds=30) as client:
        return (await client.list_tools()).tools


async def main():
    tools = {}
    for name in SERVERS:
        listed = await discover(name)
        for tool in listed:
            consumes, produces = parse_capabilities(tool.description)
            tools[f"{name}.{tool.name}"] = {"consumes": consumes, "produces": produces}

    edges = {
        source: {
            target
            for target, spec in tools.items()
            if source != target and set(spec["consumes"]) & set(tools[source]["produces"])
        }
        for source in tools
    }

    failures = []
    if len(tools) != 60:
        failures.append(f"expected 60 tools, found {len(tools)}")
    total_edges = sum(len(targets) for targets in edges.values())
    if total_edges == 0:
        failures.append("no FEEDS edges derived")

    print(f"{len(tools)} tools, {total_edges} directed FEEDS edges")
    for task, chain in CHAINS.items():
        if len(chain) != 4:
            failures.append(f"{task}: chain has {len(chain)} steps")
            continue
        missing = [key for key in chain if key not in tools]
        if missing:
            failures.append(f"{task}: unknown tools {missing}")
            continue
        for source, target in pairwise(chain):
            if target not in edges[source]:
                failures.append(f"{task}: no edge {source} -> {target}")
        print("  " + " -> ".join(chain))

    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        return 1
    print(f"PASS: {len(CHAINS)} four-step chains")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
