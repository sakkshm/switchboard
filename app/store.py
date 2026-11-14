"""HydraDB OSS HTTP client with a typed-cell decoder."""

import copy
import hashlib
import json
import os
import re
import threading
import uuid
from datetime import UTC, datetime

import requests

DEFAULT_TOKEN = "local-development-token-32-bytes"


def decode(value):
    """Decode row cells (`{"type","value"}`) and path-tagged values (`{"String": ...}`)."""
    if isinstance(value, dict):
        if value == {"type": "null"}:
            return None
        if set(value) == {"type", "value"}:
            return decode(value["value"])
        if len(value) == 1:
            tag, inner = next(iter(value.items()))
            if tag and tag[0].isupper():
                return decode(inner)
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


class Hydra:
    def __init__(self, url=None, token=None, graph=None, cell_id="cell-0"):
        self.url = (url or os.getenv("HYDRADB_URL", "http://127.0.0.1:18443")).rstrip("/")
        self.token = token or os.getenv("HYDRADB_TOKEN", DEFAULT_TOKEN)
        self.graph = graph or os.getenv("HYDRADB_GRAPH", "default")
        self.cell_id = cell_id
        self.query_count = 0

    def query(self, cypher, parameters=None):
        # OSS 0.4.0's default HTTP counter restarts at 1, while mutation
        # identities persist in object storage. A fresh logical query needs a UUID.
        body = {"cell_id": self.cell_id, "query_id": f"switchboard-{uuid.uuid4().hex}",
                "query": cypher, "page_size": 1000}
        if parameters is not None:
            body["parameters"] = parameters
        rows = []
        # Cursors are consuming handles, not offsets. The same cursor can be
        # returned repeatedly while advancing; never retry a page or stop merely
        # because its cursor equals the previous one. Failure discards all rows.
        for _ in range(10000):
            self.query_count += 1
            response = requests.post(
                f"{self.url}/v1/graphs/{self.graph}/query",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "X-Graph-Namespace": self.graph,
                    "Content-Type": "application/json",
                },
                json=dict(body),
                timeout=30,
            )
            if not response.ok:
                raise RuntimeError(f"{response.status_code}: {response.text[:500]}")
            page = response.json()
            if not isinstance(page, dict) or not isinstance(page.get("rows"), list):
                raise RuntimeError("HydraDB returned a malformed query response")
            rows.extend([[decode(cell) for cell in row] for row in page["rows"]])
            cursor = page.get("next_cursor")
            if cursor is None:
                return rows
            if page.get("query_id") != body["query_id"]:
                raise RuntimeError("HydraDB returned a cursor for a different query")
            body["cursor"] = cursor
        raise RuntimeError("HydraDB pagination exceeded the page limit; no partial result returned")


def stable_id(label, key):
    """Disjoint, deterministic identities within the engine's signed integer range."""
    return int(hashlib.sha256(f"{label}:{key}".encode()).hexdigest()[:15], 16)


def chunks(rows, size=500):
    for offset in range(0, len(rows), size):
        yield rows[offset:offset + size]


class GraphStore:
    """Only HydraDB supplies topology; caches hold successfully persisted plans."""

    def __init__(self, catalog=None, hydra=None, gateway=False):
        self.hydra = hydra or Hydra()
        self.catalog = list(catalog or [])
        self.gateway = gateway
        self._plan_cache = {}
        self._lock = threading.RLock()
        self.catalog_version = hashlib.sha256(json.dumps(
            sorted(self.catalog, key=lambda tool: tool["key"]), sort_keys=True,
            separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()).hexdigest()

    def seed(self, catalog=None):
        catalog = catalog or self.catalog
        keys = [tool["key"] for tool in catalog]
        if len(set(keys)) != len(keys) or any(tool["risk"] not in {"read", "write", "destructive"}
                                            for tool in catalog):
            raise ValueError("Catalog keys must be unique and risks must be explicit")
        # Read all existing relationships before any mutation. In particular,
        # revoke a formerly-read tool's grant before updating its risk/schema.
        # A failure leaves less privilege, never an old grant on a new write.
        self._reconcile_edges(catalog)
        rows = [{"id": stable_id("Tool", t["key"]), "key": t["key"],
                 "name": t["name"], "server": t["server"],
                 "desc": t["description"], "risk": t["risk"],
                 "schema": json.dumps(t["inputSchema"]),
                 "consumes": json.dumps(t["consumes"]), "produces": json.dumps(t["produces"]),
                 "version": t.get("version", "1")} for t in catalog]
        for batch in chunks(rows):
            self.hydra.query(
                "UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Tool, "
                "n.key = row.key, n.name = row.name, n.server = row.server, "
                "n.desc = row.desc, n.risk = row.risk, n.schema = row.schema, "
                "n.consumes_json = row.consumes, n.produces_json = row.produces, n.version = row.version",
                {"rows": batch})
        types = sorted({x for t in catalog for x in t["consumes"] + t["produces"]})
        if types and not self.gateway:
            self.hydra.query("UNWIND $rows AS row MERGE (n {id: row.id}) "
                             "SET n:Type, n.name = row.name",
                             {"rows": [{"id": stable_id("Type", x), "name": x} for x in types]})
        edges = [{"src": stable_id("Tool", a["key"]), "dst": stable_id("Tool", b["key"])}
                 for a in catalog for b in catalog if a["key"] != b["key"]
                 and set(a["produces"]) & set(b["consumes"])]
        self._edges("FEEDS", edges)
        if self.gateway:
            existing = self.hydra.query("MATCH (a:Tool)-[r:FEEDS]->(b:Tool) "
                                        "RETURN a.id AS src, b.id AS dst, r.weight AS weight")
            owned = {(edge["src"], edge["dst"]) for edge in edges}
            initial = [{"src": int(src), "dst": int(dst), "w": 1.0}
                       for src, dst, weight in existing
                       if weight is None and (int(src), int(dst)) in owned]
            for batch in chunks(initial):
                self.hydra.query("UNWIND $rows AS row MATCH (a {id: row.src})-[r:FEEDS]->(b {id: row.dst}) "
                                 "SET r.weight = row.w", {"rows": batch})
            return {"tools": len(rows), "types": 0, "feeds": len(edges)}
        for rel, field in [("CONSUMES", "consumes"), ("PRODUCES", "produces")]:
            self._edges(rel, [{"src": stable_id("Tool", t["key"]),
                               "dst": stable_id("Type", x)} for t in catalog for x in t[field]])
        for agent in ("demo", "readonly"):
            self.hydra.query("UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Agent, n.key = row.key",
                             {"rows": [{"id": stable_id("Agent", agent), "key": agent}]})
            self._edges("GRANTED", [{"src": stable_id("Agent", agent),
                                     "dst": stable_id("Tool", t["key"])} for t in catalog
                                    if agent == "demo" or t["risk"] == "read"])
        return {"tools": len(rows), "types": len(types), "feeds": len(edges)}

    def _reconcile_edges(self, catalog):
        owned = {stable_id("Tool", tool["key"]): tool for tool in catalog}
        desired = {
            "FEEDS": {(aid, bid) for aid, a in owned.items() for bid, b in owned.items()
                      if aid != bid and set(a["produces"]) & set(b["consumes"])},
            "CONSUMES": {(tid, stable_id("Type", name)) for tid, tool in owned.items()
                         for name in tool["consumes"]},
            "PRODUCES": {(tid, stable_id("Type", name)) for tid, tool in owned.items()
                         for name in tool["produces"]},
            "GRANTED": {(stable_id("Agent", agent), tid) for agent in ("demo", "readonly")
                        for tid, tool in owned.items() if agent == "demo" or tool["risk"] == "read"},
        }
        obsolete = {}
        relations = ("FEEDS",) if self.gateway else ("GRANTED", "FEEDS", "CONSUMES", "PRODUCES")
        for relation in relations:
            if relation == "GRANTED":
                source_label, target_label = "Agent", "Tool"
            elif relation == "FEEDS":
                source_label, target_label = "Tool", "Tool"
            else:
                source_label, target_label = "Tool", "Type"
            existing = self.hydra.query(
                f"MATCH (a:{source_label})-[:{relation}]->(b:{target_label}) "
                "RETURN a.id AS src, b.id AS dst")
            pairs = {(int(src), int(dst)) for src, dst in existing}
            if relation == "GRANTED":
                principals = {stable_id("Agent", agent) for agent in ("demo", "readonly")}
                scoped = {(src, dst) for src, dst in pairs if src in principals and dst in owned}
            elif relation == "FEEDS":
                scoped = {(src, dst) for src, dst in pairs if src in owned and dst in owned}
            else:
                scoped = {(src, dst) for src, dst in pairs if src in owned}
            obsolete[relation] = scoped - desired[relation]
        # Pairwise DELETE is a supported OSS write form and is safe to repeat.
        # Never delete whole labels, unknown agents, nodes, or unrelated edges.
        for relation in relations:
            for src, dst in sorted(obsolete[relation]):
                self.hydra.query(f"MATCH (a {{id: $src}})-[r:{relation}]->(b {{id: $dst}}) DELETE r",
                                 {"src": src, "dst": dst})

    def _edges(self, relation, rows):
        if relation not in {"FEEDS", "CONSUMES", "PRODUCES", "GRANTED"}:
            raise ValueError("unknown relationship")
        for batch in chunks(rows):
            self.hydra.query(f"UNWIND $rows AS row CREATE (a {{id: row.src}})"
                             f"-[:{relation}]->(b {{id: row.dst}})", {"rows": batch})

    def paths(self, candidates, goal):
        keys = list(dict.fromkeys(candidates))[:10]
        if not keys or not goal:
            return []
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}", key) for key in keys + [goal]):
            raise ValueError("invalid catalog key")
        literals = ",".join(f"'{key}'" for key in keys)
        rows = self.hydra.query(
            "CALL algo.MSpaths({sourceLabel: 'Tool', sourceProperty: 'key', "
            f"sourceValues: [{literals}], targetLabel: 'Tool', targetProperty: 'key', "
            f"targetValues: ['{goal}'], relTypes: ['FEEDS'], relDirection: 'outgoing', "
            "maxLen: 6, pathCount: 10, pairwise: false, resultLimit: 50}) YIELD path RETURN path")
        paths = []
        for row in rows:
            path = row[0]
            if not isinstance(path, dict) or not path.get("nodes"):
                continue
            chain = [n["properties"]["key"] for n in path["nodes"]]
            weights = [float(r.get("properties", {}).get("weight") or 0)
                       for r in path.get("relationships", [])]
            if chain[-1] == goal:
                paths.append({"chain": chain, "weights": weights})
        return paths

    def exists(self, key):
        """Graph-backed membership for a singleton plan; labelled MATCH avoids phantom IDs."""
        return any(row[0] == key for row in self.hydra.query(
            "MATCH (t:Tool {key: $key}) RETURN t.key AS key", {"key": key}))

    def grants(self, principal):
        if self.gateway:
            return {tool["key"] for tool in self.catalog}
        rows = self.hydra.query(
            "MATCH (a:Agent {key: $key})-[:GRANTED]->(t:Tool) RETURN t.key AS key, t.risk AS risk",
            {"key": principal})
        if principal == "readonly":
            risks = {tool["key"]: tool["risk"] for tool in self.catalog}
            # Both the trusted running catalog and persisted graph must classify
            # it as read, even if a stale GRANTED edge survived an earlier seed.
            return {row[0] for row in rows if len(row) > 1 and row[1] == "read"
                    and risks.get(row[0]) == "read"}
        return {row[0] for row in rows}

    def audit(self, decision):
        if self.gateway:
            return
        key = uuid.uuid4().hex
        record = {"id": stable_id("Decision", key), "key": key,
                  "ts": datetime.now(UTC).isoformat(),
                  "agent": str(decision.get("agent", decision.get("principal", ""))),
                  "tool": str(decision.get("tool", "")),
                  "verdict": str(decision.get("verdict", "")),
                  "reason": str(decision.get("reason", "")),
                  "request_id": str(decision.get("request_id", "")),
                  "args_hash": str(decision.get("args_hash", ""))}
        self.hydra.query("UNWIND $rows AS row MERGE (d {id: row.id}) SET d:Decision, d.key = row.key, d.ts = row.ts, "
                         "d.agent = row.agent, d.tool = row.tool, d.verdict = row.verdict, "
                         "d.reason = row.reason, d.args_hash = row.args_hash, d.request_id = row.request_id", {"rows": [record]})

    def load_plan(self, intent_hash, principal):
        with self._lock:
            return copy.deepcopy(self._plan_cache.get((principal, intent_hash)))

    def restore_plans(self):
        """Hydrate persisted STEP order once at startup; runtime hits require no traversal."""
        rows = self.hydra.query("MATCH (p:Plan)-[s:STEP]->(t:Tool) "
                                "RETURN p.payload AS payload, p.agent AS agent, "
                                "p.intent_hash AS intent_hash, t.key AS tool, s.i AS i ORDER BY s.i")
        grouped = {}
        for payload, agent, intent_hash, tool, index in rows:
            if not payload:
                continue
            plan = json.loads(payload)
            key = (agent, intent_hash, plan["key"])
            item = grouped.setdefault(key, {"plan": json.loads(payload), "steps": []})
            item["steps"].append((int(index), tool))
        with self._lock:
            for key, item in grouped.items():
                plan = item["plan"]
                ordered = [tool for _, tool in sorted(item["steps"])]
                if (plan.get("catalog_hash") == self.catalog_version
                        and ordered == [s["tool"] for s in plan["steps"]]):
                    old = self._plan_cache.get(key[:2], {})
                    if plan.get("saved_at", "") >= old.get("saved_at", ""):
                        self._plan_cache[key[:2]] = plan
        return len(self._plan_cache)

    def get_plan(self, plan_id):
        """Read durable payload and actual STEP order for a separate CLI/client process."""
        rows = self.hydra.query("MATCH (p:Plan {key: $key})-[s:STEP]->(t:Tool) "
                                "RETURN p.payload AS payload, t.key AS tool, s.i AS i ORDER BY s.i",
                                {"key": plan_id})
        if not rows:
            return None
        plan = json.loads(rows[0][0])
        if plan.get("catalog_hash") != self.catalog_version:
            raise ValueError("Plan catalog version is stale; call find again")
        if [row[1] for row in rows] != [step["tool"] for step in plan["steps"]]:
            raise ValueError("Persisted STEP order does not match the plan")
        if [int(row[2]) for row in rows] != list(range(len(plan["steps"]))):
            raise ValueError("Persisted STEP indexes are not consecutive")
        return plan

    def save_plan(self, plan, principal="demo", executed=True):
        with self._lock:
            saved = copy.deepcopy(plan)
            old = self._plan_cache.get((principal, plan["intent_hash"]), {})
            saved["wins"] = old.get("wins", plan.get("wins", 0)) + int(executed)
            saved["saved_at"] = datetime.now(UTC).isoformat()
            pid = stable_id("Plan", f"{principal}:{plan['key']}")
            self.hydra.query("UNWIND $rows AS row MERGE (p {id: row.id}) SET p:Plan, p.key = row.key, "
                             "p.agent = row.agent, p.intent_hash = row.intent_hash, p.payload = row.payload, p.wins = row.wins",
                             {"rows": [{"id": pid, "key": plan["key"], "agent": principal,
                              "intent_hash": plan["intent_hash"], "payload": json.dumps(saved), "wins": saved["wins"]}]})
            for step in plan["steps"]:
                self.hydra.query(f"MERGE (p:Plan {{id: {pid}}})-[:STEP {{i: {step['i']}}}]"
                                 f"->(t:Tool {{id: {stable_id('Tool', step['tool'])}}})")
                if self.gateway:
                    self.hydra.query("UNWIND $rows AS row MATCH (p {id: row.src})-[s:STEP]->(t {id: row.dst}) "
                                     "SET s.args = row.args", {"rows": [{"src": pid,
                                     "dst": stable_id("Tool", step["tool"]), "args": json.dumps(step["args"])}]})
            if not self.gateway:
                self.hydra.query(f"MERGE (p:Plan {{id: {pid}}})-[:BY]"
                                 f"->(a:Agent {{id: {stable_id('Agent', principal)}}})")
            self._plan_cache[(principal, plan["intent_hash"])] = saved

    def learn(self, chain, success, failed_index=None):
        with self._lock:
            updates = []
            for index, (a, b) in enumerate(zip(chain, chain[1:], strict=False), start=1):
                # A failed step says nothing about edges that were never reached.
                if not success and failed_index is not None and index > failed_index:
                    continue
                src, dst = stable_id("Tool", a), stable_id("Tool", b)
                rows = self.hydra.query("MATCH (a {id: $src})-[r:FEEDS]->(b {id: $dst}) "
                                        "RETURN r.weight AS w", {"src": src, "dst": dst})
                if rows:
                    weight = float(rows[0][0] or 0)
                    delta = 0.2 if success else -0.4
                    updates.append({"src": src, "dst": dst, "w": max(-2.0, min(2.0, weight + delta))})
            if updates:
                self.hydra.query("UNWIND $rows AS row MATCH (a {id: row.src})-[r:FEEDS]->(b {id: row.dst}) "
                                 "SET r.weight = row.w", {"rows": updates})

    def reset_weights(self):
        if not self.gateway:
            self.hydra.query("MATCH ()-[r:FEEDS]->() SET r.weight = 0.0")
            return
        owned = {stable_id("Tool", tool["key"]) for tool in self.catalog}
        edges = self.hydra.query("MATCH (a:Tool)-[:FEEDS]->(b:Tool) RETURN a.id AS src, b.id AS dst")
        rows = [{"src": int(src), "dst": int(dst), "w": 1.0} for src, dst in edges
                if int(src) in owned and int(dst) in owned]
        for batch in chunks(rows):
            self.hydra.query("UNWIND $rows AS row MATCH (a {id: row.src})-[r:FEEDS]->(b {id: row.dst}) "
                             "SET r.weight = row.w", {"rows": batch})

    def status(self):
        """Report this discovered catalog, including persisted edge weights."""
        known = {tool["key"] for tool in self.catalog}
        nodes = self.hydra.query("MATCH (t:Tool) RETURN t.key AS key")
        rows = self.hydra.query("MATCH (a:Tool)-[r:FEEDS]->(b:Tool) "
                                "RETURN a.key AS src, b.key AS dst, r.weight AS weight")
        weights = [{"from": src, "to": dst, "weight": weight} for src, dst, weight in rows
                   if src in known and dst in known]
        plans = self.hydra.query("MATCH (p:Plan) RETURN p.payload AS payload")
        count = sum(bool(payload) and json.loads(payload).get("catalog_hash") == self.catalog_version
                    for (payload,) in plans)
        return {"healthy": True, "tools": sum(key in known for (key,) in nodes),
                "feeds": len(weights), "plans": count, "weights": weights}

    def plan_usage(self):
        return {row[0]: int(row[1]) for row in self.hydra.query(
            "MATCH (:Plan)-[:STEP]->(t:Tool) RETURN t.key AS key, count(*) AS n")}
