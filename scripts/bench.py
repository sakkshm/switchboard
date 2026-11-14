"""Real planner, golden-set and 20-concurrent-request benchmark."""
# ruff: noqa: E402

import argparse
import asyncio
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.planner import Planner
from app.store import GraphStore
from catalog import load_catalog
from evals.run import environment_info, git_info
from evals.run import run as run_evals


def timings(values):
    values = sorted(values)
    return {"n": len(values), "p50_ms": statistics.median(values),
            "p95_ms": values[min(len(values) - 1, int(len(values) * .95))],
            "p99_ms": values[min(len(values) - 1, int(len(values) * .99))],
            "min_ms": min(values), "max_ms": max(values)}


def load_request(url, payload):
    started = time.perf_counter()
    error = None
    status = None
    reused = None
    graph_queries = None
    try:
        response = requests.post(url + "/compare", json=payload, timeout=120)
        status = response.status_code
        response.raise_for_status()
        body = response.json()
        reused = body.get("switchboard", {}).get("reused")
        graph_queries = body.get("switchboard", {}).get("graph_queries")
        trace = body.get("trace", body.get("switchboard", {}).get("trace", {}))
        if trace.get("status") != "success":
            error = trace.get("error", body.get("detail", "Response did not contain a successful trace"))
    except (requests.RequestException, ValueError) as exc:
        error = str(exc)
    return {"wall_ms": (time.perf_counter() - started) * 1000, "http_status": status, "error": error,
            "plan_reused": reused, "graph_queries": graph_queries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--url", default="http://127.0.0.1:18000")
    parser.add_argument("--skip-evals", action="store_true", help="Skip the golden run for a shorter diagnostic benchmark")
    parser.add_argument("--output")
    args = parser.parse_args()
    if min(args.iterations, args.concurrency, args.reps, args.rounds) < 1:
        parser.error("all counts must be positive")
    task = json.loads((ROOT / "evals/tasks.jsonl").read_text(encoding="utf-8").splitlines()[0])
    catalog = load_catalog()
    planner = Planner(GraphStore(catalog), catalog, learning=False)
    samples = []
    for index in range(args.iterations):
        started = time.perf_counter()
        planner.plan(task["task"], inputs=task["expected_values"], reuse=False)
        samples.append((time.perf_counter() - started) * 1000)
        if (index + 1) % 100 == 0:
            print(f"Planner: {index + 1}/{args.iterations}", flush=True)
    evaluation = None
    if not args.skip_evals:
        evaluation = asyncio.run(run_evals(argparse.Namespace(reps=args.reps, rounds=args.rounds, baseline=None, output=None)))
    payload = {"task": task["task"], "inputs": task["expected_values"], "principal": "demo"}
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        load = list(pool.map(lambda _: load_request(args.url.rstrip("/"), payload), range(args.concurrency)))
    errors = sum(item["error"] is not None for item in load)
    revision, dirty = git_info()
    result = {"backend": "HydraDB OSS", "environment": environment_info(), "synthetic_fixture": True, "revision": revision,
              "dirty_worktree": dirty, "planner": timings(samples), "planner_samples_ms": samples,
              "load": {**timings([item["wall_ms"] for item in load]), "concurrent_requests": args.concurrency,
                       "errors": errors, "error_rate": errors / len(load), "requests": load},
              "golden": {key: evaluation[key] for key in ("n", "reps", "families", "learning")} if evaluation else None}
    destination = Path(args.output) if args.output else ROOT / "results" / f"bench-{revision}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"planner p95={result['planner']['p95_ms']:.2f}ms; load p95={result['load']['p95_ms']:.2f}ms; error_rate={errors/len(load):.1%}")
    print(f"Wrote {destination}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
