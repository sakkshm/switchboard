import asyncio
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from app.matcher import Matcher
from catalog import load_catalog
from catalog.build import build_catalog, build_tasks
from catalog.fixture_server import call_tool, execute_tool
from evals.chain_check import check_chain

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_snapshot_and_all_golden_bindings():
    catalog = load_catalog()
    assert catalog == build_catalog()
    assert len({tool["key"] for tool in catalog}) == 60
    assert Counter(tool["family"] for tool in catalog) == dict.fromkeys(("issue", "review", "incident", "account"), 15)
    tools = {tool["key"]: tool for tool in catalog}
    for task in build_tasks():
        check_chain(task["expected_chain"], tools)
    malformed = ["list_issues", "repo_search", "get_issue", "add_comment"]
    with pytest.raises(AssertionError, match="missing types"):
        check_chain(malformed, tools)


def test_bm25_goals_allowed_scope_and_no_match():
    catalog = load_catalog()
    matcher = Matcher(catalog)
    for task in build_tasks():
        match = matcher.match(task["task"])
        assert match["goal"] == task["expected_chain"][-1]
        assert task["expected_chain"][0] in match["candidates"]
    allowed = {tool["key"] for tool in catalog if tool["risk"] == "read"}
    assert all(hit["key"] in allowed for hit in matcher.rank("comment on an issue", allowed=allowed))
    assert matcher.match("zzzyxqvv") == {"goal": None, "candidates": []}


def test_fixture_real_write_and_stale_result_rejection(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXTURE_DATA_DIR", str(tmp_path))
    arguments = {"repo": "acme/api", "number": 142, "body": "Investigating"}
    result = execute_tool("add_comment", arguments)
    assert result["ok"] is True
    assert json.loads((tmp_path / "actions.jsonl").read_text())["arguments"] == arguments
    with pytest.raises(ValueError, match="does not exist"):
        execute_tool("add_comment", {**arguments, "number": -142})
    assert len((tmp_path / "actions.jsonl").read_text().splitlines()) == 1


def test_fixture_errors_and_schema_validation(monkeypatch):
    with pytest.raises(KeyError):
        execute_tool("imaginary_tool", {})
    with pytest.raises(ValueError, match="unknown"):
        execute_tool("repo_search", {"query": "acme/api", "shell": "bad"})
    monkeypatch.setenv("FIXTURE_FAIL_MODE", "error")
    with pytest.raises(RuntimeError, match="Injected"):
        execute_tool("repo_search", {"query": "acme/api"})
    monkeypatch.setenv("FIXTURE_FAIL_MODE", "oversized")
    assert len(execute_tool("repo_search", {"query": "acme/api"})["payload"]) == 300_000


def test_fixture_timeout_is_cancellable(monkeypatch):
    monkeypatch.setenv("FIXTURE_FAIL_MODE", "timeout")

    async def run():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(call_tool("repo_search", {"query": "acme/api"}), timeout=0.02)

    asyncio.run(run())


def test_stdio_jsonrpc_roundtrip(tmp_path):
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "repo_search", "arguments": {"query": "acme/api"}}},
    ]
    environment = {**os.environ, "FIXTURE_DATA_DIR": str(tmp_path), "FIXTURE_FAIL_MODE": ""}
    process = subprocess.run([sys.executable, "-m", "catalog.fixture_server"], cwd=ROOT, env=environment,
                             input="".join(json.dumps(message) + "\n" for message in messages), capture_output=True, text=True, timeout=10, check=True)
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert [response["id"] for response in responses] == [1, 2, 3]
    assert responses[0]["result"]["capabilities"]["tools"] == {"listChanged": False}
    assert len(responses[1]["result"]["tools"]) == 60
    assert responses[2]["result"]["structuredContent"]["full_name"] == "acme/api"


def test_eval_does_not_report_backend_persistence_failure_as_bad_planning():
    from evals.run import EvaluationInfrastructureError, measure

    task = build_tasks()[0]
    catalog = load_catalog()

    class Planner:
        def plan(self, *args, **kwargs):
            return {"key": "example", "steps": [{"tool": key} for key in task["expected_chain"]], "graph_queries": 1}

    class Executor:
        async def execute(self, *args, **kwargs):
            return {"status": "error", "steps": [{"status": "success"} for _ in task["expected_chain"]],
                    "error": {"code": "execution_error", "message": "database write failed"},
                    "audit_error": "audit database unreachable"}

    async def run():
        with pytest.raises(EvaluationInfrastructureError, match="Persistence failed"):
            await measure(task, "static", Planner(), Executor(), Matcher(catalog), catalog, None, 0)

    asyncio.run(run())
