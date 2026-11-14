"""Synthetic MSpaths scale benchmark in separate graph labels, never Tool/FEEDS."""
# ruff: noqa: E402

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.matcher import Matcher
from app.store import GraphStore, Hydra, chunks, stable_id
from catalog import load_catalog


def benchmark(call, repetitions):
    values = []
    for _ in range(repetitions):
        started = time.perf_counter()
        result = call()
        values.append((time.perf_counter() - started) * 1000)
        if not result:
            raise RuntimeError("The measured traversal returned no paths")
    ordered = sorted(values)
    return {"n": repetitions, "p50_ms": statistics.median(values), "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * .95))], "samples_ms": values}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=10000)
    parser.add_argument("--edges", type=int, default=100000)
    parser.add_argument("--reps", type=int, default=100)
    parser.add_argument("--output", default="results/scale.json")
    args = parser.parse_args()
    if args.nodes < 20 or not args.nodes <= args.edges <= args.nodes * (args.nodes - 1) or args.reps < 1:
        parser.error("Need at least 20 nodes, nodes <= edges <= nodes*(nodes-1), and positive repetitions")
    hydra = Hydra()
    suffix = f"{args.nodes}_{args.edges}"
    label, relation = "ScaleTool_" + suffix, "SCALE_FEEDS_" + suffix
    schema = json.dumps({"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False})
    rows = [{"id": stable_id(label, str(i)), "key": f"scale_{i}", "type": f"synthetic_type_{i % 200}",
             "name": f"scale_{i}", "desc": "Synthetic scale tool for traversal latency measurement; not a production MCP tool.",
             "schema": schema, "risk": "read", "server": "synthetic_scale", "version": "1"} for i in range(args.nodes)]
    for batch in chunks(rows):
        hydra.query(f"UNWIND $rows AS row MERGE (n {{id: row.id}}) SET n:{label}, n.key = row.key, n.type = row.type, "
                    "n.name = row.name, n.desc = row.desc, n.schema = row.schema, n.risk = row.risk, n.server = row.server, n.version = row.version", {"rows": batch})
    # Round-robin offsets produce exactly E distinct edges and no self loops.
    edges = [{"src": rows[i % args.nodes]["id"], "dst": rows[(i % args.nodes + i // args.nodes + 1) % args.nodes]["id"]}
             for i in range(args.edges)]
    for batch in chunks(edges):
        hydra.query(f"UNWIND $rows AS row CREATE (a {{id: row.src}})-[:{relation}]->(b {{id: row.dst}})", {"rows": batch})
    node_count = int(hydra.query(f"MATCH (n:{label}) RETURN count(*) AS n")[0][0])
    edge_count = int(hydra.query(f"MATCH ()-[r:{relation}]->() RETURN count(*) AS n")[0][0])
    if (node_count, edge_count) != (args.nodes, args.edges):
        raise RuntimeError(f"Scale seed count mismatch: nodes={node_count}, edges={edge_count}")
    catalog = load_catalog()
    store = GraphStore(catalog, hydra=hydra)
    match = Matcher(catalog).match("comment on the failing test issue in acme/api")
    baseline = benchmark(lambda: store.paths(match["candidates"], match["goal"]), args.reps)
    query = (f"CALL algo.MSpaths({{sourceLabel: '{label}', sourceProperty: 'key', "
             "sourceValues: ['scale_0','scale_1','scale_2','scale_3','scale_4','scale_5','scale_6','scale_7','scale_8','scale_9'], "
             f"targetLabel: '{label}', targetProperty: 'key', targetValues: ['scale_13'], "
             f"relTypes: ['{relation}'], relDirection: 'outgoing', maxLen: 6, pathCount: 10, "
             "pairwise: false, resultLimit: 50}) YIELD path RETURN path")
    scale = benchmark(lambda: hydra.query(query), args.reps)
    result = {"synthetic": True, "backend": "HydraDB OSS", "nodes": node_count, "edges": edge_count,
              "average_out_degree": edge_count / node_count, "label": label, "relationship": relation,
              "baseline_catalog_size": len(catalog), "baseline": baseline, "scale": scale,
              "p95_ratio": scale["p95_ms"] / baseline["p95_ms"],
              "notes": "Dedicated labels persist for repeatability. No fixture nodes, grants, plans or edges were changed."}
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"baseline", "scale"}}, indent=2))
    print(f"60-tool p95={baseline['p95_ms']:.2f} ms; synthetic p95={scale['p95_ms']:.2f} ms")


if __name__ == "__main__":
    main()
