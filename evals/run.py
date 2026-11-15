"""G5: BM25 vs uniform graph vs learned graph, 5 training rounds, holdout.

Drives the planner and executor directly so the numbers are deterministic.
The learned mode updates FEEDS weights after every train task; holdout runs
never update weights.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from switchboard import binder, config, gateway, matcher, planner  # noqa: E402
from switchboard.hydra import Hydra  # noqa: E402

TASKS = ROOT / "evals" / "tasks.jsonl"
RESULTS = ROOT / "evals" / "results"


def load_tasks():
    return [json.loads(line) for line in TASKS.read_text().splitlines() if line.strip()]


def reset(hydra):
    hydra.query("MATCH ()-[r:FEEDS]->() SET r.weight = 1.0")
    hydra.query("MATCH (p:Plan) DETACH DELETE p")


def order_valid(steps, tools, input_types):
    produced = set(input_types)
    for step in steps:
        spec = tools[step["tool"]]
        if not set(spec["consumes"]) <= produced:
            return False
        produced |= set(spec["produces"])
    return True


def dependency_order(chosen, inputs):
    """Order tools so each runs after the tools that produce its inputs."""
    remaining, ordered, produced = list(chosen), [], set(inputs)
    while remaining:
        ready = [tool for tool in remaining if set(tool["consumes"]) <= produced]
        tool = ready[0] if ready else remaining[0]  # cycle: keep discovery order
        ordered.append(tool)
        produced |= set(tool["produces"])
        remaining.remove(tool)
    return [tool["key"] for tool in ordered]


def bm25_plan(task, tools, inputs):
    """Fair lexical baseline: BM25 picks the goal and the best-ranked producer
    for each missing input type, then the chain is ordered by data dependency.
    No graph traversal, no learned weights."""
    ranked = matcher.rank(task["task"], list(tools.values()))
    chosen, available = [ranked[0]], set(inputs)
    for _ in range(len(ranked)):
        missing = {name for tool in chosen for name in tool["consumes"]} - available
        if not missing:
            break
        picked = next(
            (
                tool
                for tool in ranked
                if tool["key"] not in {item["key"] for item in chosen}
                and set(tool["produces"]) & missing
            ),
            None,
        )
        if picked is None:
            break
        chosen.append(picked)
        available |= set(picked["produces"])
    chain = dependency_order(chosen, inputs)
    steps, _ = binder.bind(tools, chain)
    return {"plan_id": "bm25", "task": task["task"], "steps": steps}


def bm25_raw_plan(task, tools):
    """Relevance-ordered top-4, with no dependency awareness at all."""
    chain = [tool["key"] for tool in matcher.rank(task["task"], list(tools.values()))[:4]]
    steps, _ = binder.bind(tools, chain)
    return {"plan_id": "bm25", "task": task["task"], "steps": steps}


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))
    return ordered[index]


async def observe(hydra, task, tools, plan, learn):
    chain = [step["tool"] for step in plan["steps"]]
    started = time.perf_counter()
    result = await gateway.execute(hydra, plan, task["inputs"], learn=learn)
    latency = (time.perf_counter() - started) * 1000
    expected = set(task["expected"])
    got = set(chain)
    return {
        "id": task["id"],
        "family": task["family"],
        "chain": chain,
        "ok": bool(result.get("ok")),
        "order_valid": order_valid(plan["steps"], tools, set(task["inputs"])),
        "top1": chain == task["expected"],
        "success": bool(result.get("ok")) and bool(chain) and chain[-1] == task["goal"],
        "precision": len(expected & got) / len(got) if got else 0.0,
        "recall": len(expected & got) / len(expected),
        "latency_ms": latency,
    }


async def measure(hydra, tasks, tools, uniform, learn):
    observations = []
    for task in tasks:
        try:
            plan = planner.make(hydra, task["task"], task["inputs"], uniform=uniform)
        except planner.NoPlan as exc:
            observations.append(
                {
                    "id": task["id"],
                    "family": task["family"],
                    "chain": [],
                    "ok": False,
                    "order_valid": False,
                    "top1": False,
                    "success": False,
                    "precision": 0.0,
                    "recall": 0.0,
                    "latency_ms": 0.0,
                    "error": str(exc),
                }
            )
            continue
        observations.append(await observe(hydra, task, tools, plan, learn))
    return observations


async def measure_bm25(hydra, tasks, tools):
    runs = {"bm25": [], "bm25_raw": []}
    for task in tasks:
        inputs = set(task["inputs"])
        runs["bm25"].append(await observe(hydra, task, tools, bm25_plan(task, tools, inputs), learn=False))
        runs["bm25_raw"].append(await observe(hydra, task, tools, bm25_raw_plan(task, tools), learn=False))
    return {name: summarize(items) for name, items in runs.items()}


async def train_round(hydra, tasks, tools, uniform):
    for task in tasks:
        try:
            plan = planner.make(hydra, task["task"], task["inputs"], uniform=uniform)
        except planner.NoPlan:
            continue
        await gateway.execute(hydra, plan, task["inputs"], learn=True)


def summarize(observations):
    if not observations:
        return {}
    latencies = [item["latency_ms"] for item in observations]
    return {
        "n": len(observations),
        "top1": statistics.mean(item["top1"] for item in observations),
        "order_validity": statistics.mean(item["order_valid"] for item in observations),
        "task_success": statistics.mean(item["success"] for item in observations),
        "precision": statistics.mean(item["precision"] for item in observations),
        "recall": statistics.mean(item["recall"] for item in observations),
        "latency_median_ms": statistics.median(latencies),
        "latency_p95_ms": percentile(latencies, 0.95),
        "failures": [
            {"id": item["id"], "chain": item["chain"]}
            for item in observations
            if not item["success"]
        ],
    }


def median_of(summaries, key):
    values = [summary[key] for summary in summaries if key in summary]
    return statistics.median(values) if values else 0.0


def spread_of(summaries, key):
    values = [summary[key] for summary in summaries if key in summary]
    return [min(values), max(values)] if values else [0.0, 0.0]


def aggregate(summaries):
    keys = ["top1", "order_validity", "task_success", "precision", "recall"]
    return {
        "n": summaries[0]["n"] if summaries else 0,
        **{key: median_of(summaries, key) for key in keys},
        "spread": {key: spread_of(summaries, key) for key in keys},
        "latency_median_ms": median_of(summaries, "latency_median_ms"),
        "latency_p95_ms": median_of(summaries, "latency_p95_ms"),
        "failures": sorted({failure["id"] for summary in summaries for failure in summary.get("failures", [])}),
    }


async def experiment(reps, rounds):
    hydra = Hydra.from_config(config.load())
    tools = {tool["key"]: tool for tool in planner.load_tools(hydra)}
    tasks = load_tasks()
    train = [task for task in tasks if task["split"] == "train"]
    holdout = [task for task in tasks if task["split"] == "holdout"]

    bm25_runs, bm25_raw_runs, uniform_runs, train_runs = [], [], [], []
    curves = []
    for _ in range(reps):
        reset(hydra)
        measured = await measure_bm25(hydra, tasks, tools)
        bm25_runs.append(measured["bm25"])
        bm25_raw_runs.append(measured["bm25_raw"])
        uniform_runs.append(summarize(await measure(hydra, holdout, tools, uniform=True, learn=False)))
        train_runs.append(
            summarize(await measure(hydra, train, tools, uniform=True, learn=False))
        )

        reset(hydra)
        curve = [summarize(await measure(hydra, holdout, tools, uniform=False, learn=False))]
        for _round in range(rounds):
            await train_round(hydra, train, tools, uniform=False)
            curve.append(summarize(await measure(hydra, holdout, tools, uniform=False, learn=False)))
        curves.append(curve)

    final_curve = [
        {"round": index, **aggregate([curve[index] for curve in curves])}
        for index in range(rounds + 1)
    ]
    return {
        "n": len(tasks),
        "train": len(train),
        "holdout": len(holdout),
        "reps": reps,
        "rounds": rounds,
        "bm25": aggregate(bm25_runs),
        "bm25_raw": aggregate(bm25_raw_runs),
        "uniform": {
            "holdout": aggregate(uniform_runs),
            "train": aggregate(train_runs),
        },
        "learned": final_curve,
    }


def print_table(report):
    print(f"tasks={report['n']} train={report['train']} holdout={report['holdout']} "
          f"reps={report['reps']} rounds={report['rounds']}")

    def line(name, split, row):
        failed = f" failed={','.join(row.get('failures', []))}" if row.get("failures") else ""
        print(
            f"{name:9} {split:8} n={row['n']:<3}"
            f" top1={row['top1']:.2f} order={row['order_validity']:.2f}"
            f" success={row['task_success']:.2f} precision={row['precision']:.2f}"
            f" recall={row['recall']:.2f} p95={row['latency_p95_ms']:.0f}ms{failed}"
        )

    line("bm25", "all", report["bm25"])
    line("bm25-raw", "all", report["bm25_raw"])
    line("uniform", "train", report["uniform"]["train"])
    line("uniform", "holdout", report["uniform"]["holdout"])
    for row in report["learned"]:
        line("learned", f"round {row['round']}", row)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--out", default=str(RESULTS / "latest.json"))
    args = parser.parse_args()

    report = await experiment(args.reps, args.rounds)
    report["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print_table(report)

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n")
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamped = RESULTS / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    stamped.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
