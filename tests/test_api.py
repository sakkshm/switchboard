"""API contracts with explicit graph fixtures; no production topology fallback."""

import copy
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.executor import catalog_hash
from app.main import create_app
from app.metrics import bounded_context, token_count
from catalog import load_catalog
from catalog.fixture_server import call_tool

CHAINS = [
    ["repo_search", "list_issues", "get_issue", "add_comment"],
    ["locate_file", "read_file", "get_diff", "post_review"],
    ["search_logs", "get_trace", "open_ticket", "notify_channel"],
    ["find_account", "list_orders", "get_order", "issue_refund"],
]
TASK = "comment on the failing test issue in acme/api"
INPUTS = {"repo": "acme/api", "message": "Reviewed in API contract test"}
TASKS_PATH = Path(__file__).parents[1] / "evals" / "tasks.jsonl"
GOLDEN_TASKS = [json.loads(line) for line in TASKS_PATH.read_text().splitlines()]


class ContractGraph:
    """Canned graph responses for API tests, not an alternative planner backend."""

    def __init__(self, catalog):
        self.catalog = catalog
        self.catalog_version = catalog_hash(catalog)
        self.hydra = self
        self.query_count = 0
        self.path_calls = 0
        self.available = True
        self.paths_exist = True
        self.plans = {}
        self.decisions = []

    def query(self, query):
        self.query_count += 1
        if not self.available:
            raise RuntimeError("backend unavailable")
        return [[len(self.catalog) if self.paths_exist else 0]]

    def grants(self, principal):
        self.query_count += 1
        if not self.available:
            raise RuntimeError("backend unavailable")
        if principal not in {"demo", "readonly"}:
            return set()
        return {tool["key"] for tool in self.catalog if principal == "demo" or tool["risk"] == "read"}

    def paths(self, candidates, goal):
        self.path_calls += 1
        self.query_count += 1
        if not self.paths_exist:
            return []
        return [{"chain": list(chain), "weights": [0, 0, 0]} for chain in CHAINS if chain[-1] == goal]

    def audit(self, decision):
        self.decisions.append(copy.deepcopy(decision))

    def load_plan(self, intent_hash, principal):
        return copy.deepcopy(self.plans.get((principal, intent_hash)))

    def save_plan(self, plan, principal):
        self.plans[principal, plan["intent_hash"]] = copy.deepcopy(plan)

    def restore_plans(self):
        return len(self.plans)

    def learn(self, chain, success, failed_index=None):
        pass

    def plan_usage(self):
        return dict(Counter(step["tool"] for plan in self.plans.values() for step in plan["steps"]))


class FixtureTransport:
    """In-process fixture here; real subprocess transport is covered separately."""

    def __init__(self):
        self.calls = []

    async def call(self, tool, args):
        self.calls.append(tool["key"])
        return await call_tool(tool["name"], args)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXTURE_DATA_DIR", str(tmp_path))
    catalog = load_catalog()
    store, transport = ContractGraph(catalog), FixtureTransport()
    app = create_app(store=store, catalog=catalog, transport=transport)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, store, transport, tmp_path


def test_compare_executes_then_reuses_persisted_plan(api):
    client, store, transport, directory = api
    first = client.post("/compare", json={"task": TASK, "inputs": INPUTS})
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["trace"]["status"] == "success", data
    assert data["baseline"]["tools"] == 60
    assert data["baseline"]["live_execution"] is False
    assert data["switchboard"]["tools"] == 4
    assert data["switchboard"]["tokens"] < data["baseline"]["tokens"]
    assert data["switchboard"]["graph_queries"] == 1
    assert data["switchboard"]["tool_calls"] == 4
    assert transport.calls == CHAINS[0]
    assert (directory / "actions.jsonl").exists()
    assert client.get("/trace/" + data["trace"]["id"]).json() == data["trace"]
    second = client.post("/compare", json={"task": TASK, "inputs": {**INPUTS, "message": "Second run"}}).json()
    assert second["trace"]["status"] == "success", second
    assert second["switchboard"]["reused"] is True
    assert second["switchboard"]["graph_queries"] == 0
    assert store.path_calls == 1
    records = [json.loads(line) for line in (directory / "actions.jsonl").read_text().splitlines()]
    assert [record["arguments"]["body"] for record in records] == [INPUTS["message"], "Second run"]
    assert "switchboard_reuse_hits_total 1" in client.get("/metrics").text


def test_readonly_filters_discovery_and_rechecks_existing_plan(api):
    client, store, transport, directory = api
    response = client.post("/plan", json={"task": TASK, "inputs": INPUTS})
    assert response.status_code == 200, response.text
    plan = response.json()
    denied = client.post("/plan", json={"task": TASK, "inputs": INPUTS, "principal": "readonly"})
    assert denied.status_code == 404
    assert not transport.calls
    trace = client.post("/run/" + plan["key"], json={"inputs": INPUTS, "principal": "readonly"}).json()
    assert trace["status"] == "blocked", trace
    assert trace["steps"][-1]["tool"] == "add_comment"
    assert trace["steps"][-1]["status"] == "blocked"
    assert transport.calls == CHAINS[0][:-1]
    assert not (directory / "actions.jsonl").exists()
    assert store.decisions[-1]["verdict"] == "blocked"
    available = client.get("/api/catalog?principal=readonly").json()["tools"]
    assert all(tool["risk"] == "read" for tool in available)


def test_destructive_plan_requires_explicit_confirmation(api):
    client, _, _, directory = api
    task = GOLDEN_TASKS[-1]
    assert task["expected_chain"][-1] == "issue_refund"
    inputs = task["expected_values"]
    response = client.post("/plan", json={"task": task["task"], "inputs": inputs})
    assert response.status_code == 200, response.text
    plan = response.json()
    denied = client.post("/run/" + plan["key"], json={"inputs": inputs}).json()
    assert denied["status"] == "blocked", denied
    assert not (directory / "actions.jsonl").exists()
    token = client.post("/confirm/" + plan["key"], json={}).json()["confirmation_token"]
    success = client.post("/run/" + plan["key"], json={"inputs": inputs, "confirmation_token": token}).json()
    assert success["status"] == "success", success


def test_empty_graph_removes_plans_and_backend_readiness_is_honest(api):
    client, store, transport, _ = api
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").json()["ready"] is True
    store.paths_exist = False
    assert client.get("/readyz").status_code == 503
    assert client.post("/plan", json={"task": TASK, "inputs": INPUTS}).status_code == 404
    assert not transport.calls
    store.available = False
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 503
    assert client.post("/plan", json={"task": TASK, "inputs": INPUTS}).status_code == 503


def rpc(client, method, params=None, principal="demo"):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"x-agent-key": principal},
    )


def test_mcp_exposes_three_tools_and_executes_plan(api):
    client, _, _, _ = api
    initialized = rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
    ).json()
    assert initialized["result"]["serverInfo"]["name"] == "switchboard"
    notification = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert notification.status_code == 202
    listed = rpc(client, "tools/list").json()["result"]["tools"]
    assert [tool["name"] for tool in listed] == ["find", "describe", "run"]
    result = rpc(client, "tools/call", {"name": "find", "arguments": {"task": TASK, "inputs": INPUTS}}).json()["result"]
    assert result["isError"] is False, result
    found = json.loads(result["content"][0]["text"])
    assert [tool["name"] for tool in found["tools"]] == CHAINS[0]
    result = rpc(client, "tools/call", {"name": "describe", "arguments": {"tool": "add_comment"}}).json()["result"]
    assert json.loads(result["content"][0]["text"])["type"] == "object"
    blocked = rpc(
        client, "tools/call", {"name": "describe", "arguments": {"tool": "add_comment"}}, principal="readonly"
    ).json()
    assert blocked["result"]["isError"] is True
    result = rpc(
        client, "tools/call", {"name": "run", "arguments": {"plan_id": found["plan_id"], "inputs": INPUTS}}
    ).json()["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"])["status"] == "success"


def test_missing_resources_and_invalid_requests_are_explicit(api):
    client, _, _, _ = api
    assert client.get("/trace/missing").status_code == 404
    assert client.post("/run/missing", json={}).status_code == 404
    assert client.post("/plan", json={"task": ""}).status_code == 422
    assert rpc(client, "unknown").json()["error"]["code"] == -32601
    malformed = client.post("/mcp", content="{bad", headers={"content-type": "application/json"})
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == -32700
    params = rpc(client, "tools/call", {"name": "find", "arguments": {"task": TASK, "extra": True}}).json()
    assert params["error"]["code"] == -32602


def test_untrusted_browser_origin_is_rejected(api):
    client, _, _, _ = api
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"origin": "https://unrelated.example"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "task", [GOLDEN_TASKS[index] for index in (0, 6, 12, 18)], ids=["issue", "review", "incident", "account"]
)
def test_each_family_runs_to_its_expected_end_state(api, task):
    client, _, _, directory = api
    inputs = task["expected_values"]
    response = client.post("/plan", json={"task": task["task"], "inputs": inputs})
    assert response.status_code == 200, response.text
    plan = response.json()
    token = client.post("/confirm/" + plan["key"], json={}).json()["confirmation_token"]
    trace = client.post("/run/" + plan["key"], json={"inputs": inputs, "confirmation_token": token}).json()
    assert trace["status"] == "success", trace
    assert [step["tool"] for step in trace["steps"]] == task["expected_chain"]
    record = json.loads((directory / "actions.jsonl").read_text())
    assert record["tool"] == task["expected_chain"][-1]


def test_twenty_concurrent_comparisons_complete_without_dropped_writes(api):
    client, _, _, directory = api

    def run(index):
        return client.post("/compare", json={"task": TASK, "inputs": {**INPUTS, "message": f"Run {index}"}})

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(run, range(20)))
    assert all(result.status_code == 200 for result in results)
    assert all(result.json()["trace"]["status"] == "success" for result in results)
    records = [json.loads(line) for line in (directory / "actions.jsonl").read_text().splitlines()]
    assert len(records) == 20
    assert {record["arguments"]["body"] for record in records} == {f"Run {index}" for index in range(20)}


@pytest.mark.parametrize("budget", [64, 128, 3000])
def test_bounded_context_is_valid_json_under_the_token_budget(budget):
    value = {"text": '日本語 \\"quoted\\"\n💡 <|endoftext|> ' * 10000}
    bounded = bounded_context(value, budget)
    parsed = json.loads(bounded)
    assert parsed["truncated"] is True
    assert parsed["original_tokens"] > budget
    assert token_count(bounded) <= budget
    original = {"status": "small", "value": 42}
    assert json.loads(bounded_context(original, budget)) == original


def test_bounded_run_keeps_outcome_and_trace_reference():
    envelope = {
        "trace_id": "trace-123",
        "status": "success",
        "metrics": {"tool_calls": 4, "wall_ms": 123},
        "error": None,
        "result": {"body": "long response " * 10000},
    }
    bounded = bounded_context(envelope, 128)
    parsed = json.loads(bounded)
    for key in ("trace_id", "status", "metrics", "error"):
        assert parsed[key] == envelope[key]
    assert parsed["result"]["truncated"] is True
    assert token_count(bounded) <= 128


def test_context_budget_refuses_whole_fresh_and_reused_chains(api):
    client, _, transport, _ = api
    refused = client.post("/plan", json={"task": TASK, "inputs": INPUTS, "context_budget_tokens": 128})
    assert refused.status_code == 404
    assert "context budget" in refused.json()["detail"]
    assert not transport.calls
    successful = client.post("/compare", json={"task": TASK, "inputs": INPUTS}).json()
    assert successful["trace"]["status"] == "success"
    calls = len(transport.calls)
    reused = client.post("/plan", json={"task": TASK, "inputs": INPUTS, "context_budget_tokens": 128})
    assert reused.status_code == 404
    assert "context budget" in reused.json()["detail"]
    assert len(transport.calls) == calls
