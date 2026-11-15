"""Graph planner: BM25 goal, one MSpaths call, bindings from produced types."""

import json
import random
import time

from . import binder, matcher, validator
from .hydra import node_id

FIELDS = [
    "id",
    "key",
    "name",
    "server",
    "description",
    "consumes",
    "produces",
    "input_schema",
    "output_schema",
]


class NoPlan(Exception):
    pass


def load_tools(hydra):
    projection = ", ".join(f"t.{field} AS {field}" for field in FIELDS)
    rows = hydra.query(f"MATCH (t:Tool) RETURN {projection}")
    return [dict(zip(FIELDS, row, strict=False)) for row in rows]


def _literal(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def fetch_paths(hydra, sources, goal):
    # pairwise: true drops every source whose internal id is not below the
    # target's, so a many-source lookup must opt out of pair dedupe.
    source_values = ", ".join(_literal(source) for source in sources)
    query = f"""CALL algo.MSpaths({{
  sourceLabel: 'Tool', sourceProperty: 'key', sourceValues: [{source_values}],
  targetLabel: 'Tool', targetProperty: 'key', targetValues: [{_literal(goal)}],
  relTypes: ['FEEDS'], relDirection: 'outgoing',
  maxLen: 6, pathCount: 20, pairwise: false, resultLimit: 50
}}) YIELD path RETURN path"""
    return [row[0] for row in hydra.query(query)]


def components(path, candidates, uniform=False):
    keys = [node["properties"]["key"] for node in path["nodes"]]
    if uniform:
        weights = [1.0] * len(path["relationships"])
    else:
        weights = [relationship["properties"].get("weight") or 1.0 for relationship in path["relationships"]]
    coverage = len(set(keys) & set(candidates))
    return coverage, round(sum(weights) / len(weights), 3), len(keys)


def choose(paths, candidates, uniform=False):
    """Lexical shortlist, then learned rerank: BM25 picks plausible routes,
    edge weights break ties, longer plans win before path order."""
    scored = [(components(path, candidates, uniform), path) for path in paths]
    best = max(coverage for (coverage, _, _), _ in scored)
    shortlist = [
        (weight, length, coverage, path)
        for (coverage, weight, length), path in scored
        if coverage >= best - 1
    ]
    return max(shortlist, key=lambda item: (item[0], item[1], item[2]))[3]


def save(hydra, plan_id, task, steps):
    hydra.query(
        "UNWIND $rows AS row MERGE (p {id: row.id}) "
        "SET p:Plan, p.key = row.key, p.task = row.task, p.steps = row.steps, "
        "p.ts = row.ts, p.wins = row.wins",
        {
            "rows": [
                {
                    "id": plan_id,
                    "key": str(plan_id),
                    "task": task,
                    "steps": json.dumps(steps),
                    "ts": int(time.time()),
                    "wins": 0,
                }
            ]
        },
    )
    for step in steps:
        # top-level MERGE with no following clause is the only supported shape
        hydra.query(
            f"MERGE (p:Plan {{id: {plan_id}}})-[r:STEP {{i: {step['i']}}}]"
            f"->(t:Tool {{id: {node_id(step['tool'])}}})"
        )


def make(hydra, task, supplied=None, uniform=False):
    supplied = supplied or {}
    tools = {tool["key"]: tool for tool in load_tools(hydra)}
    if not tools:
        raise NoPlan("the tool graph is empty; run switchboard index")

    ranked = matcher.rank(task, list(tools.values()))
    goal = ranked[0]["key"]
    candidates = [tool["key"] for tool in ranked[:20] if tool["key"] != goal]
    sources = [key for key in tools if key != goal]
    if not sources:
        raise NoPlan("the graph has only one tool")

    paths = fetch_paths(hydra, sources, goal)
    if not paths:
        raise NoPlan(f"no FEEDS path to {goal}")

    path = choose(paths, candidates, uniform)
    chain = [node["properties"]["key"] for node in path["nodes"]]
    steps, required = binder.bind(tools, chain)
    error = validator.check(steps, tools)
    if error:
        raise NoPlan(error)

    plan_id = random.getrandbits(48)
    save(hydra, plan_id, task, steps)
    return {
        "plan_id": str(plan_id),
        "task": task,
        "goal": goal,
        "steps": steps,
        "inputs": required,
        "missing_inputs": [name for name in required if name not in supplied],
    }


def load(hydra, plan_id):
    rows = hydra.query(
        "MATCH (p:Plan {id: $id}) RETURN p.task AS task, p.steps AS steps",
        {"id": int(plan_id)},
    )
    if not rows:
        raise NoPlan(f"no plan {plan_id}")
    task, steps = rows[0]
    return {"plan_id": str(plan_id), "task": task, "steps": json.loads(steps)}


def record(hydra, plan_id, wins):
    hydra.query(
        "MATCH (p:Plan {id: $id}) SET p.wins = $wins",
        {"id": int(plan_id), "wins": wins},
    )
