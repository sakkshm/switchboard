"""Deterministic, synthetic MCP stdio server; writes stay in FIXTURE_DATA_DIR.

This small server implements the MCP JSON-RPC stdio transport directly. The
fixture has no remote dependencies and works with any MCP ClientSession.
"""

import asyncio
import hashlib
import json
import os
import sys
import threading
from pathlib import Path

from catalog import load_catalog

_LOCK = threading.Lock()


def _id(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:12]


def _write(name, arguments):
    directory = Path(os.getenv("FIXTURE_DATA_DIR", ".data/fixture")).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "actions.jsonl"
    record = {"tool": name, "arguments": arguments, "id": _id(json.dumps(arguments, sort_keys=True))}
    with _LOCK, destination.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
    return {"ok": True, "id": record["id"], "side_effect_file": str(destination), **arguments}


def execute_tool(name, arguments):
    """The same deterministic implementation used by tools/call and tests."""
    tools = {tool["name"]: tool for tool in load_catalog()}
    if name not in tools:
        raise KeyError(f"Unknown fixture tool: {name}")
    tool = tools[name]
    schema = tool["inputSchema"]
    if not isinstance(arguments, dict):
        raise TypeError("arguments must be an object")
    unknown = set(arguments) - set(schema["properties"])
    missing = set(schema["required"]) - set(arguments)
    if missing or unknown:
        raise ValueError(f"Invalid arguments: missing={sorted(missing)}, unknown={sorted(unknown)}")
    for field, value in arguments.items():
        expected = schema["properties"][field]["type"]
        valid = isinstance(value, str) if expected == "string" else isinstance(value, int) and not isinstance(value, bool)
        if not valid:
            raise ValueError(f"Invalid type for {field}: expected {expected}")
    mode = os.getenv("FIXTURE_FAIL_MODE", "")
    failure_tool = os.getenv("FIXTURE_FAIL_TOOL", "")
    if mode and (not failure_tool or failure_tool == name):
        if mode == "error":
            raise RuntimeError("Injected upstream error")
        if mode == "oversized":
            return {"payload": "x" * 300_000}
    family = tool["family"]
    stage = tool["stage"]
    # Alternate graph paths are structurally legal but return stale identifiers.
    # Terminal checks expose the error instead of pretending the task succeeded.
    stale = tool.get("distractor", False)
    if family == "issue":
        if stage == 0:
            return {"full_name": arguments["query"], "source": "synthetic"}
        if stage == 1:
            return [{"number": -142 if stale else 142, "repo": arguments["repo"], "title": "Failing test"}]
        if stage == 2:
            return {"number": arguments["number"], "repo": arguments["repo"], "title": "Failing test"}
        if stage == 3:
            if arguments["number"] != 142:
                raise ValueError("Issue does not exist in the current fixture snapshot")
            return _write(name, arguments)
    if family == "review":
        if stage == 0:
            return {"path": arguments["query"]}
        if stage == 1:
            return {"path": arguments["path"], "content": "stale" if stale else "def authenticate(): return True"}
        if stage == 2:
            return {"path": arguments["path"], "diff": "stale" if arguments["content"] == "stale" else "+def authenticate(): return True"}
        if stage == 3:
            if arguments["diff"] == "stale":
                raise ValueError("Cannot review a stale diff")
            return _write(name, arguments)
    if family == "incident":
        if stage == 0:
            return {"trace_id": "trace-" + _id(arguments["service"]), "service": arguments["service"]}
        if stage == 1:
            return {"trace_id": arguments["trace_id"], "summary": "stale" if stale else "Synthetic checkout timeout"}
        if stage == 2:
            # This tool prepares a ticket; only notify_channel commits a write.
            return {"ticket_id": "stale" if arguments["summary"] == "stale" else "INC-142", "summary": arguments["summary"]}
        if stage == 3:
            if arguments["ticket_id"] != "INC-142":
                raise ValueError("Ticket is not current")
            return _write(name, arguments)
    if family == "account":
        if stage == 0:
            return {"account_id": "account-" + _id(arguments["query"])}
        if stage == 1:
            return [{"order_id": "stale" if stale else "order-142", "account_id": arguments["account_id"]}]
        if stage == 2:
            return {"order_id": arguments["order_id"], "amount": 1200, "currency": "USD"}
        if stage == 3:
            if arguments["order_id"] != "order-142":
                raise ValueError("Order is not refundable")
            return _write(name, arguments)
    if tool["risk"] != "read":
        return _write(name, arguments)
    return {"ok": True, "tool": name, "data": "synthetic fixture", **arguments}


async def call_tool(name, arguments):
    if os.getenv("FIXTURE_FAIL_MODE") == "timeout" and os.getenv("FIXTURE_FAIL_TOOL", name) == name:
        await asyncio.sleep(30)
    return execute_tool(name, arguments)


def tools_list():
    return [{key: tool[key] for key in ("name", "description", "inputSchema", "annotations")}
            for tool in load_catalog()]


async def dispatch(message):
    method = message.get("method")
    params = message.get("params", {})
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "switchboard-synthetic-fixture", "version": "1.0.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": tools_list()}
    if method == "tools/call":
        try:
            result = await call_tool(params["name"], params.get("arguments", {}))
            response = {"content": [{"type": "text", "text": json.dumps(result)}], "isError": False}
            if isinstance(result, dict):
                response["structuredContent"] = result
            return response
        except (ValueError, KeyError, RuntimeError, TypeError) as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    raise KeyError("Method not found")


async def serve():
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            if "id" not in message:  # MCP lifecycle and cancellation notifications
                continue
            try:
                response = {"jsonrpc": "2.0", "id": message["id"], "result": await dispatch(message)}
            except KeyError:
                response = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Method not found"}}
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(serve())
