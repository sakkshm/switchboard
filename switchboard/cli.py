"""Command line: manage upstreams, index the graph, plan and run tasks."""

import argparse
import asyncio
import json
import sys
from collections import Counter

import requests

from . import config, gateway, index, infer, planner
from .hydra import Hydra


def fail(message):
    print(f"switchboard: {message}", file=sys.stderr)
    return 1


def pairs(values):
    result = {}
    for item in values:
        key, separator, value = item.partition("=")
        if not separator:
            raise ValueError(f"expected NAME=VALUE, got {item}")
        result[key] = value
    return result


def add_mcp(args):
    servers = config.load()["servers"]
    if "." in args.name:
        return fail("server names cannot contain a dot")
    if args.name in servers:
        return fail(f"{args.name} already exists; remove it first")
    if args.url:
        server = {"transport": "http", "url": args.url}
        if args.header:
            server["headers"] = pairs(args.header)
    else:
        command = [part for part in args.command if part != "--"]
        if not command:
            return fail("give a command after --, or pass --url")
        server = {"transport": "stdio", "command": command}
        if args.cwd:
            server["cwd"] = args.cwd
        if args.env:
            server["env"] = pairs(args.env)

    try:
        tools = index.discover({args.name: server})
    except Exception as exc:
        return fail(f"cannot reach {args.name}: {exc}")

    current = config.load()
    current["servers"][args.name] = server
    config.save(current)
    print(f"added {args.name}: {len(tools)} tools")
    for tool in tools:
        print(f"  {tool['key']}  consumes={tool['consumes']} produces={tool['produces']}")
    print("run switchboard index to write them into the graph")
    return 0


def remove_mcp(args):
    current = config.load()
    if args.name not in current["servers"]:
        return fail(f"{args.name} is not configured")
    del current["servers"][args.name]
    config.save(current)
    print(f"removed {args.name}; run switchboard index to refresh the graph")
    return 0


def list_servers(_args):
    current = config.load()
    hydra = Hydra.from_config(current)
    counts = {}
    try:
        counts = Counter(tool["server"] for tool in planner.load_tools(hydra))
    except Exception as exc:
        print(f"switchboard: graph unavailable: {exc}", file=sys.stderr)

    if not current["servers"]:
        print("no upstreams; add one with: switchboard add mcp NAME -- COMMAND")
        return 0
    for name, server in current["servers"].items():
        transport = server.get("transport", "stdio")
        target = server.get("url") if transport == "http" else " ".join(server.get("command", []))
        print(f"{name:12} {transport:6} {counts.get(name, 0):3} tools  {target}")
    return 0


def index_command(_args):
    current = config.load()
    if not current["servers"]:
        return fail("no upstreams; add one with: switchboard add mcp NAME -- COMMAND")
    try:
        tools = index.discover(current["servers"])
    except Exception as exc:
        return fail(str(exc))
    nodes, edges = index.write(Hydra.from_config(current), tools)
    typed = sum(1 for tool in tools if tool["consumes"] or tool["produces"])
    print(f"indexed {nodes} tools, {edges} FEEDS edges")
    if not typed:
        print("no tool declares consumes/produces; run switchboard annotate --infer")
    return 0


def tools_command(args):
    current = config.load()
    try:
        tools = planner.load_tools(Hydra.from_config(current))
    except Exception as exc:
        return fail(str(exc))
    if args.server:
        tools = [tool for tool in tools if tool["server"] == args.server]
    if not tools:
        return fail("no indexed tools; run switchboard index")
    for tool in sorted(tools, key=lambda item: item["key"]):
        print(f"{tool['key']:42} consumes={tool['consumes']} produces={tool['produces']}")
    print(f"{len(tools)} tools")
    return 0


def graph_command(_args):
    current = config.load()
    try:
        rows = Hydra.from_config(current).query(
            "MATCH (a:Tool)-[r:FEEDS]->(b:Tool) RETURN a.key, b.key, r.weight"
        )
    except Exception as exc:
        return fail(str(exc))
    if not rows:
        return fail("no FEEDS edges; run switchboard index")
    for source, target, weight in sorted(rows):
        print(f"{source} -> {target}  weight={1.0 if weight is None else weight}")
    print(f"{len(rows)} edges")
    return 0


def reset_command(args):
    if not args.yes:
        return fail("this deletes every tool and plan in the graph; pass --yes")
    current = config.load()
    hydra = Hydra.from_config(current)
    try:
        tools = hydra.scalar("MATCH (t:Tool) RETURN count(*) AS n")
        plans = hydra.scalar("MATCH (p:Plan) RETURN count(*) AS n")
        hydra.query("MATCH (t:Tool) DETACH DELETE t")
        hydra.query("MATCH (p:Plan) DETACH DELETE p")
    except Exception as exc:
        return fail(str(exc))
    print(f"deleted {tools} tools and {plans} plans")
    return 0


def annotate(args):
    if args.infer:
        return infer_annotate(args)
    current = config.load()
    if not args.name or not args.tool:
        return fail("usage: switchboard annotate NAME TOOL --consumes ... --produces ...")
    server = current["servers"].get(args.name)
    if server is None:
        return fail(f"{args.name} is not configured")
    try:
        names = {tool["name"] for tool in index.discover({args.name: server})}
    except Exception as exc:
        return fail(f"cannot reach {args.name}: {exc}")
    if args.tool not in names:
        return fail(f"{args.name} has no tool named {args.tool}")

    entry = server.setdefault("capabilities", {}).setdefault(args.tool, {})
    if args.consumes is not None:
        entry["consumes"] = [part.strip() for part in args.consumes.split(",") if part.strip()]
    if args.produces is not None:
        entry["produces"] = [part.strip() for part in args.produces.split(",") if part.strip()]
    if not entry.get("consumes") and not entry.get("produces"):
        server["capabilities"].pop(args.tool)
    if not server["capabilities"]:
        server.pop("capabilities")
    config.save(current)
    print(f"annotated {args.name}.{args.tool}: consumes={entry.get('consumes', [])} produces={entry.get('produces', [])}")
    print("run switchboard index to refresh the graph")
    return 0


def infer_annotate(args):
    current = config.load()
    if args.server:
        if args.server not in current["servers"]:
            return fail(f"{args.server} is not configured")
        servers = {args.server: current["servers"][args.server]}
    else:
        servers = current["servers"]
    if not servers:
        return fail("no upstreams; add one with: switchboard add mcp NAME -- COMMAND")
    try:
        tools = index.discover(servers)
    except Exception as exc:
        return fail(str(exc))

    print(f"inferring capabilities for {len(tools)} tools, one model call...")
    try:
        inferred = infer.infer(tools)
    except ValueError as exc:
        return fail(str(exc))

    for name in servers:
        current["servers"][name].pop("inferred", None)
    for key, entry in inferred.items():
        server, _, tool = key.partition(".")
        current["servers"][server].setdefault("inferred", {})[tool] = entry
    config.save(current)

    producers = {key: entry for key, entry in inferred.items() if entry["produces"]}
    print(f"inferred capabilities for {len(tools)} tools, {len(producers)} of them produce data:")
    for key in sorted(producers):
        print(f"  {key:42} produces={','.join(producers[key]['produces'])}")
    print("run switchboard index to refresh the graph")
    return 0


def source_of(reference, steps):
    """Human name of where a step's argument comes from."""
    if reference.startswith("$input."):
        return "input"
    if reference.startswith("$steps."):
        return steps[int(reference.split(".")[1])]["tool"]
    return reference


def preview(value, limit=90):
    """One short line for a step result, whatever shape it has."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def plan_command(args):
    current = config.load()
    supplied = pairs(args.input)
    try:
        result = planner.make(Hydra.from_config(current), args.task, supplied)
    except planner.NoPlan as exc:
        return fail(f"NoPlan: {exc}")
    print(f"plan {result['plan_id']}  goal {result['goal']}")
    print(f"  task: {args.task}")
    for step in result["steps"]:
        flows = ", ".join(
            f"{name} <- {source_of(reference, result['steps'])}"
            for name, reference in step["args"].items()
        )
        print(f"  {step['i']}. {step['tool']:26} {flows}")
    print(f"inputs: {', '.join(result['inputs']) or 'none'}")
    if result["missing_inputs"]:
        print(f"missing: {', '.join(result['missing_inputs'])}")
    flags = " ".join(f"--input {name}={supplied.get(name, '...')}" for name in result["inputs"])
    print(f"run: switchboard run {result['plan_id']} {flags}".rstrip())
    return 0


def run_command(args):
    current = config.load()
    hydra = Hydra.from_config(current)
    try:
        plan = planner.load(hydra, args.plan_id)
    except planner.NoPlan as exc:
        return fail(str(exc))
    steps = plan["steps"]
    print(f"plan {plan['plan_id']}  {plan['task']}", flush=True)
    result = asyncio.run(gateway.execute(hydra, plan, pairs(args.input)))
    done = result.get("results", [])
    failed = result.get("failed_step")
    for step in steps:
        if step["i"] < len(done):
            print(f"  ok   {step['i']}. {step['tool']:26} {preview(done[step['i']])}")
        elif step["i"] == failed:
            print(f"  fail {step['i']}. {step['tool']:26} {result.get('error', '')}")
        else:
            break
    if result["ok"]:
        print(f"done: {len(steps)}/{len(steps)} steps ok")
        if done:
            print(f"result: {preview(done[-1], 300)}")
        return 0
    if result.get("missing_inputs"):
        return fail(f"missing inputs: {', '.join(result['missing_inputs'])}")
    failed_tool = steps[failed]["tool"] if failed is not None else "?"
    print(f"stopped at step {failed} of {len(steps)}: {failed_tool}")
    print(f"that edge lost 0.2 weight; run `switchboard plan {json.dumps(plan['task'])}` again")
    return 1


def serve_command(args):
    current = config.load()
    hydra = Hydra.from_config(current)
    tools = planner.load_tools(hydra)
    gateway.log(f"{len(current['servers'])} upstreams, {len(tools)} indexed tools")
    server = gateway.build(hydra)
    if args.http:
        host, _, port = args.http.rpartition(":")
        server.run("streamable-http", host=host or "127.0.0.1", port=int(port))
    else:
        server.run("stdio")
    return 0


def reason(exc):
    """One short line for a failed call, without requests' multi-line pool text."""
    return " ".join(str(exc).split())[:200]


def status_command(_args):
    current = config.load()
    hydra = Hydra.from_config(current)
    # readiness is the store the gateway actually talks to, not the admin port
    try:
        tools = hydra.scalar("MATCH (t:Tool) RETURN count(*) AS n")
        edges = hydra.scalar("MATCH ()-[r:FEEDS]->() RETURN count(*) AS n")
        plans = hydra.scalar("MATCH (p:Plan) RETURN count(*) AS n")
    except Exception as exc:
        print(f"HydraDB {hydra.url}  ready=False")
        return fail(f"cannot reach the graph store: {reason(exc)}")
    print(f"HydraDB {hydra.url}  ready=True")
    print(f"servers={len(current['servers'])} tools={tools} edges={edges} plans={plans}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="switchboard")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="add an upstream MCP server")
    add_kind = add.add_subparsers(dest="kind", required=True)
    add_mcp_parser = add_kind.add_parser("mcp")
    add_mcp_parser.add_argument("name")
    add_mcp_parser.add_argument("--url")
    add_mcp_parser.add_argument("--header", action="append", default=[])
    add_mcp_parser.add_argument("--env", action="append", default=[])
    add_mcp_parser.add_argument("--cwd")
    add_mcp_parser.add_argument("command", nargs="*")
    add_mcp_parser.set_defaults(handler=add_mcp)

    remove = sub.add_parser("remove", help="remove an upstream MCP server")
    remove_kind = remove.add_subparsers(dest="kind", required=True)
    remove_mcp_parser = remove_kind.add_parser("mcp")
    remove_mcp_parser.add_argument("name")
    remove_mcp_parser.set_defaults(handler=remove_mcp)

    sub.add_parser("list").set_defaults(handler=list_servers)
    tools_parser = sub.add_parser("tools", help="list indexed tools and their types")
    tools_parser.add_argument("--server")
    tools_parser.set_defaults(handler=tools_command)
    sub.add_parser("graph", help="list FEEDS edges and learned weights").set_defaults(handler=graph_command)
    sub.add_parser("index").set_defaults(handler=index_command)
    sub.add_parser("status").set_defaults(handler=status_command)

    reset_parser = sub.add_parser("reset", help="delete every tool and plan from the graph")
    reset_parser.add_argument("--yes", action="store_true")
    reset_parser.set_defaults(handler=reset_command)

    annotate_parser = sub.add_parser("annotate", help="set or infer consumes/produces for upstream tools")
    annotate_parser.add_argument("name", nargs="?")
    annotate_parser.add_argument("tool", nargs="?")
    annotate_parser.add_argument("--consumes")
    annotate_parser.add_argument("--produces")
    annotate_parser.add_argument("--infer", action="store_true", help="let a model name produced data types")
    annotate_parser.add_argument("--server", help="limit --infer to one server")
    annotate_parser.set_defaults(handler=annotate)

    plan = sub.add_parser("plan", help="find an ordered tool plan for a task")
    plan.add_argument("task")
    plan.add_argument("--input", action="append", default=[], metavar="NAME=VALUE",
                      help="data you already have; optional, used to pick the route that fits")
    plan.set_defaults(handler=plan_command)

    run = sub.add_parser("run", help="execute a plan by id")
    run.add_argument("plan_id")
    run.add_argument("--input", action="append", default=[], metavar="NAME=VALUE",
                      help="one for each input the plan reports it needs")
    run.set_defaults(handler=run_command)

    serve = sub.add_parser("serve")
    serve.add_argument("--http")
    serve.set_defaults(handler=serve_command)

    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = None
    if "--" in argv:
        split = argv.index("--")
        argv, command = argv[:split], argv[split + 1 :]
    args = build_parser().parse_args(argv)
    if command is not None and hasattr(args, "command"):
        args.command = command
    try:
        return args.handler(args)
    except ValueError as exc:
        return fail(str(exc))
    except requests.RequestException as exc:
        return fail(f"cannot reach HydraDB: {reason(exc)}")
    except RuntimeError as exc:
        return fail(str(exc))
