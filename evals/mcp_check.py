"""MCP round-trip check: stdio, then an HTTP daemon with two concurrent clients.

Run from the repo root with the venv active and a populated graph:

    .venv/bin/python evals/mcp_check.py
    .venv/bin/python evals/mcp_check.py "download README.md..." --input owner=x

Prints one PASS/FAIL line per check and exits non-zero on any failure.
"""

import argparse
import asyncio
import os
import subprocess
import sys
import time

import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from switchboard.executor import value

DEFAULT_TASK = "comment on the failing test issue in acme/api"
DEFAULT_INPUTS = {"repo": "acme/api", "message": "reproduced"}
HOST, PORT = "127.0.0.1", 8799

failures = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


async def round_trip(client, task, inputs):
    """List tools, then find + run. Returns (tools, plan, result)."""
    tools = sorted(tool.name for tool in (await client.list_tools()).tools)
    plan = value(await client.call_tool("find", {"task": task, "inputs": inputs}))
    result = value(await client.call_tool("run", {"plan_id": plan["plan_id"], "inputs": inputs}))
    return tools, plan, result


async def wait_port(timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _, writer = await asyncio.open_connection(HOST, PORT)
            writer.close()
            await writer.wait_closed()
            return True
        except OSError:
            await asyncio.sleep(0.2)
    return False


async def stdio_check(task, inputs):
    params = StdioServerParameters(
        command=".venv/bin/switchboard", args=["serve"], env=dict(os.environ)
    )
    async with Client(params) as client:
        tools, plan, result = await round_trip(client, task, inputs)
    check("stdio tools are exactly find, run", tools == ["find", "run"], str(tools))
    check("stdio find returns steps", bool(plan.get("steps")), f"plan={plan.get('plan_id')}")
    check("stdio run ok", result.get("ok") is True, f"steps={len(result.get('results', []))}")


async def http_client(task, inputs):
    async with httpx2.AsyncClient() as http, Client(
        streamable_http_client(f"http://{HOST}:{PORT}/mcp", http_client=http),
        read_timeout_seconds=30,
    ) as client:
        return await round_trip(client, task, inputs)


async def http_check(task, inputs):
    server = subprocess.Popen(
        [".venv/bin/switchboard", "serve", "--http", f"{HOST}:{PORT}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not await wait_port():
            check("http daemon accepts connections", False)
            return
        a, b = await asyncio.gather(http_client(task, inputs), http_client(task, inputs))
    finally:
        server.terminate()
        server.wait(timeout=10)
    for label, (tools, plan, result) in (("A", a), ("B", b)):
        check(f"http client {label} sees find, run", tools == ["find", "run"], str(tools))
        check(f"http client {label} run ok", result.get("ok") is True, f"plan={plan.get('plan_id')}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task", nargs="?", default=DEFAULT_TASK)
    parser.add_argument("--input", action="append", default=[])
    args = parser.parse_args()
    inputs = dict(item.partition("=")[::2] for item in args.input) or DEFAULT_INPUTS

    await stdio_check(args.task, inputs)
    await http_check(args.task, inputs)

    print(f"{'OK' if not failures else 'FAILED'}: {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
