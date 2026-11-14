"""Measured HydraDB evals; never substitute a local graph for unavailable OSS.

Run after scripts/seed.py. Resets fixture edge weights, executes synthetic writes,
and leaves the final learned weights in the graph. Holdout never updates weights.
"""
# ruff: noqa: E402

import argparse
import asyncio
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.executor import Executor, catalog_hash
from app.matcher import Matcher
from app.metrics import token_count, tool_context
from app.planner import Planner
from app.store import GraphStore
from app.validate import validate_order
from catalog import load_catalog


class EvaluationInfrastructureError(RuntimeError):
    """Database/audit failures invalidate a measured eval run."""


def environment_info():
    return {"client_platform": platform.platform(), "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
            "timing_context": "Shared development host; wall times are not a dedicated hardware benchmark."}


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * fraction))]


def context_tokens(catalog, chain):
    """Use the same exact cl100k_base schema serialization as the live demo."""
    tools = {tool["key"]: tool for tool in catalog}
    return token_count(tool_context([tools[key] for key in chain]))


def baseline_plan(task, matcher, catalog, allowed):
    # Fixed four-step budget, same BM25 candidate pool as the graph planner.
    keys = [item["key"] for item in matcher.rank(task["task"], allowed=allowed, limit=10)][:4]
    tools = {tool["key"]: tool for tool in catalog}
    intent_hash = hashlib.sha256(("bm25:" + task["task"]).encode()).hexdigest()
    return {"key": "baseline_" + intent_hash[:16], "intent_hash": intent_hash,
            "intent": task["task"], "steps": [{"i": i, "tool": key, "args": tools[key]["args"]} for i, key in enumerate(keys)],
            "catalog_hash": catalog_hash(catalog), "graph_queries": 0, "reused": False}


async def measure(task, variant, planner, executor, matcher, catalog, store, repetition):
    started = time.perf_counter()
    error = None
    plan = None
    trace = None
    chain = []
    graph_queries = 0
    try:
        if variant == "bm25":
            plan = baseline_plan(task, matcher, catalog, store.grants("demo"))
        else:
            plan = planner.plan(task["task"], principal="demo", inputs=task["expected_values"], reuse=False)
        chain = [step["tool"] for step in plan["steps"]]
        graph_queries = plan.get("graph_queries", 0)
        plan_ms = (time.perf_counter() - started) * 1000
        valid = bool(chain) and validate_order(chain, catalog) is None
        if valid:
            confirmation = executor.confirmations.issue(plan["key"], "demo") if task.get("confirmation_token") else None
            trace = await executor.execute(plan, task["expected_values"], principal="demo", confirmation_token=confirmation)
            error = trace.get("error")
            if (trace.get("audit_error") or trace.get("learning_error")
                    or (error and trace.get("steps") and all(step["status"] == "success" for step in trace["steps"]))):
                raise EvaluationInfrastructureError(f"Persistence failed during {task['id']}: {error}")
        else:
            error = {"code": "invalid_order", "message": "Consumed types are not supplied by preceding tools"}
    except (requests.RequestException, EvaluationInfrastructureError):
        raise
    except RuntimeError as exc:
        raise EvaluationInfrastructureError(f"Graph backend failed during {task['id']}: {exc}") from exc
    except (ValueError, TypeError, KeyError, OSError) as exc:
        plan_ms = (time.perf_counter() - started) * 1000
        valid = False
        error = {"code": type(exc).__name__, "message": str(exc)[:500]}
    expected = set(task["expected_chain"])
    actual = set(chain)
    end_state = trace.get("result", {}) if trace else {}
    success = bool(trace and trace.get("status") == "success" and isinstance(end_state, dict)
                   and all(end_state.get(key) == value for key, value in task["expected_end_state"].items()))
    return {"task_id": task["id"], "family": task["family"], "split": task["split"],
            "variant": variant, "repetition": repetition, "chain": chain,
            "precision": len(actual & expected) / len(actual) if actual else 0,
            "recall": len(actual & expected) / len(expected), "order_validity": float(valid),
            "top1_accuracy": float(chain == task["expected_chain"]), "task_success": float(success),
            "tokens": context_tokens(catalog, chain), "graph_queries": graph_queries,
            "latency_ms": round(plan_ms, 3), "wall_ms": round((time.perf_counter() - started) * 1000, 3),
            "tool_calls": trace["metrics"]["tool_calls"] if trace else 0, "error": error}


def summarize(records):
    grouped = defaultdict(list)
    for record in records:
        grouped[(record["family"], record["variant"], record["split"])].append(record)
    summaries = []
    for (family, variant, split), group in sorted(grouped.items()):
        row = {"family": family, "variant": variant, "split": split,
               "n": len({item["task_id"] for item in group}), "observations": len(group)}
        for metric in ("precision", "recall", "order_validity", "top1_accuracy", "task_success", "tokens", "graph_queries"):
            values = [item[metric] for item in group if item[metric] is not None]
            row[metric] = statistics.mean(values) if values else None
        timings = [item["latency_ms"] for item in group]
        row["latency_ms"] = {"median": statistics.median(timings), "min": min(timings), "max": max(timings), "p95": percentile(timings, .95)}
        summaries.append(row)
    return summaries


def git_info():
    revision = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip() or "unknown"
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip())
    return revision, dirty


async def run(arguments):
    catalog = load_catalog()
    store = GraphStore(catalog)
    tasks = [json.loads(line) for line in (ROOT / "evals/tasks.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    matcher = Matcher(catalog)
    # Fail before producing an apparently valid report when the backend is absent.
    granted = store.grants("demo")
    if not granted:
        raise RuntimeError("No demo grants in HydraDB; start the OSS stack and run scripts/seed.py")
    store.reset_weights()
    records = []
    learning = []
    variants = ["bm25"] if arguments.baseline else ["bm25", "static"]
    for variant in variants:
        planner = Planner(store, catalog, learning=False)
        executor = Executor(store, catalog, rate_limit=100_000, learning=False)
        for repetition in range(arguments.reps):
            for task in tasks:
                record = await measure(task, variant, planner, executor, matcher, catalog, store, repetition)
                records.append(record)
            print(f"{variant}: repetition {repetition + 1}/{arguments.reps} complete", flush=True)
    if not arguments.baseline:
        train = [task for task in tasks if task["split"] == "train"]
        holdout = [task for task in tasks if task["split"] == "holdout"]
        store.reset_weights()
        planner = Planner(store, catalog, learning=True)
        train_executor = Executor(store, catalog, rate_limit=100_000, learning=True)
        eval_executor = Executor(store, catalog, rate_limit=100_000, learning=False)
        for round_index in range(arguments.rounds + 1):
            if round_index:
                for task in train:
                    await measure(task, "learning", planner, train_executor, matcher, catalog, store, round_index)
            holdout_records = []
            for repetition in range(arguments.reps):
                for task in holdout:
                    holdout_records.append(await measure(task, "learning", planner, eval_executor, matcher, catalog, store, repetition))
            point = {"round": round_index, "n": len(holdout), "reps": arguments.reps,
                     "top1_accuracy": statistics.mean(item["top1_accuracy"] for item in holdout_records),
                     "task_success": statistics.mean(item["task_success"] for item in holdout_records),
                     "order_validity": statistics.mean(item["order_validity"] for item in holdout_records)}
            learning.append(point)
            print(f"learning round {round_index}: held-out top1={point['top1_accuracy']:.3f}, success={point['task_success']:.3f}", flush=True)
            if round_index == arguments.rounds:
                records.extend(holdout_records)
    revision, dirty = await asyncio.to_thread(git_info)
    result = {"schema_version": 1, "created_at": datetime.now(UTC).isoformat(), "revision": revision,
              "dirty_worktree": dirty, "catalog_hash": catalog_hash(catalog), "backend": "HydraDB OSS", "environment": environment_info(),
              "transport": "MCP stdio, synthetic fixture", "n": len(tasks), "reps": arguments.reps,
              "rounds": arguments.rounds if not arguments.baseline else 0,
              "notes": ["All tools and tasks are synthetic; writes append local JSONL records.",
                        "Train split: 16 tasks; holdout: 8 tasks. Holdout never updates weights.",
                        "Tasks in a family share tools: this evaluates paraphrase generalization, not unseen-tool generalization.",
                        "BM25 has a fixed four-step budget and the same candidate pool; invalid orders do not execute.",
                        "No improvement is assumed; flat or lower learning scores are reported as measured.",
                        "Graph query counts measure plan traversals; grants, audits and persistence are additional database calls."],
              "full_catalog_tokens": context_tokens(catalog, [tool["key"] for tool in catalog]),
              "families": summarize(records), "learning": learning, "measurements": records}
    destination = Path(arguments.output) if arguments.output else ROOT / "evals/results" / f"{revision}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("family     variant    split    order   success top1    p95_ms")
    for row in result["families"]:
        print(f"{row['family']:10} {row['variant']:10} {row['split']:8} {row['order_validity']:.3f}   {row['task_success']:.3f}   {row['top1_accuracy']:.3f}   {row['latency_ms']['p95']:.1f}")
    print(f"Wrote {destination}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--baseline", choices=["bm25"])
    parser.add_argument("--output")
    arguments = parser.parse_args()
    if arguments.reps < 1 or arguments.rounds < 1:
        parser.error("reps and rounds must be positive")
    try:
        asyncio.run(run(arguments))
    except (RuntimeError, OSError, requests.RequestException) as exc:
        parser.exit(1, f"Evaluation failed; no result report written: {exc}\n")


if __name__ == "__main__":
    main()
