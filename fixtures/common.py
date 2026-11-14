"""Trusted fixture metadata, capability headers, and MCP protocol helpers."""

import asyncio
import copy
import json
import os
import re
import sys

from catalog import load_catalog
from catalog.build import build_tasks, tool
from catalog.fixture_server import _write, execute_tool

SERVER_NAMES = {"issue": "issues", "review": "repo", "incident": "incident", "account": "account"}
BUILTIN_MODULES = {"fixtures.issue_server": "issue", "fixtures.repo_server": "review",
                   "fixtures.incident_server": "incident", "fixtures.account_server": "account", "fixtures.echo_server": "echo"}
HEADER = re.compile(r"\[consumes:\s*([^|]*)\|\s*produces:\s*([^]]*)\]\s*$")


def parse_header(description):
    match = HEADER.search(description)
    if not match:
        raise ValueError("Missing capability header")
    return tuple([item.strip() for item in group.split(",") if item.strip()] for group in match.groups())


def _bindings(name, family, stage):
    canonical = {
        "issue": [
            {"query": {"input": "repo"}},
            {"repo": {"type": "repo", "input": "repo"}},
            {"repo": {"type": "repo", "input": "repo"}, "number": {"type": "issue_number"}},
            {"repo": {"type": "repo", "input": "repo"}, "number": {"type": "issue"}, "body": {"input": "message"}},
        ],
        "review": [
            {"query": {"input": "path"}},
            {"path": {"type": "file_path"}},
            {"path": {"type": "file_path"}, "content": {"type": "file_content"}},
            {"diff": {"type": "diff"}, "body": {"input": "message"}},
        ],
        "incident": [
            {"service": {"input": "service"}},
            {"trace_id": {"type": "trace_id"}},
            {"summary": {"type": "trace"}},
            {"ticket_id": {"type": "ticket"}, "channel": {"input": "channel"}, "body": {"input": "message"}},
        ],
        "account": [
            {"query": {"input": "account"}},
            {"account_id": {"type": "account_id"}},
            {"order_id": {"type": "order_id"}},
            {"order_id": {"type": "order"}, "reason": {"input": "reason"}},
        ],
    }
    return copy.deepcopy(canonical[family][stage]) if stage < 4 else {"query": {"input": "query"}}


def tool_table(family=None):
    if family == "echo":
        return echo_tools()
    rows = copy.deepcopy(load_catalog())
    # Keep exactly fifteen tools per upstream while adding two distinct terminal chains.
    replaced = {"list_labels", "read_label", "list_services", "read_service"}
    rows = [record for record in rows if record["key"] not in replaced]
    variants = [
        tool("list_comments", "List comments for an issue before closing it", "issue", 4, ["issue"], ["comment_list"],
             {"number": "$steps.1.result.number"}, outputs={"comment_list": "issue_number"}),
        tool("close_issue", "Close an issue after reviewing its comments", "issue", 4, ["comment_list"], ["closed_issue"],
             {"number": "$steps.2.result.issue_number", "reason": "$input.message"}, outputs={"closed_issue": "id"}, risk="write"),
        tool("find_oncall", "Find the on-call responder for an incident trace", "incident", 4, ["trace"], ["oncall"],
             {"summary": "$steps.1.result.summary"}, outputs={"oncall": "responder"}),
        tool("page_oncall", "Page the on-call responder for a service incident", "incident", 4, ["oncall"], ["page"],
             {"responder": "$steps.2.result.responder", "body": "$input.message"}, outputs={"page": "id"}, risk="write"),
    ]
    rows.extend(variants)
    variant_bindings = {
        "list_comments": {"number": {"type": "issue"}},
        "close_issue": {"number": {"type": "comment_list"}, "reason": {"input": "message"}},
        "find_oncall": {"summary": {"type": "trace"}},
        "page_oncall": {"responder": {"type": "oncall"}, "body": {"input": "message"}},
    }
    for record in rows:
        record["server"] = SERVER_NAMES[record["family"]]
        record["bindings"] = variant_bindings.get(record["key"], _bindings(record["key"], record["family"], record["stage"]))
        record["input_types"] = {"repo": "repo"} if record["key"] in {"list_issues", "get_issue", "list_archived_issues", "list_cached_issues", "add_comment"} else {}
        for produced in record["produces"]:
            record["outputs"].setdefault(produced, "data")
    result = [record for record in rows if family is None or record["family"] == family]
    assert len(result) == (60 if family is None else 15)
    return result


def echo_tools():
    first = tool("echo_a", "Echo a value so it can be played back", "echo", 0, [], ["echo_value"],
                 {"value": "$input.value"}, outputs={"echo_value": "value"})
    second = tool("echo_b", "Play back an echoed value; echo then play it back", "echo", 1, ["echo_value"], ["echo_playback"],
                  {"value": "$steps.0.result.value"}, outputs={"echo_playback": "value"})
    first.update(server="echo", bindings={"value": {"input": "value"}}, input_types={})
    second.update(server="echo", bindings={"value": {"type": "echo_value"}}, input_types={})
    return [first, second]


def tools_list(family):
    return [{**{key: record[key] for key in ("name", "description", "inputSchema", "annotations")},
             "_meta": {"switchboard": {key: record[key] for key in ("args", "outputs", "bindings", "input_types", "risk", "version")}}}
            for record in tool_table(family)]


async def call_tool(name, arguments, family):
    records = {record["name"]: record for record in tool_table(family)}
    if name not in records:
        raise KeyError(f"Unknown tool on {family} fixture: {name}")
    record = records[name]
    schema = record["inputSchema"]
    if not isinstance(arguments, dict) or set(arguments) != set(schema["required"]):
        raise ValueError("Arguments do not match the fixture schema")
    for key, value in arguments.items():
        expected = schema["properties"][key]["type"]
        if (expected == "integer" and (not isinstance(value, int) or isinstance(value, bool))) or (expected == "string" and not isinstance(value, str)):
            raise ValueError(f"Invalid type for {key}")
    failure = os.getenv("SWITCHBOARD_FAIL_TOOL", "").split(".")[-1]
    if failure in {"*", name}:
        mode = os.getenv("SWITCHBOARD_FAIL_MODE", "error")
        if mode == "timeout":
            await asyncio.sleep(30)
        elif mode == "oversized":
            return {"payload": "x" * 300_000}
        else:
            raise RuntimeError(f"Injected failure for {name}")
    if family == "echo":
        return {"value": arguments["value"], "played_back": name == "echo_b"}
    if name == "list_comments":
        return {"issue_number": arguments["number"], "comments": [{"body": "Tests are now passing"}]}
    if name == "close_issue":
        if arguments["number"] != 142:
            raise ValueError("Unknown issue")
        return _write(name, arguments)
    if name == "find_oncall":
        if arguments["summary"] == "stale":
            raise ValueError("Cannot page from a stale trace")
        return {"responder": "fixture-oncall", "summary": arguments["summary"]}
    if name == "page_oncall":
        if arguments["responder"] != "fixture-oncall":
            raise ValueError("Unknown responder")
        return _write(name, arguments)
    return execute_tool(name, arguments)


async def dispatch(message, family):
    method, params = message.get("method"), message.get("params", {})
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-03-26"), "capabilities": {"tools": {}},
                "serverInfo": {"name": f"switchboard-{family}-fixture", "version": "2.0.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": tools_list(family)}
    if method == "tools/call":
        try:
            result = await call_tool(params["name"], params.get("arguments", {}), family)
            response = {"content": [{"type": "text", "text": json.dumps(result)}], "isError": False}
            if isinstance(result, dict):
                response["structuredContent"] = result
            return response
        except (KeyError, ValueError, RuntimeError, TypeError) as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    raise KeyError("Method not found")


async def serve_async(family):
    for line in sys.stdin:
        try:
            message = json.loads(line)
            if "id" not in message:
                continue
            try:
                response = {"jsonrpc": "2.0", "id": message["id"], "result": await dispatch(message, family)}
            except KeyError:
                response = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Method not found"}}
        except (json.JSONDecodeError, TypeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


def serve(family):
    asyncio.run(serve_async(family))


def golden_tasks():
    records = build_tasks()
    train_limits = {"issue": 5, "review": 5, "incident": 4, "account": 4}
    counts = dict.fromkeys(train_limits, 0)
    for record in records:
        family = record["family"]
        counts[family] += 1
        record["split"] = "train" if counts[family] <= train_limits[family] else "holdout"
        record["expected_chain"] = [f"{SERVER_NAMES[family]}.{name}" for name in record["expected_chain"]]
        record["allow_writes"] = family == "account"
        record.pop("confirmation_token", None)
    return records
