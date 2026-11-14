"""G0: six checks against the local HydraDB OSS stack."""

import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.store import Hydra  # noqa: E402 -- allow invocation as a standalone script

ADMIN_URL = os.getenv("HYDRADB_ADMIN_URL", "http://127.0.0.1:19090").rstrip("/")
HYDRA = Hydra()

NODES = [
    {"id": 9001, "key": "probe_a", "name": "Probe A"},
    {"id": 9002, "key": "probe_b", "name": "Probe B"},
    {"id": 9003, "key": "probe_c", "name": "Probe C"},
    {"id": 9004, "key": "probe_d", "name": "Probe D"},
]
EDGES = [
    {"src": 9001, "dst": 9002},
    {"src": 9002, "dst": 9003},
    {"src": 9003, "dst": 9004},
]
CHAIN = ["probe_a", "probe_b", "probe_c", "probe_d"]

PATH_QUERY = """CALL algo.MSpaths({
  sourceLabel: 'ProbeTool', sourceProperty: 'key', sourceValues: ['probe_a'],
  targetLabel: 'ProbeTool', targetProperty: 'key', targetValues: ['probe_d'],
  relTypes: ['PROBE_FEEDS'], relDirection: 'outgoing',
  maxLen: 4, pathCount: 10, pairwise: true, resultLimit: 50
}) YIELD path RETURN path"""


def wait_ready(timeout=60):
    deadline = time.monotonic() + timeout
    last = "no response"
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{ADMIN_URL}/readyz", timeout=2)
            if response.status_code == 200:
                return True
            last = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            last = str(exc)
        time.sleep(1)
    print(f"FAIL readyz: {last}")
    return False


def check_nodes():
    HYDRA.query(
        "UNWIND $rows AS row MERGE (n {id: row.id}) "
        "SET n:ProbeTool, n.key = row.key, n.name = row.name",
        {"rows": NODES},
    )
    count = HYDRA.query("MATCH (t:ProbeTool) RETURN count(*) AS n")[0][0]
    assert count == len(NODES), count
    return f"{count} tools"


def check_edges():
    cypher = (
        "UNWIND $rows AS row "
        "CREATE (a {id: row.src})-[:PROBE_FEEDS]->(b {id: row.dst})"
    )
    HYDRA.query(cypher, {"rows": EDGES})
    first = HYDRA.query("MATCH ()-[r:PROBE_FEEDS]->() RETURN count(*) AS n")[0][0]
    HYDRA.query(cypher, {"rows": EDGES})
    second = HYDRA.query("MATCH ()-[r:PROBE_FEEDS]->() RETURN count(*) AS n")[0][0]
    assert first == second == len(EDGES), (first, second)
    return f"{second} edges, rerun stable"


def chains():
    return [
        [node["properties"]["key"] for node in row[0]["nodes"]]
        for row in HYDRA.query(PATH_QUERY)
    ]


def check_path():
    found = chains()
    assert CHAIN in found, found
    return " -> ".join(CHAIN)


def check_steps():
    for index, node in enumerate(NODES):
        HYDRA.query(
            f"MERGE (p:ProbePlan {{id: 9900}})-[:PROBE_STEP {{i: {index}}}]"
            f"->(t:ProbeTool {{id: {node['id']}}})"
        )
    keys = [
        row[0]
        for row in HYDRA.query(
            "MATCH (p:ProbePlan {id: 9900})-[s:PROBE_STEP]->(t:ProbeTool) "
            "RETURN t.key AS k ORDER BY s.i"
        )
    ]
    assert keys == CHAIN, keys
    return " -> ".join(keys)


def check_weight():
    HYDRA.query(
        "MATCH (a:ProbeTool {id: 9001})-[r:PROBE_FEEDS]->(b:ProbeTool {id: 9002}) "
        "SET r.weight = 0.25"
    )
    value = HYDRA.query(
        "MATCH (a:ProbeTool {id: 9001})-[r:PROBE_FEEDS]->(b:ProbeTool {id: 9002}) "
        "RETURN r.weight AS w"
    )[0][0]
    assert abs(value - 0.25) < 1e-9, value
    for row in HYDRA.query(PATH_QUERY):
        path = row[0]
        keys = [node["properties"]["key"] for node in path["nodes"]]
        if keys == CHAIN:
            weight = path["relationships"][0]["properties"].get("weight")
            assert weight is not None and abs(weight - 0.25) < 1e-9, weight
            return f"weight {weight} hydrated"
    raise AssertionError("expected path not found")


def check(name, fn):
    try:
        detail = fn()
    except Exception as exc:
        print(f"FAIL {name}: {exc}")
        return False
    print(f"PASS {name}: {detail}")
    return True


def main():
    if not wait_ready():
        print("0/6 PASS")
        return 1

    print("PASS readyz: 200")
    results = [
        check("node batch", check_nodes),
        check("edge batch", check_edges),
        check("path query", check_path),
        check("plan steps", check_steps),
        check("edge weight", check_weight),
    ]
    passed = 1 + sum(results)
    print(f"{passed}/6 PASS")
    return 0 if passed == 6 else 1


if __name__ == "__main__":
    sys.exit(main())
