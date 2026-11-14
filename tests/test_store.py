import copy
import re
from unittest.mock import patch

import pytest
import requests

from app.store import GraphStore, Hydra, stable_id


class Response:
    ok = True

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_pagination_keeps_request_identity_and_consumes_repeated_cursor():
    sent = []

    def respond(url, **kwargs):
        body = copy.deepcopy(kwargs["json"])
        sent.append(body)
        return Response({"query_id": body["query_id"],
                         "rows": [[{"type": "integer", "value": len(sent)}]],
                         "next_cursor": 17 if len(sent) < 3 else None})

    hydra = Hydra()
    with patch("app.store.requests.post", side_effect=respond):
        assert hydra.query("MATCH (t:Tool) RETURN t.id", {"key": "example"}) == [[1], [2], [3]]
    assert len(sent) == hydra.query_count == 3
    assert "cursor" not in sent[0]
    assert sent[1]["cursor"] == sent[2]["cursor"] == 17
    for request in sent[1:]:
        assert {key: value for key, value in request.items() if key != "cursor"} == sent[0]


def test_failed_consuming_page_is_not_retried_or_returned_as_partial_success():
    calls = 0

    def respond(url, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise requests.Timeout("page response lost")
        return Response({"query_id": kwargs["json"]["query_id"],
                         "rows": [[{"type": "integer", "value": 1}]], "next_cursor": 17})

    with patch("app.store.requests.post", side_effect=respond):
        with pytest.raises(requests.Timeout):
            Hydra().query("MATCH (t:Tool) RETURN t.id")
    assert calls == 2


def test_new_logical_queries_get_different_restart_safe_ids():
    sent = []

    def respond(url, **kwargs):
        sent.append(kwargs["json"])
        return Response({"rows": [], "next_cursor": None})

    with patch("app.store.requests.post", side_effect=respond):
        Hydra().query("MATCH (t:Tool) RETURN count(*)")
        Hydra().query("MATCH (t:Tool) RETURN count(*)")
    assert sent[0]["query_id"] != sent[1]["query_id"]


def tool(key, risk="read", consumes=(), produces=()):
    return {"key": key, "name": key, "description": "Fixture tool", "server": "fixture",
            "inputSchema": {"type": "object"}, "consumes": list(consumes),
            "produces": list(produces), "risk": risk, "args": {}}


class RecordedGraph:
    """Record only the documented seed/query shapes; never connect to HydraDB."""

    def __init__(self):
        self.edges = {name: set() for name in ("GRANTED", "FEEDS", "CONSUMES", "PRODUCES")}
        self.tools = {}
        self.calls = []
        self.fail_delete = False

    def query(self, cypher, parameters=None):
        self.calls.append((cypher, copy.deepcopy(parameters)))
        if "RETURN a.id AS src, b.id AS dst" in cypher:
            relation = re.search(r"\[:(\w+)\]", cypher).group(1)
            return [list(pair) for pair in self.edges[relation]]
        if " DELETE r" in cypher:
            if self.fail_delete:
                raise RuntimeError("revocation unavailable")
            relation = re.search(r"\[r:(\w+)\]", cypher).group(1)
            self.edges[relation].discard((parameters["src"], parameters["dst"]))
        elif cypher.startswith("UNWIND") and "CREATE (a" in cypher:
            relation = re.search(r"\[:(\w+)\]", cypher).group(1)
            self.edges[relation].update((row["src"], row["dst"]) for row in parameters["rows"])
        elif "SET n:Tool" in cypher:
            self.tools.update({row["id"]: row for row in parameters["rows"]})
        elif "RETURN t.key AS key, t.risk AS risk" in cypher:
            aid = stable_id("Agent", parameters["key"])
            return [[self.tools[dst]["key"], self.tools[dst]["risk"]]
                    for src, dst in self.edges["GRANTED"] if src == aid and dst in self.tools]
        return []


def test_reseed_revokes_promoted_write_before_updating_risk_and_preserves_unrelated_edges():
    graph = RecordedGraph()
    initial = [tool("lookup", produces=["old_type"]), tool("followup", consumes=["old_type"])]
    GraphStore(initial, graph).seed()
    unrelated = (stable_id("Tool", "other_source"), stable_id("Tool", "other_target"))
    graph.edges["FEEDS"].add(unrelated)
    outsider_grant = (stable_id("Agent", "other_agent"), stable_id("Tool", "lookup"))
    graph.edges["GRANTED"].add(outsider_grant)
    changed = [tool("lookup", risk="write", produces=["new_type"]), tool("followup", consumes=["old_type"])]
    start = len(graph.calls)
    store = GraphStore(changed, graph)
    store.seed()
    assert "lookup" not in store.grants("readonly")
    assert "followup" in store.grants("readonly")
    assert unrelated in graph.edges["FEEDS"]
    assert outsider_grant in graph.edges["GRANTED"]
    assert (stable_id("Tool", "lookup"), stable_id("Tool", "followup")) not in graph.edges["FEEDS"]
    assert (stable_id("Tool", "lookup"), stable_id("Type", "old_type")) not in graph.edges["PRODUCES"]
    calls = graph.calls[start:]
    revoke = next(i for i, (q, _) in enumerate(calls) if "[r:GRANTED]" in q and "DELETE r" in q)
    update = next(i for i, (q, _) in enumerate(calls) if "SET n:Tool" in q)
    assert revoke < update
    before = copy.deepcopy(graph.edges)
    store.seed()
    assert graph.edges == before


def test_failed_revocation_stops_seed_before_risk_update():
    graph = RecordedGraph()
    GraphStore([tool("lookup")], graph).seed()
    graph.fail_delete = True
    start = len(graph.calls)
    with pytest.raises(RuntimeError, match="revocation"):
        GraphStore([tool("lookup", risk="write")], graph).seed()
    assert not any("SET n:Tool" in q for q, _ in graph.calls[start:])
    assert graph.tools[stable_id("Tool", "lookup")]["risk"] == "read"


def test_runtime_readonly_guard_denies_stale_grants_against_current_catalog():
    graph = RecordedGraph()
    GraphStore([tool("lookup")], graph).seed()
    changed_store = GraphStore([tool("lookup", risk="write")], graph)
    assert changed_store.grants("readonly") == set()
    assert changed_store.grants("demo") == {"lookup"}


def test_singleton_membership_requires_a_labelled_graph_match():
    class Membership:
        def query(self, cypher, parameters):
            assert "MATCH (t:Tool {key: $key})" in cypher
            return [["lookup"]] if parameters["key"] == "lookup" else []

    store = GraphStore([tool("lookup")], Membership())
    assert store.exists("lookup")
    assert not store.exists("removed")
