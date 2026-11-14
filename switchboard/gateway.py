"""The two-tool MCP surface: `find` returns a plan, `run` executes it."""

import sys
from itertools import pairwise

from mcp.server.mcpserver import MCPServer

from . import binder, config, planner, validator
from .executor import call
from .hydra import node_id


def log(message):
    print(f"switchboard: {message}", file=sys.stderr, flush=True)


def _update_weights(hydra, steps, delta):
    for first, second in pairwise(steps):
        source, target = node_id(first["tool"]), node_id(second["tool"])
        current = hydra.scalar(
            "MATCH (a {id: $src})-[r:FEEDS]->(b {id: $dst}) RETURN r.weight AS w",
            {"src": source, "dst": target},
        )
        weight = round(max(0.1, min(2.0, (current or 1.0) + delta)), 3)
        hydra.query(
            "MATCH (a {id: $src})-[r:FEEDS]->(b {id: $dst}) SET r.weight = $w",
            {"src": source, "dst": target, "w": weight},
        )


async def execute(hydra, plan, inputs):
    tools = {tool["key"]: tool for tool in planner.load_tools(hydra)}
    servers = config.load()["servers"]
    error = validator.check(plan["steps"], tools)
    if error:
        return {"plan_id": plan["plan_id"], "ok": False, "error": error, "results": []}

    required = {
        name
        for step in plan["steps"]
        for name, reference in step["args"].items()
        if reference.startswith("$input.")
    }
    missing = sorted(required - set(inputs))
    if missing:
        return {"plan_id": plan["plan_id"], "ok": False, "missing_inputs": missing, "results": []}

    results = []
    for step in plan["steps"]:
        tool = tools[step["tool"]]
        server = servers.get(tool["server"])
        if server is None:
            return {
                "plan_id": plan["plan_id"],
                "ok": False,
                "failed_step": step["i"],
                "error": f"upstream {tool['server']} is not configured",
                "results": results,
            }
        arguments = binder.resolve_args(step["args"], inputs, results)
        log(f"step {step['i']} {step['tool']} args={arguments}")
        try:
            results.append(await call(server, tool["name"], arguments))
        except Exception as exc:
            _update_weights(hydra, plan["steps"][: len(results) + 1], -0.2)
            return {
                "plan_id": plan["plan_id"],
                "ok": False,
                "failed_step": step["i"],
                "error": str(exc),
                "results": results,
            }

    _update_weights(hydra, plan["steps"], 0.2)
    return {"plan_id": plan["plan_id"], "ok": True, "results": results}


def build(hydra):
    server = MCPServer(
        "switchboard",
        instructions=(
            "Switchboard turns a task into an ordered tool plan. "
            "Call find with the task, then run with the plan_id and any missing inputs."
        ),
    )

    @server.tool()
    def find(task: str, inputs: dict[str, str] | None = None) -> dict:
        """Find an ordered tool plan for a task. Returns plan_id and numbered steps."""
        plan = planner.make(hydra, task, inputs or {})
        log(f"plan {plan['plan_id']}: " + " -> ".join(step["tool"] for step in plan["steps"]))
        return plan

    @server.tool(name="run")
    async def run_plan(plan_id: str, inputs: dict[str, str] | None = None) -> dict:
        """Run a plan by id. Returns one result per step, or the failing step."""
        plan = planner.load(hydra, plan_id)
        result = await execute(hydra, plan, inputs or {})
        log(f"run {plan_id}: ok={result['ok']}")
        return result

    return server
